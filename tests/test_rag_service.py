"""exporters/rag_service 单元测试.

覆盖：
- _chunk_to_retrieval 序列化（截断 / 分数 round / metadata 过滤 / provenance 透传 / 容错）
- rag_retrieve 流程（route_counts / 结果序列化 / 空结果 / error 字段）

运行:
    python -m pytest tests/test_rag_service.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from bridge.hybrid_search import HybridChunkResult, HybridSearchResult
from exporters.rag_service import RagResult, _chunk_to_retrieval, rag_retrieve


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
class TestChunkToRetrieval:
    def test_full_serialization(self) -> None:
        chunk = HybridChunkResult(
            slug="s1",
            chunk_text="hello world",
            type="entity",
            score=0.123456,
            source="graph",
            metadata={"seed": "x", "nested": {"a": 1}, "lst": [1, 2]},
            provenance={"route": "graph", "seed_node": "x"},
        )
        d = _chunk_to_retrieval(chunk)
        assert d["text"] == "hello world"
        assert d["score"] == pytest.approx(round(0.123456, 4), abs=1e-6)
        assert d["type"] == "entity"
        assert d["source"] == "graph"
        assert d["slug"] == "s1"
        # 标量保留，dict/list 被过滤
        assert d["metadata"]["seed"] == "x"
        assert "nested" not in d["metadata"]
        assert "lst" not in d["metadata"]
        assert d["provenance"] == {"route": "graph", "seed_node": "x"}

    def test_body_truncation(self) -> None:
        chunk = HybridChunkResult(slug="s", chunk_text="x" * 1000)
        d = _chunk_to_retrieval(chunk, limit_body=50)
        assert len(d["text"]) == 50

    def test_none_tolerated(self) -> None:
        d = _chunk_to_retrieval(None)
        assert d["text"] == ""
        assert d["type"] == "unknown"
        assert d["score"] == 0.0
        assert d["source"] == "unknown"
        assert d["slug"] == ""
        assert d["metadata"] == {}
        assert d["provenance"] == {}

    def test_zero_score(self) -> None:
        chunk = HybridChunkResult(slug="s", chunk_text="t", score=0.0)
        assert _chunk_to_retrieval(chunk)["score"] == 0.0


# ---------------------------------------------------------------------------
# rag_retrieve flow
# ---------------------------------------------------------------------------
class TestRagRetrieve:
    def _engine_returns(self, search_result: HybridSearchResult):
        patcher = patch("bridge.hybrid_search.AOFHybridSearch")
        mock_cls = patcher.start()
        engine = mock_cls.return_value
        engine.hybrid_search = AsyncMock(return_value=search_result)
        return patcher, engine

    def test_route_counts_and_results(self) -> None:
        search = HybridSearchResult(
            query="q",
            expanded_queries=["q"],
            results=[
                HybridChunkResult(
                    slug="g1", chunk_text="graph hit", type="entity",
                    score=0.5, source="rrf", provenance={"route": "graph", "seed_node": "Alpha"},
                )
            ],
            keyword_count=3,
            vector_count=2,
            graph_count=1,
        )
        patcher, _ = self._engine_returns(search)
        try:
            result = asyncio.run(rag_retrieve("q"))
        finally:
            patcher.stop()

        assert isinstance(result, RagResult)
        assert result.query == "q"
        assert result.route_counts == {"keyword": 3, "vector": 2, "graph": 1}
        assert len(result.results) == 1
        assert result.results[0]["provenance"] == {"route": "graph", "seed_node": "Alpha"}
        assert result.results[0]["score"] == pytest.approx(0.5)
        assert result.error is None

    def test_empty_results(self) -> None:
        search = HybridSearchResult(query="q", expanded_queries=["q"], results=[])
        patcher, _ = self._engine_returns(search)
        try:
            result = asyncio.run(rag_retrieve("q"))
        finally:
            patcher.stop()
        assert result.results == []
        assert result.route_counts == {"keyword": 0, "vector": 0, "graph": 0}

    def test_to_dict_structure(self) -> None:
        r = RagResult(
            query="q",
            results=[{"text": "t", "type": "chunk", "score": 0.1, "source": "rrf", "slug": "s",
                      "metadata": {}, "provenance": {}}],
            route_counts={"keyword": 1, "vector": 0, "graph": 0},
            execution_time_ms=42,
        )
        d = r.to_dict()
        assert d["query"] == "q"
        assert d["count"] == 1
        assert d["execution_time_ms"] == 42
        assert d["error"] is None
        assert d["results"][0]["score"] == 0.1

    def test_params_forwarded_to_engine(self) -> None:
        search = HybridSearchResult(query="q", expanded_queries=["q"], results=[])
        patcher, engine = self._engine_returns(search)
        try:
            asyncio.run(rag_retrieve("q", dataset_id="d1", dataset_name="d2", limit=7,
                                     expansion=True, include_graph=False))
        finally:
            patcher.stop()
        engine.hybrid_search.assert_awaited_once_with(
            query="q",
            dataset_id="d1",
            dataset_name="d2",
            limit=7,
            expansion=True,
            include_graph=False,
        )
