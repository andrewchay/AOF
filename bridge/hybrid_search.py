#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""混合搜索模块 - 将 GBrain 的 RRF + 4 层去重引入 AOF.

本模块提供：
- 多查询扩展（基于 Anthropic Claude）
- 向量搜索 + 关键词搜索并行执行
- RRF（Reciprocal Rank Fusion）融合
- 4 层去重（按来源、文本相似度、类型多样性、单页上限）

使用示例:
    engine = AOFHybridSearch()
    results = await engine.hybrid_search(
        query="Garry Tan 和 YC 的关系",
        dataset_name="main_dataset",
        limit=10,
        expansion=True,
    )
"""

from __future__ import annotations

import asyncio
import enum
import importlib.util
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants (ported from GBrain)
# ---------------------------------------------------------------------------
RRF_K = 60
COSINE_DEDUP_THRESHOLD = 0.85
MAX_TYPE_RATIO = 0.6
MAX_PER_PAGE = 2
MAX_QUERIES = 3
MIN_WORDS_FOR_EXPANSION = 3


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
class SearchType(str, enum.Enum):
    """Cognee 搜索类型（局部引用，避免循环导入）."""
    RAG_COMPLETION = "RAG_COMPLETION"
    CHUNKS_LEXICAL = "CHUNKS_LEXICAL"
    CHUNKS = "CHUNKS"
    GRAPH_COMPLETION = "GRAPH_COMPLETION"


@dataclass
class HybridChunkResult:
    """统一的搜索结果项，兼容 GBrain 的 dedup 逻辑."""
    slug: str
    chunk_text: str
    type: str = "unknown"
    score: float = 0.0
    source: str = "unknown"  # 'keyword' | 'vector' | 'graph' | 'rrf'
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)  # 命中溯源链路


@dataclass
class HybridSearchResult:
    """混合搜索最终结果."""
    query: str
    expanded_queries: list[str]
    results: list[HybridChunkResult]
    keyword_count: int = 0
    vector_count: int = 0
    graph_count: int = 0
    execution_time_ms: int = 0


# ---------------------------------------------------------------------------
# Query Expansion (ported from GBrain expansion.ts)
# ---------------------------------------------------------------------------
async def expand_query(query: str, max_queries: int = MAX_QUERIES) -> list[str]:
    """通过 Anthropic Claude 生成查询变体.

    如果查询词少于 3 个，或 Anthropic API 不可用，直接返回原查询.
    """
    word_count = len(query.split())
    if word_count < MIN_WORDS_FOR_EXPANSION:
        return [query]

    api_key = _get_env("ANTHROPIC_API_KEY")
    if not api_key:
        return [query]

    try:
        import anthropic
    except ImportError:
        return [query]

    client = anthropic.AsyncAnthropic(api_key=api_key)

    try:
        response = await client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=300,
            tools=[
                {
                    "name": "expand_query",
                    "description": "Generate alternative phrasings of a search query to improve recall",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "alternative_queries": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "2 alternative phrasings of the original query, each approaching the topic from a different angle",
                            },
                        },
                        "required": ["alternative_queries"],
                    },
                }
            ],
            tool_choice={"type": "tool", "name": "expand_query"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        f'Generate 2 alternative search queries that would find relevant results for this question. '
                        f'Each alternative should approach the topic from a different angle or use different terminology.\n\n'
                        f'Original query: "{query}"'
                    ),
                }
            ],
        )

        alternatives: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "expand_query":
                input_data = getattr(block, "input", {}) or {}
                alts = input_data.get("alternative_queries", [])
                if isinstance(alts, list):
                    alternatives = [str(a) for a in alts[:2]]
                break

        all_queries = [query, *alternatives]
        # 去重（保持大小写原始形式）
        seen: set[str] = set()
        unique: list[str] = []
        for q in all_queries:
            key = q.lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(q)
        return unique[:max_queries]
    except Exception:
        return [query]


# ---------------------------------------------------------------------------
# RRF Fusion (ported from GBrain hybrid.ts)
# ---------------------------------------------------------------------------
def rrf_fusion(lists: list[list[HybridChunkResult]]) -> list[HybridChunkResult]:
    """Reciprocal Rank Fusion: 合并多个排序列表.

    score = sum(1 / (RRF_K + rank)) across all lists it appears in.
    """
    scores: dict[str, dict[str, Any]] = {}

    for result_list in lists:
        for rank, r in enumerate(result_list):
            key = f"{r.slug}:{r.chunk_text[:50]}"
            rrf_score = 1.0 / (RRF_K + rank)
            if key in scores:
                scores[key]["score"] += rrf_score
            else:
                scores[key] = {"result": r, "score": rrf_score}

    # 按融合分数降序排列
    sorted_entries = sorted(scores.values(), key=lambda x: x["score"], reverse=True)

    # 把融合分写回对象，并将来源标记为融合结果，原始来源保留在 metadata 中
    for entry in sorted_entries:
        r = entry["result"]
        r.score = entry["score"]
        r.metadata = r.metadata or {}
        r.metadata["_rrf_source"] = r.source
        r.source = "rrf"

    return [entry["result"] for entry in sorted_entries]


# ---------------------------------------------------------------------------
# 4-Layer Dedup (ported from GBrain dedup.ts)
# ---------------------------------------------------------------------------
def dedup_results(
    results: list[HybridChunkResult],
    cosine_threshold: float = COSINE_DEDUP_THRESHOLD,
    max_type_ratio: float = MAX_TYPE_RATIO,
    max_per_page: int = MAX_PER_PAGE,
) -> list[HybridChunkResult]:
    """4 层去重流水线."""
    deduped = results

    # Layer 1: 每页保留得分最高的 3 个 chunk
    deduped = _dedup_by_source(deduped)

    # Layer 2: 文本相似度去重（用 Jaccard 系数代理余弦相似度）
    deduped = _dedup_by_text_similarity(deduped, cosine_threshold)

    # Layer 3: 类型多样性控制（单一类型不超过总结果的 60%）
    deduped = _enforce_type_diversity(deduped, max_type_ratio)

    # Layer 4: 单页 chunk 上限
    deduped = _cap_per_page(deduped, max_per_page)

    return deduped


def _dedup_by_source(results: list[HybridChunkResult]) -> list[HybridChunkResult]:
    by_page: dict[str, list[HybridChunkResult]] = {}
    for r in results:
        by_page.setdefault(r.slug, []).append(r)

    kept: list[HybridChunkResult] = []
    for chunks in by_page.values():
        chunks.sort(key=lambda x: x.score, reverse=True)
        kept.extend(chunks[:3])

    return sorted(kept, key=lambda x: x.score, reverse=True)


def _dedup_by_text_similarity(
    results: list[HybridChunkResult], threshold: float
) -> list[HybridChunkResult]:
    kept: list[HybridChunkResult] = []

    for r in results:
        r_words = set(r.chunk_text.lower().split())
        too_similar = False
        for k in kept:
            k_words = set(k.chunk_text.lower().split())
            intersection = r_words & k_words
            union = r_words | k_words
            jaccard = len(intersection) / len(union) if union else 0.0
            if jaccard > threshold:
                too_similar = True
                break
        if not too_similar:
            kept.append(r)

    return kept


def _enforce_type_diversity(
    results: list[HybridChunkResult], max_ratio: float
) -> list[HybridChunkResult]:
    max_per_type = max(1, int(len(results) * max_ratio + 0.999))  # ceil
    type_counts: dict[str, int] = {}
    kept: list[HybridChunkResult] = []

    for r in results:
        count = type_counts.get(r.type, 0)
        if count < max_per_type:
            kept.append(r)
            type_counts[r.type] = count + 1

    return kept


def _cap_per_page(results: list[HybridChunkResult], max_per_page: int) -> list[HybridChunkResult]:
    page_counts: dict[str, int] = {}
    kept: list[HybridChunkResult] = []

    for r in results:
        count = page_counts.get(r.slug, 0)
        if count < max_per_page:
            kept.append(r)
            page_counts[r.slug] = count + 1

    return kept


# ---------------------------------------------------------------------------
# Cognee Result Normalization
# ---------------------------------------------------------------------------
def _normalize_cognee_result(raw: Any, source_label: str) -> Optional[HybridChunkResult]:
    """将 Cognee 的多种返回格式统一为 HybridChunkResult."""
    # 兼容 Cognee 包装层: {dataset_id, dataset_name, search_result: [...]}
    if isinstance(raw, dict) and "search_result" in raw:
        results = raw["search_result"]
        if isinstance(results, list) and results:
            # 返回包装内第一条的有效结果；调用方会再遍历，这里只需展开
            return _normalize_cognee_result(results[0], source_label)
        return None

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        return HybridChunkResult(
            slug=_slugify(text[:40]),
            chunk_text=text,
            source=source_label,
        )

    if not isinstance(raw, dict):
        # 尝试从对象属性提取
        text = getattr(raw, "text", None) or getattr(raw, "content", None) or getattr(raw, "name", None) or str(raw)
        slug = getattr(raw, "id", None) or getattr(raw, "name", None) or _slugify(str(text)[:40])
        node_type = getattr(raw, "type", None) or getattr(raw, "node_type", None) or getattr(raw, "label", None) or "unknown"
        return HybridChunkResult(
            slug=str(slug),
            chunk_text=str(text),
            type=str(node_type),
            source=source_label,
        )

    # dict 处理
    text = raw.get("text") or raw.get("content") or raw.get("chunk_text") or raw.get("result") or raw.get("summary")
    if not text:
        # 尝试拼接多个字段
        parts = []
        for k in ("name", "title", "description", "statement"):
            if k in raw and raw[k]:
                parts.append(str(raw[k]))
        text = "\n".join(parts) if parts else None

    if not text:
        return None

    slug = raw.get("id") or raw.get("name") or raw.get("title") or raw.get("source_id") or _slugify(str(text)[:40])
    node_type = raw.get("type") or raw.get("node_type") or raw.get("label") or raw.get("entity_type") or "unknown"

    return HybridChunkResult(
        slug=str(slug),
        chunk_text=str(text).strip(),
        type=str(node_type),
        source=source_label,
        metadata={k: v for k, v in raw.items() if k not in {"text", "content", "chunk_text", "result", "summary"}},
    )


def _flatten_cognee_results(raw: Any) -> list[Any]:
    """将 Cognee 可能返回的嵌套/包装结构展平为 chunk 对象列表."""
    if raw is None:
        return []
    if isinstance(raw, list):
        out = []
        for item in raw:
            out.extend(_flatten_cognee_results(item))
        return out
    if isinstance(raw, dict) and "search_result" in raw:
        return _flatten_cognee_results(raw.get("search_result"))
    return [raw]


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff ]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "untitled"


# ---------------------------------------------------------------------------
# Main Engine
# ---------------------------------------------------------------------------
class AOFHybridSearch:
    """AOF 混合搜索引擎.

    结合 Cognee 的向量搜索（RAG_COMPLETION）和关键词搜索（CHUNKS_LEXICAL），
    通过 RRF 融合 + 4 层去重，提供比单一搜索更高质量的召回.
    """

    def __init__(self, cognee_root: str | None = None):
        self.cognee_root = cognee_root
        self._cognee_available = importlib.util.find_spec("cognee") is not None

    def _import_cognee(self):
        if not self._cognee_available:
            raise RuntimeError("Cognee 未安装")
        if self.cognee_root:
            import sys
            from pathlib import Path
            root = str(Path(self.cognee_root).resolve())
            if root not in sys.path:
                sys.path.insert(0, root)
        import cognee  # type: ignore
        return cognee

    async def _keyword_search(
        self,
        query: str,
        datasets: list[str] | None,
        limit: int,
    ) -> list[HybridChunkResult]:
        """关键词搜索：使用 Cognee CHUNKS_LEXICAL 作为代理."""
        try:
            cognee = self._import_cognee()
            from cognee.modules.search.types import SearchType as CogneeSearchType  # type: ignore

            results = await cognee.search(
                query_type=CogneeSearchType.CHUNKS_LEXICAL,
                query_text=query,
                top_k=limit * 2,
                datasets=datasets,
            )
            normalized = []
            for item in _flatten_cognee_results(results):
                norm = _normalize_cognee_result(item, "keyword")
                if norm:
                    normalized.append(norm)
            return normalized
        except Exception:
            # 完全失败时返回空列表，让融合层处理
            return []

    async def _vector_search(
        self,
        query: str,
        datasets: list[str] | None,
        limit: int,
    ) -> list[HybridChunkResult]:
        """向量搜索：使用 Cognee RAG_COMPLETION 作为代理."""
        try:
            cognee = self._import_cognee()
            from cognee.modules.search.types import SearchType as CogneeSearchType  # type: ignore

            results = await asyncio.wait_for(
                cognee.search(
                    query_type=CogneeSearchType.RAG_COMPLETION,
                    query_text=query,
                    top_k=limit * 2,
                    datasets=datasets,
                ),
                timeout=15.0,
            )
            normalized = []
            for item in _flatten_cognee_results(results):
                norm = _normalize_cognee_result(item, "vector")
                if norm:
                    normalized.append(norm)
            return normalized
        except Exception:
            return []

    async def _graph_search(
        self,
        query: str,
        dataset_id: str | None,
        dataset_name: str | None,
        limit: int,
    ) -> list[HybridChunkResult]:
        """图谱路径召回：实体匹配 + 关系路径扩展，构成第三路.

        该路是 AOF 的差异化能力：从知识图谱的"实体-关系"结构出发，
        输出带完整溯源链路（seed_matched / graph_path / relation）的结果.
        """
        try:
            from bridge.graph_retrieval import graph_path_retrieval  # type: ignore

            hits = await graph_path_retrieval(
                query=query,
                dataset_id=dataset_id,
                dataset_name=dataset_name,
                limit=limit * 2,
                cognee_root=self.cognee_root,
            )
            results: list[HybridChunkResult] = []
            for hit in hits:
                results.append(HybridChunkResult(
                    slug=_slugify(f"{hit.seed}:{hit.node}"),
                    chunk_text=hit.text,
                    type=hit.node_type,
                    score=hit.score,
                    source="graph",
                    metadata={
                        "seed": hit.seed,
                        "node": hit.node,
                        "hops": hit.hops,
                    },
                    provenance=hit.provenance,
                ))
            return results
        except Exception:
            return []

    async def hybrid_search(
        self,
        query: str,
        dataset_id: Optional[str] = None,
        dataset_name: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        expansion: bool = False,
        include_graph: bool = True,
    ) -> HybridSearchResult:
        """执行混合搜索.

        Args:
            query: 搜索查询
            dataset_id: 数据集 ID
            dataset_name: 数据集名称
            limit: 返回结果数量
            offset: 分页偏移
            expansion: 是否开启查询扩展
            include_graph: 是否包含图谱路径召回（第三路）

        Returns:
            HybridSearchResult
        """
        start_time = time.time()
        datasets = []
        if dataset_id:
            datasets.append(dataset_id)
        if dataset_name:
            datasets.append(dataset_name)
        datasets = datasets or None

        inner_limit = min(limit * 2, 100)

        # 1. 查询扩展
        queries = [query]
        if expansion:
            try:
                queries = await expand_query(query)
                if not queries:
                    queries = [query]
            except Exception:
                queries = [query]

        # 2. 并行执行关键词搜索（ always run，不需要额外 API key）
        keyword_tasks = [self._keyword_search(q, datasets, inner_limit) for q in queries]
        keyword_lists = await asyncio.gather(*keyword_tasks, return_exceptions=True)
        keyword_results: list[HybridChunkResult] = []
        for lst in keyword_lists:
            if isinstance(lst, list):
                keyword_results.extend(lst)

        # 3. 并行执行向量搜索
        vector_tasks = [self._vector_search(q, datasets, inner_limit) for q in queries]
        vector_lists = await asyncio.gather(*vector_tasks, return_exceptions=True)
        vector_results: list[HybridChunkResult] = []
        for lst in vector_lists:
            if isinstance(lst, list):
                vector_results.extend(lst)

        # 3b. 图谱路径召回（第三路）
        graph_results: list[HybridChunkResult] = []
        if include_graph:
            try:
                graph_results = await self._graph_search(query, dataset_id, dataset_name, inner_limit)
            except Exception:
                graph_results = []

        # 4. RRF 融合（至少一路非空就融合；不因为向量路失败而退化）
        all_lists = []
        if vector_results:
            all_lists.append(vector_results)
        if keyword_results:
            all_lists.append(keyword_results)
        if graph_results:
            all_lists.append(graph_results)

        if not all_lists:
            return HybridSearchResult(
                query=query,
                expanded_queries=queries,
                results=[],
                keyword_count=0,
                vector_count=0,
                graph_count=0,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        fused = rrf_fusion(all_lists)

        # 5. 4 层去重
        deduped = dedup_results(fused)
        final = deduped[offset : offset + limit]

        return HybridSearchResult(
            query=query,
            expanded_queries=queries,
            results=final,
            keyword_count=len(keyword_results),
            vector_count=len(vector_results),
            graph_count=len(graph_results),
            execution_time_ms=int((time.time() - start_time) * 1000),
        )


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------
async def hybrid_search(
    query: str,
    dataset_id: Optional[str] = None,
    dataset_name: Optional[str] = None,
    limit: int = 20,
    expansion: bool = False,
    include_graph: bool = True,
    cognee_root: Optional[str] = None,
) -> HybridSearchResult:
    """便捷函数：执行混合搜索."""
    engine = AOFHybridSearch(cognee_root=cognee_root)
    return await engine.hybrid_search(
        query=query,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        limit=limit,
        expansion=expansion,
        include_graph=include_graph,
    )


def _get_env(name: str) -> str | None:
    import os
    return os.environ.get(name)
