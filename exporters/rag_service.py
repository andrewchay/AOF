#!/usr/bin/env python3
"""RAG 统一检索服务 - 多路召回 + 命中溯源.

对齐 exporters/okf_service.py 的共享服务模式，供 REST API 与 MCP Server 复用：

- **多路召回**：关键词（lexical）+ 向量（vector）+ 图谱路径（graph）三路并行
- **混合检索**：RRF 融合 + 4 层去重（复用 bridge/hybrid_search）
- **命中溯源**：每条结果携带 provenance（来源路 / 数据集 / 种子实体 / 图谱路径 / 关系）

使用示例:
    from exporters.rag_service import rag_retrieve
    result = await rag_retrieve(
        query="Ganyu 与 Qixing 的关系",
        dataset_name="genshin_ultimate_kg",
    )
    # result["results"][0]["provenance"] 包含完整溯源链路
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RagResult:
    """RAG 检索统一结果."""
    query: str
    results: list[dict[str, Any]] = field(default_factory=list)
    route_counts: dict[str, int] = field(default_factory=dict)
    execution_time_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "route_counts": self.route_counts,
            "execution_time_ms": self.execution_time_ms,
            "count": len(self.results),
            "results": self.results,
            "error": self.error,
        }


def _chunk_to_retrieval(chunk: Any, limit_body: int = 600) -> dict[str, Any]:
    """把 HybridChunkResult 序列化为带溯源的结构化检索项."""
    meta = getattr(chunk, "metadata", {}) or {}
    prov = getattr(chunk, "provenance", {}) or {}
    text = getattr(chunk, "chunk_text", "") or ""
    return {
        "text": text[:limit_body],
        "type": getattr(chunk, "type", "unknown"),
        "score": round(float(getattr(chunk, "score", 0.0) or 0.0), 4),
        "source": getattr(chunk, "source", "unknown"),
        "slug": getattr(chunk, "slug", ""),
        "metadata": {k: v for k, v in meta.items() if not isinstance(v, (dict, list))},
        "provenance": prov,
    }


async def rag_retrieve(
    query: str,
    dataset_id: Optional[str] = None,
    dataset_name: Optional[str] = None,
    limit: int = 10,
    expansion: bool = False,
    include_graph: bool = True,
    cognee_root: Optional[str] = None,
) -> RagResult:
    """统一 RAG 检索：多路召回 + 混合检索 + 命中溯源.

    Args:
        query: 检索查询
        dataset_id: 数据集 ID（可选）
        dataset_name: 数据集名称（可选）
        limit: 返回结果数
        expansion: 是否开启查询扩展
        include_graph: 是否包含图谱路径召回路
        cognee_root: cognee 安装根目录（可选）

    Returns:
        RagResult（含 route_counts 与每条结果的 provenance）
    """
    from bridge.hybrid_search import AOFHybridSearch

    engine = AOFHybridSearch(cognee_root=cognee_root)
    result = await engine.hybrid_search(
        query=query,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        limit=limit,
        expansion=expansion,
        include_graph=include_graph,
    )

    results = [_chunk_to_retrieval(r) for r in result.results]
    return RagResult(
        query=result.query,
        results=results,
        route_counts={
            "keyword": result.keyword_count,
            "vector": result.vector_count,
            "graph": result.graph_count,
        },
        execution_time_ms=result.execution_time_ms,
    )
