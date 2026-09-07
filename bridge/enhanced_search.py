#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""增强语义搜索模块 - 利用 Cognee 全部搜索能力。

本模块封装 Cognee 的多种搜索类型，提供：
- 统一的搜索接口
- 基于意图的智能搜索类型选择
- 本体指导的检索增强
- 搜索结果后处理

使用示例:
    search = EnhancedSemanticSearch()
    results = await search.search(
        query="查询过去30天活跃用户",
        context=SearchContext(query_source="api", user_intent="analytics"),
        ontology_guidance=my_ontology,
    )
"""

from __future__ import annotations

import enum
import importlib.util
from dataclasses import dataclass, field
from typing import Any, Optional


class SearchType(str, enum.Enum):
    """Cognee 支持的搜索类型。"""
    
    # 基础搜索（AOF 当前已支持）
    GRAPH_COMPLETION = "GRAPH_COMPLETION"
    RAG_COMPLETION = "RAG_COMPLETION"
    CHUNKS = "CHUNKS"
    
    # 高级搜索（未充分利用）
    GRAPH_COMPLETION_COT = "GRAPH_COMPLETION_COT"  # Chain-of-Thought 推理
    GRAPH_COMPLETION_CONTEXT_EXTENSION = "GRAPH_COMPLETION_CONTEXT_EXTENSION"  # 上下文扩展
    GRAPH_SUMMARY_COMPLETION = "GRAPH_SUMMARY_COMPLETION"  # 基于摘要的完成
    TEMPORAL = "TEMPORAL"  # 时序感知搜索
    CODING_RULES = "CODING_RULES"  # 代码规则搜索
    FEELING_LUCKY = "FEELING_LUCKY"  # 智能选择最佳类型
    CYPHER = "CYPHER"  # 原生 Cypher 查询
    
    # 其他类型
    SUMMARIES = "SUMMARIES"
    TRIPLET_COMPLETION = "TRIPLET_COMPLETION"
    NATURAL_LANGUAGE = "NATURAL_LANGUAGE"
    CHUNKS_LEXICAL = "CHUNKS_LEXICAL"


@dataclass
class SearchContext:
    """搜索上下文信息。"""
    query_source: str = "api"  # api, cli, ui
    user_intent: Optional[str] = None  # analytics, debugging, exploration, etc.
    previous_queries: list[str] = field(default_factory=list)
    session_id: Optional[str] = None
    dataset_filter: Optional[list[str]] = None
    top_k: int = 10


@dataclass
class OntologyConstraint:
    """本体约束条件。"""
    target_classes: list[str] = field(default_factory=list)
    target_properties: list[str] = field(default_factory=list)
    exclude_classes: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """搜索结果。"""
    query: str
    search_type_used: SearchType
    results: list[Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    execution_time_ms: int = 0


class SearchIntentClassifier:
    """查询意图分类器。"""
    
    # 意图关键词映射
    INTENT_PATTERNS = {
        "analytics": ["统计", "计算", "平均", "总和", "趋势", "分析", "metric", "statistics", "calculate", "average", "trend"],
        "debugging": ["错误", "问题", "为什么", "失败", "debug", "error", "issue", "why", "fail"],
        "exploration": ["有哪些", "列表", "所有", "探索", "what are", "list", "all", "explore"],
        "comparison": ["对比", "比较", "区别", "compare", "versus", "vs", "difference"],
        "temporal": ["时间", "历史", "过去", "最近", "期间", "time", "history", "past", "recent", "period", "last month", "last week"],
        "code_related": ["代码", "函数", "类", "方法", "code", "function", "class", "method", "implementation"],
        "reasoning": ["如何", "为什么", "解释", "推理", "how to", "why", "explain", "reason"],
    }
    
    def classify(self, query: str) -> dict[str, float]:
        """对查询进行意图分类，返回各意图的置信度分数。"""
        query_lower = query.lower()
        scores = {}
        
        for intent, keywords in self.INTENT_PATTERNS.items():
            score = sum(1 for kw in keywords if kw.lower() in query_lower)
            scores[intent] = score / max(len(keywords), 1)
        
        # 归一化
        total = sum(scores.values())
        if total > 0:
            scores = {k: v / total for k, v in scores.items()}
        
        return scores
    
    def get_primary_intent(self, query: str) -> tuple[str, float]:
        """获取主要意图及其置信度。"""
        scores = self.classify(query)
        if not scores:
            return ("general", 0.0)
        
        primary = max(scores.items(), key=lambda x: x[1])
        return primary


class EnhancedSemanticSearch:
    """增强语义搜索引擎。"""
    
    def __init__(self):
        self._cognee_available = self._check_cognee()
        self._intent_classifier = SearchIntentClassifier()
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        return importlib.util.find_spec("cognee") is not None
    
    def select_search_type(
        self,
        query: str,
        context: SearchContext,
    ) -> SearchType:
        """基于查询意图和上下文选择最佳搜索类型。
        
        选择策略:
        1. 如果用户明确指定了搜索类型，使用指定类型
        2. 基于意图分类选择最合适的类型
        3. 根据上下文调整（如调试场景使用 CODE 类型）
        """
        # 检查上下文是否有预设类型
        if context.user_intent:
            intent_to_type = {
                "analytics": SearchType.GRAPH_COMPLETION,
                "debugging": SearchType.CODING_RULES,
                "exploration": SearchType.GRAPH_SUMMARY_COMPLETION,
                "comparison": SearchType.GRAPH_COMPLETION_COT,  # 需要推理
                "temporal": SearchType.TEMPORAL,
                "code_related": SearchType.CODING_RULES,
                "reasoning": SearchType.GRAPH_COMPLETION_COT,
            }
            if context.user_intent in intent_to_type:
                return intent_to_type[context.user_intent]
        
        # 基于查询内容自动分类
        intent_scores = self._intent_classifier.classify(query)
        
        # 选择最高分的意图对应的搜索类型
        if intent_scores.get("temporal", 0) > 0.3:
            return SearchType.TEMPORAL
        elif intent_scores.get("reasoning", 0) > 0.3:
            return SearchType.GRAPH_COMPLETION_COT
        elif intent_scores.get("code_related", 0) > 0.3:
            return SearchType.CODING_RULES
        elif intent_scores.get("analytics", 0) > 0.3:
            return SearchType.GRAPH_COMPLETION
        elif intent_scores.get("exploration", 0) > 0.3:
            return SearchType.GRAPH_SUMMARY_COMPLETION
        
        # 默认使用标准 GRAPH_COMPLETION
        return SearchType.GRAPH_COMPLETION
    
    async def search(
        self,
        query: str,
        context: Optional[SearchContext] = None,
        ontology_guidance: Optional[Any] = None,
        search_type: Optional[SearchType] = None,
    ) -> SearchResult:
        """执行增强搜索。
        
        Args:
            query: 搜索查询
            context: 搜索上下文
            ontology_guidance: 本体指导（可选）
            search_type: 指定搜索类型（可选，自动选择）
            
        Returns:
            搜索结果
        """
        import time
        
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        
        context = context or SearchContext()
        
        # 选择搜索类型
        selected_type = search_type or self.select_search_type(query, context)
        
        start_time = time.time()
        
        # 准备搜索参数
        search_params = self._build_search_params(
            query=query,
            search_type=selected_type,
            context=context,
            ontology_guidance=ontology_guidance,
        )
        
        # 执行搜索
        raw_results = await self._execute_cognee_search(search_params)
        
        # 后处理结果
        processed_results = self._post_process_results(
            raw_results,
            selected_type,
            ontology_guidance,
        )
        
        execution_time = int((time.time() - start_time) * 1000)
        
        return SearchResult(
            query=query,
            search_type_used=selected_type,
            results=processed_results,
            metadata={
                "total_hits": len(processed_results),
                "params": search_params,
            },
            execution_time_ms=execution_time,
        )
    
    def _build_search_params(
        self,
        query: str,
        search_type: SearchType,
        context: SearchContext,
        ontology_guidance: Optional[Any],
    ) -> dict[str, Any]:
        """构建 Cognee 搜索参数。"""
        params: dict[str, Any] = {
            "query_text": query,
            "query_type": search_type.value,
            "top_k": context.top_k,
        }
        
        if context.dataset_filter:
            params["datasets"] = context.dataset_filter
        
        if context.session_id:
            params["session_id"] = context.session_id
        
        # 如果有本体指导，添加到参数中
        if ontology_guidance:
            params["ontology_filter"] = self._convert_ontology_to_filter(ontology_guidance)
        
        return params
    
    def _convert_ontology_to_filter(
        self,
        ontology_guidance: Any,
    ) -> Optional[dict]:
        """将本体指导转换为 Cognee 过滤器。"""
        if isinstance(ontology_guidance, OntologyConstraint):
            return {
                "include_classes": ontology_guidance.target_classes,
                "exclude_classes": ontology_guidance.exclude_classes,
                "include_properties": ontology_guidance.target_properties,
            }
        return None
    
    async def _execute_cognee_search(self, params: dict[str, Any]) -> list[Any]:
        """调用 Cognee 执行搜索。"""
        import cognee
        from cognee.modules.search.types import SearchType as CogneeSearchType
        
        # 转换搜索类型
        search_type_value = params.pop("query_type")
        cognee_search_type = CogneeSearchType(search_type_value)
        
        # 执行搜索
        results = await cognee.search(
            query_type=cognee_search_type,
            query_text=params["query_text"],
            top_k=params.get("top_k", 10),
            datasets=params.get("datasets"),
        )
        
        return results if isinstance(results, list) else [results]
    
    def _post_process_results(
        self,
        results: list[Any],
        search_type: SearchType,
        ontology_guidance: Optional[Any],
    ) -> list[Any]:
        """对搜索结果进行后处理。"""
        if not results:
            return []
        
        processed = []
        
        for result in results:
            # 根据搜索类型进行特定处理
            if search_type == SearchType.CODE:
                processed.append(self._format_code_result(result))
            elif search_type == SearchType.CYPHER:
                processed.append(self._format_cypher_result(result))
            else:
                processed.append(self._format_general_result(result))
        
        return processed
    
    def _format_code_result(self, result: Any) -> dict[str, Any]:
        """格式化代码搜索结果。"""
        return {
            "type": "code",
            "content": str(result),
            "metadata": {
                "language": self._detect_code_language(str(result)),
            },
        }
    
    def _format_cypher_result(self, result: Any) -> dict[str, Any]:
        """格式化 Cypher 查询结果。"""
        return {
            "type": "cypher_result",
            "data": result,
        }
    
    def _format_general_result(self, result: Any) -> dict[str, Any]:
        """格式化一般搜索结果。"""
        if isinstance(result, dict):
            return result
        return {
            "type": "text",
            "content": str(result),
        }
    
    def _detect_code_language(self, code: str) -> str:
        """简单检测代码语言。"""
        code_lower = code.lower()
        if "def " in code or "class " in code or "import " in code:
            return "python"
        elif "function" in code or "const " in code or "let " in code:
            return "javascript"
        elif "select " in code and "from " in code_lower:
            return "sql"
        return "unknown"


class MultiSearchStrategy:
    """多策略搜索 - 并行执行多种搜索并融合结果。"""
    
    def __init__(self, search_engine: EnhancedSemanticSearch):
        self.search_engine = search_engine
    
    async def parallel_search(
        self,
        query: str,
        strategies: list[SearchType],
        context: Optional[SearchContext] = None,
    ) -> dict[str, SearchResult]:
        """并行执行多种搜索策略。
        
        适用于需要对比不同搜索方式结果的场景。
        """
        import asyncio
        
        tasks = [
            self.search_engine.search(
                query=query,
                context=context,
                search_type=st,
            )
            for st in strategies
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return {
            st.value: result
            for st, result in zip(strategies, results)
            if not isinstance(result, Exception)
        }
    
    async def fusion_search(
        self,
        query: str,
        context: Optional[SearchContext] = None,
    ) -> SearchResult:
        """融合搜索 - 智能组合多种策略的结果。
        
        选择最佳结果的融合策略。
        """
        # 执行多种策略
        strategies = [
            SearchType.GRAPH_COMPLETION,
            SearchType.RAG_COMPLETION,
            SearchType.SUMMARIES,
        ]
        
        results_map = await self.parallel_search(query, strategies, context)
        
        # 简单的融合策略：选择结果最丰富的
        best_result = max(
            results_map.values(),
            key=lambda r: len(r.results) if r.results else 0,
        )
        
        return best_result


# 便捷函数
async def search_with_intent(
    query: str,
    user_intent: Optional[str] = None,
    top_k: int = 10,
) -> SearchResult:
    """基于意图的便捷搜索函数。
    
    Args:
        query: 搜索查询
        user_intent: 用户意图 (analytics, debugging, exploration, etc.)
        top_k: 返回结果数量
        
    Returns:
        搜索结果
    """
    search_engine = EnhancedSemanticSearch()
    context = SearchContext(
        user_intent=user_intent,
        top_k=top_k,
    )
    return await search_engine.search(query, context=context)
