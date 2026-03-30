#!/usr/bin/env python3
"""Cypher 图查询模块 - 支持原生 Cypher 查询执行。

本模块提供：
- Cypher 查询执行
- 查询结果格式化
- 查询模板管理
- 查询历史记录

使用示例:
    # 执行 Cypher 查询
    result = await execute_cypher("MATCH (n) RETURN n LIMIT 10")
    
    # 使用预定义模板
    result = await query_by_template("find_connected_nodes", {"node_id": "123"})
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class CypherQueryResult:
    """Cypher 查询结果。"""
    query: str
    records: list[dict[str, Any]]
    columns: list[str]
    execution_time_ms: int = 0
    record_count: int = 0
    error: Optional[str] = None


@dataclass
class CypherQueryTemplate:
    """Cypher 查询模板。"""
    name: str
    description: str
    query_template: str
    parameters: list[str]
    example_params: Optional[dict[str, Any]] = None


class CypherQueryExecutor:
    """Cypher 查询执行器。"""
    
    # 预定义的常用查询模板
    DEFAULT_TEMPLATES: dict[str, CypherQueryTemplate] = {
        "find_all_nodes": CypherQueryTemplate(
            name="find_all_nodes",
            description="查找所有节点",
            query_template="MATCH (n) RETURN n LIMIT {limit}",
            parameters=["limit"],
            example_params={"limit": 100}
        ),
        "find_node_by_id": CypherQueryTemplate(
            name="find_node_by_id",
            description="根据 ID 查找节点",
            query_template="MATCH (n) WHERE id(n) = {node_id} RETURN n",
            parameters=["node_id"],
            example_params={"node_id": 123}
        ),
        "find_connected_nodes": CypherQueryTemplate(
            name="find_connected_nodes",
            description="查找与指定节点相连的节点",
            query_template="MATCH (n)-[r]-(m) WHERE id(n) = {node_id} RETURN n, r, m LIMIT {limit}",
            parameters=["node_id", "limit"],
            example_params={"node_id": 123, "limit": 50}
        ),
        "find_nodes_by_label": CypherQueryTemplate(
            name="find_nodes_by_label",
            description="根据标签查找节点",
            query_template="MATCH (n:{label}) RETURN n LIMIT {limit}",
            parameters=["label", "limit"],
            example_params={"label": "Person", "limit": 100}
        ),
        "find_shortest_path": CypherQueryTemplate(
            name="find_shortest_path",
            description="查找两个节点之间的最短路径",
            query_template="MATCH path = shortestPath((a)-[*]-(b)) WHERE id(a) = {start_id} AND id(b) = {end_id} RETURN path",
            parameters=["start_id", "end_id"],
            example_params={"start_id": 1, "end_id": 10}
        ),
        "count_nodes_by_label": CypherQueryTemplate(
            name="count_nodes_by_label",
            description="统计各标签的节点数量",
            query_template="MATCH (n) RETURN labels(n) as label, count(n) as count ORDER BY count DESC",
            parameters=[],
        ),
        "find_nodes_with_property": CypherQueryTemplate(
            name="find_nodes_with_property",
            description="根据属性查找节点",
            query_template="MATCH (n) WHERE n.{property_name} = '{property_value}' RETURN n LIMIT {limit}",
            parameters=["property_name", "property_value", "limit"],
            example_params={"property_name": "name", "property_value": "John", "limit": 10}
        ),
        "get_node_relationships": CypherQueryTemplate(
            name="get_node_relationships",
            description="获取节点的所有关系",
            query_template="MATCH (n)-[r]-(m) WHERE id(n) = {node_id} RETURN type(r) as relationship, count(r) as count",
            parameters=["node_id"],
            example_params={"node_id": 123}
        ),
    }
    
    def __init__(self):
        self._cognee_available = self._check_cognee()
        self.templates = dict(self.DEFAULT_TEMPLATES)
        self._history: list[dict[str, Any]] = []
    
    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        try:
            import cognee
            return True
        except ImportError:
            return False
    
    async def execute(
        self,
        query: str,
        parameters: Optional[dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> CypherQueryResult:
        """
        执行 Cypher 查询。
        
        Args:
            query: Cypher 查询语句
            parameters: 查询参数
            timeout_ms: 超时时间（毫秒）
            
        Returns:
            查询结果
        """
        import time
        
        start_time = time.time()
        
        if not self._cognee_available:
            return CypherQueryResult(
                query=query,
                records=[],
                columns=[],
                error="Cognee not installed"
            )
        
        try:
            # 使用 Cognee 的 Cypher 搜索
            import cognee
            from cognee.modules.search.types import SearchType
            
            # Cognee 的 Cypher 搜索实际上是通过 search API 实现的
            results = await cognee.search(
                query_type=SearchType.CYPHER,
                query_text=query,
            )
            
            execution_time = int((time.time() - start_time) * 1000)
            
            # 格式化结果
            formatted_results = self._format_results(results)
            
            # 记录历史
            self._record_history(query, parameters, execution_time, len(formatted_results))
            
            return CypherQueryResult(
                query=query,
                records=formatted_results,
                columns=self._extract_columns(formatted_results),
                execution_time_ms=execution_time,
                record_count=len(formatted_results),
            )
            
        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            return CypherQueryResult(
                query=query,
                records=[],
                columns=[],
                execution_time_ms=execution_time,
                error=str(e)
            )
    
    def execute_template(
        self,
        template_name: str,
        parameters: dict[str, Any],
    ) -> CypherQueryResult:
        """
        使用预定义模板执行查询。
        
        Args:
            template_name: 模板名称
            parameters: 模板参数
            
        Returns:
            查询结果
        """
        template = self.templates.get(template_name)
        if not template:
            return CypherQueryResult(
                query="",
                records=[],
                columns=[],
                error=f"Template '{template_name}' not found"
            )
        
        # 替换参数
        query = template.query_template
        for param in template.parameters:
            value = parameters.get(param)
            if value is None:
                # 使用示例参数
                value = template.example_params.get(param) if template.example_params else ""
            query = query.replace(f"{{{param}}}", str(value))
        
        # 异步执行
        import asyncio
        return asyncio.run(self.execute(query, parameters))
    
    def _format_results(self, results: Any) -> list[dict[str, Any]]:
        """格式化 Cypher 查询结果。"""
        if not results:
            return []
        
        formatted = []
        
        if isinstance(results, list):
            for record in results:
                if isinstance(record, dict):
                    formatted.append(record)
                else:
                    formatted.append({"value": str(record)})
        elif isinstance(results, dict):
            formatted.append(results)
        else:
            formatted.append({"value": str(results)})
        
        return formatted
    
    def _extract_columns(self, records: list[dict[str, Any]]) -> list[str]:
        """提取列名。"""
        if not records:
            return []
        
        columns = set()
        for record in records:
            if isinstance(record, dict):
                columns.update(record.keys())
        
        return sorted(list(columns))
    
    def _record_history(
        self,
        query: str,
        parameters: Optional[dict[str, Any]],
        execution_time: int,
        result_count: int,
    ) -> None:
        """记录查询历史。"""
        self._history.append({
            "timestamp": datetime.now().isoformat(),
            "query": query[:200],  # 截断长查询
            "parameters": parameters,
            "execution_time_ms": execution_time,
            "result_count": result_count,
        })
        
        # 只保留最近 100 条
        if len(self._history) > 100:
            self._history = self._history[-100:]
    
    def get_history(
        self,
        limit: int = 20,
        query_filter: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        获取查询历史。
        
        Args:
            limit: 返回记录数量
            query_filter: 可选的查询过滤
            
        Returns:
            历史记录列表
        """
        history = self._history
        
        if query_filter:
            history = [h for h in history if query_filter.lower() in h["query"].lower()]
        
        return history[-limit:]
    
    def add_template(self, template: CypherQueryTemplate) -> bool:
        """
        添加自定义查询模板。
        
        Args:
            template: 查询模板
            
        Returns:
            是否成功添加
        """
        self.templates[template.name] = template
        return True
    
    def list_templates(self) -> list[dict[str, Any]]:
        """
        列出所有可用模板。
        
        Returns:
            模板信息列表
        """
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "example_params": t.example_params,
                "query_preview": t.query_template[:100] + "..." if len(t.query_template) > 100 else t.query_template,
            }
            for t in self.templates.values()
        ]
    
    def validate_query(self, query: str) -> tuple[bool, Optional[str]]:
        """
        验证 Cypher 查询语法。
        
        Args:
            query: Cypher 查询语句
            
        Returns:
            (是否有效, 错误信息)
        """
        if not query or not query.strip():
            return False, "Query is empty"
        
        # 基础语法检查
        query_upper = query.upper().strip()
        
        # 检查是否以允许的语句开头
        allowed_starts = ["MATCH", "RETURN", "CALL", "UNWIND", "WITH", "OPTIONAL"]
        if not any(query_upper.startswith(s) for s in allowed_starts):
            return False, f"Query must start with one of: {', '.join(allowed_starts)}"
        
        # 检查基本语法结构
        open_parens = query.count("(")
        close_parens = query.count(")")
        if open_parens != close_parens:
            return False, f"Mismatched parentheses: {open_parens} open, {close_parens} close"
        
        open_braces = query.count("{")
        close_braces = query.count("}")
        if open_braces != close_braces:
            return False, f"Mismatched braces: {open_braces} open, {close_braces} close"
        
        open_brackets = query.count("[")
        close_brackets = query.count("]")
        if open_brackets != close_brackets:
            return False, f"Mismatched brackets: {open_brackets} open, {close_brackets} close"
        
        # 检查危险操作
        dangerous_keywords = ["DELETE", "REMOVE", "SET", "CREATE", "MERGE", "DROP"]
        for keyword in dangerous_keywords:
            if re.search(rf'\b{keyword}\b', query_upper):
                return False, f"Dangerous operation detected: {keyword}. Use read-only queries."
        
        return True, None
    
    def export_results(
        self,
        result: CypherQueryResult,
        format: str = "json",
        output_path: Optional[Path] = None,
    ) -> Path:
        """
        导出查询结果。
        
        Args:
            result: 查询结果
            format: 导出格式 (json, csv)
            output_path: 输出路径
            
        Returns:
            输出文件路径
        """
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = Path(f"cypher_results_{timestamp}.{format}")
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if format == "json":
            data = {
                "query": result.query,
                "execution_time_ms": result.execution_time_ms,
                "record_count": result.record_count,
                "columns": result.columns,
                "records": result.records,
            }
            output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        
        elif format == "csv":
            import csv
            
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                if result.columns:
                    writer = csv.DictWriter(f, fieldnames=result.columns)
                    writer.writeheader()
                    for record in result.records:
                        writer.writerow(record)
        
        return output_path


# 便捷函数
async def execute_cypher(query: str, parameters: Optional[dict[str, Any]] = None) -> CypherQueryResult:
    """便捷函数：执行 Cypher 查询。
    
    Args:
        query: Cypher 查询语句
        parameters: 查询参数
        
    Returns:
        查询结果
    """
    executor = CypherQueryExecutor()
    return await executor.execute(query, parameters)


def list_cypher_templates() -> list[dict[str, Any]]:
    """便捷函数：列出所有 Cypher 模板。
    
    Returns:
        模板列表
    """
    executor = CypherQueryExecutor()
    return executor.list_templates()


def validate_cypher_query(query: str) -> tuple[bool, Optional[str]]:
    """便捷函数：验证 Cypher 查询。
    
    Args:
        query: Cypher 查询语句
        
    Returns:
        (是否有效, 错误信息)
    """
    executor = CypherQueryExecutor()
    return executor.validate_query(query)
