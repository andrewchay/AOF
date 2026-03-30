#!/usr/bin/env python3
"""Memify 反馈闭环模块 - 利用 Cognee Memify 实现本体优化闭环。

本模块提供：
- 用户交互数据收集和存储
- 反馈分析与本体改进建议生成
- 基于反馈权重的图谱优化
- 自动反馈候选生成

工作流程:
    User Query → Search → User Feedback → Memify Analysis → Ontology Update Suggestions

使用示例:
    feedback_loop = MemifyFeedbackLoop()
    
    # 捕获交互
    await feedback_loop.capture_interaction(
        query="查询活跃用户",
        search_results=results,
        feedback_score=4,
        used_graph_elements={"node_ids": [...], "edge_ids": [...]}
    )
    
    # 分析并生成本体改进建议
    suggestions = await feedback_loop.analyze_and_improve_ontology(
        session_ids=["session_1", "session_2"],
        min_feedback_score=3
    )
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class FeedbackItem:
    """单个反馈项。"""
    query: str
    feedback_score: int  # 1-5
    feedback_text: Optional[str]
    used_graph_elements: dict[str, Any]  # {node_ids: [...], edge_ids: [...]}
    timestamp: datetime
    session_id: Optional[str] = None
    qa_id: Optional[str] = None


@dataclass
class OntologyImprovementSuggestion:
    """本体改进建议。"""
    suggestion_type: str  # "add_class", "add_property", "add_relation", "modify_weight"
    target: str  # 目标类/属性名
    reason: str
    confidence: float  # 0-1
    source_feedback: list[str] = field(default_factory=list)  # 相关的反馈 ID


@dataclass
class FeedbackAnalysisResult:
    """反馈分析结果。"""
    total_feedback_items: int
    low_score_items: list[FeedbackItem] = field(default_factory=list)
    missing_elements: list[str] = field(default_factory=list)
    improvement_suggestions: list[OntologyImprovementSuggestion] = field(default_factory=list)
    confidence_score: float = 0.0


class MemifyFeedbackLoop:
    """Memify 反馈闭环处理器。"""
    
    def __init__(self, feedback_storage_path: Optional[Path] = None):
        self._cognee_available = self._check_cognee()
        self.feedback_storage = feedback_storage_path or Path("logs/feedback")
        self.feedback_storage.mkdir(parents=True, exist_ok=True)
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        try:
            import cognee
            return True
        except ImportError:
            return False
    
    async def capture_interaction(
        self,
        query: str,
        search_results: list[Any],
        feedback_score: int,
        used_graph_elements: dict[str, list[str]],
        feedback_text: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """
        捕获用户交互并保存到 Cognee 会话存储。
        
        Args:
            query: 用户查询
            search_results: 搜索结果
            feedback_score: 用户评分 (1-5)
            used_graph_elements: 使用的图谱元素 {node_ids: [...], edge_ids: [...]}
            feedback_text: 用户反馈文本（可选）
            session_id: 会话 ID（可选）
            
        Returns:
            反馈记录 ID
        """
        # 生成本地反馈记录
        feedback_id = f"fb_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hash(query) % 10000}"
        
        feedback_record = {
            "feedback_id": feedback_id,
            "query": query,
            "feedback_score": feedback_score,
            "feedback_text": feedback_text,
            "used_graph_elements": used_graph_elements,
            "search_results_summary": self._summarize_results(search_results),
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
        }
        
        # 保存到本地存储
        feedback_file = self.feedback_storage / f"feedback_{session_id or 'default'}.jsonl"
        with open(feedback_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(feedback_record, ensure_ascii=False) + '\n')
        
        # 如果 Cognee 可用，也保存到 Cognee
        if self._cognee_available:
            try:
                await self._save_to_cognee_memify(feedback_record)
            except Exception as e:
                # Cognee 保存失败不影响本地记录
                print(f"Warning: Failed to save to Cognee memify: {e}")
        
        return feedback_id
    
    async def _save_to_cognee_memify(self, feedback_record: dict[str, Any]) -> None:
        """保存反馈到 Cognee Memify。"""
        # Note: Cognee 的 memify API 可能会变化，这里提供基本框架
        try:
            from cognee.modules.memify import save_interaction
            await save_interaction(
                data=json.dumps(feedback_record),
                session_id=feedback_record.get("session_id"),
            )
        except ImportError:
            # Memify 模块可能不可用
            pass
    
    def _summarize_results(self, results: list[Any]) -> dict[str, Any]:
        """汇总搜索结果。"""
        return {
            "result_count": len(results),
            "result_types": list(set(type(r).__name__ for r in results)),
        }
    
    async def analyze_and_improve_ontology(
        self,
        session_ids: Optional[list[str]] = None,
        min_feedback_score: int = 3,
        max_suggestions: int = 10,
    ) -> FeedbackAnalysisResult:
        """
        分析用户反馈并生成本体改进建议。
        
        Args:
            session_ids: 要分析的会话 ID 列表（None 表示所有）
            min_feedback_score: 评分阈值，低于此值的反馈会被重点关注
            max_suggestions: 最大建议数量
            
        Returns:
            反馈分析结果，包含改进建议
        """
        # 加载反馈数据
        feedback_items = await self._load_feedback_items(session_ids)
        
        if not feedback_items:
            return FeedbackAnalysisResult(total_feedback_items=0)
        
        # 筛选低分反馈
        low_score_items = [
            item for item in feedback_items
            if item.feedback_score < min_feedback_score
        ]
        
        # 分析缺失的本体元素
        missing_elements = self._identify_missing_elements(low_score_items)
        
        # 生成改进建议
        suggestions = self._generate_improvement_suggestions(
            low_score_items,
            missing_elements,
            max_suggestions,
        )
        
        # 计算置信度
        confidence = self._calculate_confidence(feedback_items, suggestions)
        
        return FeedbackAnalysisResult(
            total_feedback_items=len(feedback_items),
            low_score_items=low_score_items,
            missing_elements=missing_elements,
            improvement_suggestions=suggestions,
            confidence_score=confidence,
        )
    
    async def _load_feedback_items(
        self,
        session_ids: Optional[list[str]] = None,
    ) -> list[FeedbackItem]:
        """加载反馈项。"""
        items = []
        
        if session_ids:
            # 加载指定会话的反馈
            for sid in session_ids:
                feedback_file = self.feedback_storage / f"feedback_{sid}.jsonl"
                if feedback_file.exists():
                    items.extend(self._parse_feedback_file(feedback_file))
        else:
            # 加载所有反馈
            for feedback_file in self.feedback_storage.glob("feedback_*.jsonl"):
                items.extend(self._parse_feedback_file(feedback_file))
        
        return items
    
    def _parse_feedback_file(self, file_path: Path) -> list[FeedbackItem]:
        """解析反馈文件。"""
        items = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    data = json.loads(line)
                    items.append(FeedbackItem(
                        query=data.get("query", ""),
                        feedback_score=data.get("feedback_score", 0),
                        feedback_text=data.get("feedback_text"),
                        used_graph_elements=data.get("used_graph_elements", {}),
                        timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
                        session_id=data.get("session_id"),
                        qa_id=data.get("feedback_id"),
                    ))
        except Exception as e:
            print(f"Warning: Failed to parse feedback file {file_path}: {e}")
        
        return items
    
    def _identify_missing_elements(self, low_score_items: list[FeedbackItem]) -> list[str]:
        """从低分反馈中识别可能缺失的本体元素。"""
        missing = []
        
        for item in low_score_items:
            # 分析查询中的术语
            query_terms = self._extract_terms_from_query(item.query)
            
            # 检查这些术语是否在已使用的图谱元素中
            used_nodes = set(item.used_graph_elements.get("node_ids", []))
            
            # 简单的启发式：查询中提到但未在结果中使用的概念可能是缺失的
            for term in query_terms:
                # 这里应该有更复杂的匹配逻辑
                if not any(term.lower() in node.lower() for node in used_nodes):
                    missing.append(term)
        
        # 去重并返回
        return list(set(missing))
    
    def _extract_terms_from_query(self, query: str) -> list[str]:
        """从查询中提取术语。"""
        # 简单的提取：驼峰命名、引号内的词、大写短语
        import re
        
        terms = []
        
        # 引号内的词
        quoted = re.findall(r'["\']([^"\']+)["\']', query)
        terms.extend(quoted)
        
        # 驼峰命名
        camel_case = re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', query)
        terms.extend(camel_case)
        
        # 大写短语（可能是类名）
        caps = re.findall(r'\b[A-Z]{2,}\b', query)
        terms.extend(caps)
        
        return [t for t in terms if len(t) > 2]
    
    def _generate_improvement_suggestions(
        self,
        low_score_items: list[FeedbackItem],
        missing_elements: list[str],
        max_suggestions: int,
    ) -> list[OntologyImprovementSuggestion]:
        """生成本体改进建议。"""
        suggestions = []
        
        # 为每个缺失的元素生成建议
        for element in missing_elements[:max_suggestions]:
            # 确定建议类型
            if any(kw in element.lower() for kw in ["relation", "relates", "connects"]):
                suggestion_type = "add_relation"
            elif any(kw in element.lower() for kw in ["property", "has", "is"]):
                suggestion_type = "add_property"
            else:
                suggestion_type = "add_class"
            
            # 找出相关的反馈
            related_feedback = [
                item.qa_id for item in low_score_items
                if element.lower() in item.query.lower()
            ]
            
            suggestion = OntologyImprovementSuggestion(
                suggestion_type=suggestion_type,
                target=element,
                reason=f"User query mentioned '{element}' but it was not found in search results, suggesting missing ontology element",
                confidence=min(0.7 + len(related_feedback) * 0.1, 0.95),
                source_feedback=related_feedback,
            )
            suggestions.append(suggestion)
        
        return suggestions
    
    def _calculate_confidence(
        self,
        feedback_items: list[FeedbackItem],
        suggestions: list[OntologyImprovementSuggestion],
    ) -> float:
        """计算整体置信度分数。"""
        if not feedback_items or not suggestions:
            return 0.0
        
        # 基于反馈数量和平均建议置信度计算
        avg_suggestion_confidence = sum(s.confidence for s in suggestions) / len(suggestions)
        feedback_volume_score = min(len(feedback_items) / 10, 1.0)  # 最多 10 个反馈得满分
        
        return (avg_suggestion_confidence * 0.6 + feedback_volume_score * 0.4)
    
    def export_suggestions_to_feedback_jsonl(
        self,
        suggestions: list[OntologyImprovementSuggestion],
        output_file: Path,
    ) -> None:
        """将建议导出为 feedback_candidates.jsonl 格式。"""
        records = []
        
        for suggestion in suggestions:
            record = {
                "action": suggestion.suggestion_type,
                "target": suggestion.target,
                "reason": suggestion.reason,
                "confidence": suggestion.confidence,
                "source_feedback": suggestion.source_feedback,
            }
            
            # 映射到 ontology_factory 的 feedback 格式
            if suggestion.suggestion_type == "add_class":
                record["action"] = "add_class"
                record["name"] = suggestion.target
                record["parent"] = "Thing"
            elif suggestion.suggestion_type == "add_property":
                record["action"] = "add_property"
                record["name"] = suggestion.target
            elif suggestion.suggestion_type == "add_relation":
                record["action"] = "add_relation"
                record["name"] = suggestion.target
            
            records.append(record)
        
        # 写入文件
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')


# 便捷函数
async def analyze_search_feedback(
    feedback_dir: Path = Path("logs/feedback"),
    min_score: int = 3,
    output_file: Optional[Path] = None,
) -> FeedbackAnalysisResult:
    """便捷函数：分析搜索反馈并生成改进建议。
    
    Args:
        feedback_dir: 反馈文件目录
        min_score: 最低评分阈值
        output_file: 输出文件路径（可选）
        
    Returns:
        分析结果
    """
    feedback_loop = MemifyFeedbackLoop(feedback_dir)
    result = await feedback_loop.analyze_and_improve_ontology(
        min_feedback_score=min_score,
    )
    
    if output_file and result.improvement_suggestions:
        feedback_loop.export_suggestions_to_feedback_jsonl(
            result.improvement_suggestions,
            output_file,
        )
    
    return result
