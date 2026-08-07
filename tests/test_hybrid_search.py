"""bridge/hybrid_search 单元测试.

覆盖：
- RRF 融合（分数计算、跨列表累加、source 标记、空输入）
- 4 层去重（按来源、文本相似度、类型多样性、单页上限）
- Cognee 结果归一化与展平
- 查询扩展（短查询 / 无 key / anthropic 缺失 / mock 成功 / 异常兜底）
- 引擎级多路召回（fake 路由，不依赖真实 cognee）
- 回归：单路召回时 score 不允许退化为 0（防止上次 "score=0.0" bug 复发）

运行:
    python -m pytest tests/test_hybrid_search.py -v
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

from bridge.hybrid_search import (
    AOFHybridSearch,
    COSINE_DEDUP_THRESHOLD,
    MAX_PER_PAGE,
    MAX_TYPE_RATIO,
    RRF_K,
    HybridChunkResult,
    _cap_per_page,
    _dedup_by_source,
    _dedup_by_text_similarity,
    _enforce_type_diversity,
    _flatten_cognee_results,
    _normalize_cognee_result,
    dedup_results,
    expand_query,
    rrf_fusion,
)


def make_result(
    slug: str,
    text: str,
    type_: str = "chunk",
    score: float = 0.0,
    source: str = "keyword",
    provenance: dict[str, Any] | None = None,
) -> HybridChunkResult:
    return HybridChunkResult(
        slug=slug,
        chunk_text=text,
        type=type_,
        score=score,
        source=source,
        provenance=provenance or {},
    )


def async_seq(value: Any):
    """构造始终返回 value 的 async 函数."""
    async def _f(*args: Any, **kwargs: Any) -> Any:
        return value
    return _f


# ---------------------------------------------------------------------------
# RRF Fusion
# ---------------------------------------------------------------------------
class TestRRFFusion:
    def test_single_list_rank_scoring(self) -> None:
        lst = [make_result("a", "alpha"), make_result("b", "beta")]
        fused = rrf_fusion([lst])
        assert len(fused) == 2
        assert fused[0].score == pytest.approx(1.0 / (RRF_K + 0))
        assert fused[1].score == pytest.approx(1.0 / (RRF_K + 1))
        # 降序排列
        assert fused[0].slug == "a"
        assert fused[1].slug == "b"

    def test_source_marked_rrf_with_original_kept(self) -> None:
        lst = [make_result("a", "alpha", source="keyword")]
        fused = rrf_fusion([lst])
        assert fused[0].source == "rrf"
        assert fused[0].metadata.get("_rrf_source") == "keyword"

    def test_cross_list_accumulates_score(self) -> None:
        a = make_result("x", "same content", source="keyword")
        b = make_result("x", "same content", source="vector")
        fused = rrf_fusion([[a], [b]])
        # 同一 key 在两路中都排在 rank 0 → 2/60
        assert len(fused) == 1
        assert fused[0].score == pytest.approx(2.0 / RRF_K)

    def test_cross_list_rank_sensitivity(self) -> None:
        # 路1 中 x 排第 0，路2 中 x 排第 2
        a = make_result("x", "same content", source="keyword")
        l1 = [a, make_result("y", "other one")]
        l2 = [make_result("z", "third one"), make_result("w", "fourth one"), make_result("x", "same content", source="vector")]
        fused = rrf_fusion([l1, l2])
        assert fused[0].slug == "x"
        assert fused[0].score == pytest.approx(1.0 / (RRF_K + 0) + 1.0 / (RRF_K + 2))

    def test_empty_lists(self) -> None:
        assert rrf_fusion([]) == []
        assert rrf_fusion([[], []]) == []

    def test_deduplicated_slug_text_key(self) -> None:
        # 同 slug 不同文本视为不同结果
        a = make_result("s", "text one", source="keyword")
        b = make_result("s", "text two", source="vector")
        fused = rrf_fusion([[a], [b]])
        assert len(fused) == 2


# ---------------------------------------------------------------------------
# 4-Layer Dedup
# ---------------------------------------------------------------------------
class TestDedupBySource:
    def test_keeps_top3_per_page(self) -> None:
        results = [
            make_result("p1", "t1", score=1.0),
            make_result("p1", "t2", score=3.0),
            make_result("p1", "t3", score=2.0),
            make_result("p1", "t4", score=0.5),
        ]
        kept = _dedup_by_source(results)
        assert len(kept) == 3
        assert {r.chunk_text for r in kept} == {"t1", "t2", "t3"}
        assert [r.score for r in kept] == [3.0, 2.0, 1.0]

    def test_groups_by_slug(self) -> None:
        results = [
            make_result("p1", "a", score=1.0),
            make_result("p2", "b", score=1.0),
            make_result("p1", "c", score=2.0),
        ]
        kept = _dedup_by_source(results)
        # p1 保留 top2、p2 保留 1 → 总 3 个不同 slug 都保留
        assert len(kept) == 3
        assert {r.slug for r in kept} == {"p1", "p2"}


class TestDedupTextSimilarity:
    def test_identical_text_dropped(self) -> None:
        r1 = make_result("a", "apple banana cherry")
        r2 = make_result("b", "apple banana cherry")
        r3 = make_result("c", "completely different words")
        kept = _dedup_by_text_similarity([r1, r2, r3], COSINE_DEDUP_THRESHOLD)
        assert r1 in kept
        assert r2 not in kept
        assert r3 in kept

    def test_below_threshold_kept(self) -> None:
        r1 = make_result("a", "unique text number 1")
        r2 = make_result("b", "unique text number 9")
        # jaccard = 3/4 = 0.75 < 0.85 → 都保留
        kept = _dedup_by_text_similarity([r1, r2], 0.85)
        assert r1 in kept and r2 in kept

    def test_empty_results(self) -> None:
        assert _dedup_by_text_similarity([], 0.85) == []


class TestEnforceTypeDiversity:
    def test_caps_dominant_type(self) -> None:
        results = [
            make_result(f"k{i}", f"keyword text {i}", type_="chunk") for i in range(10)
        ] + [
            make_result(f"g{i}", f"graph text {i}", type_="entity") for i in range(5)
        ]
        kept = _enforce_type_diversity(results, MAX_TYPE_RATIO)
        # len=15, max_per_type = ceil(15*0.6) = 9
        chunk_kept = [r for r in kept if r.type == "chunk"]
        entity_kept = [r for r in kept if r.type == "entity"]
        assert len(chunk_kept) == 9
        assert len(entity_kept) == 5

    def test_all_same_type(self) -> None:
        results = [make_result(f"n{i}", f"text {i}") for i in range(10)]
        kept = _enforce_type_diversity(results, 0.6)
        assert len(kept) == 6  # ceil(10*0.6)


class TestCapPerPage:
    def test_caps_same_slug(self) -> None:
        results = [make_result("p1", f"text {i}", score=float(10 - i)) for i in range(5)]
        kept = _cap_per_page(results, MAX_PER_PAGE)
        assert len(kept) == MAX_PER_PAGE
        assert kept[0].score == pytest.approx(10.0)


class TestDedupPipeline:
    def test_full_pipeline_caps_per_page(self) -> None:
        results = [
            make_result("p1", f"unique text number {i}", score=float(10 - i), source="keyword")
            for i in range(8)
        ]
        deduped = dedup_results(results)
        assert len(deduped) == MAX_PER_PAGE
        assert deduped[0].score == pytest.approx(10.0)
        assert deduped[1].score == pytest.approx(9.0)

    def test_full_pipeline_empty(self) -> None:
        assert dedup_results([]) == []


# ---------------------------------------------------------------------------
# Cognee Result Normalization
# ---------------------------------------------------------------------------
class TestNormalizeCogneeResult:
    def test_string_input(self) -> None:
        r = _normalize_cognee_result("hello world", "keyword")
        assert r is not None
        assert r.chunk_text == "hello world"
        assert r.source == "keyword"

    def test_blank_string_returns_none(self) -> None:
        assert _normalize_cognee_result("   ", "keyword") is None

    def test_dict_input(self) -> None:
        raw = {"text": "some content", "type": "Entity", "id": "n1", "extra": "keep"}
        r = _normalize_cognee_result(raw, "vector")
        assert r is not None
        assert r.slug == "n1"
        assert r.type == "Entity"
        assert r.source == "vector"
        assert "text" not in r.metadata
        assert r.metadata["extra"] == "keep"

    def test_wrapped_search_result(self) -> None:
        raw = {"dataset_id": "d", "search_result": [{"text": "inner text", "id": "x"}]}
        r = _normalize_cognee_result(raw, "keyword")
        assert r is not None
        assert r.chunk_text == "inner text"
        assert r.slug == "x"

    def test_no_text_returns_none(self) -> None:
        assert _normalize_cognee_result({"id": "x", "type": "t"}, "keyword") is None

    def test_object_input(self) -> None:
        class _Obj:
            text = "object text"
            id = "obj1"

        r = _normalize_cognee_result(_Obj(), "keyword")
        assert r is not None
        assert r.chunk_text == "object text"
        assert r.slug == "obj1"


class TestFlattenCogneeResults:
    def test_nested_wrappers(self) -> None:
        raw = {"search_result": [{"text": "a"}, [{"text": "b"}, "c"]]}
        flat = _flatten_cognee_results(raw)
        assert len(flat) == 3

    def test_none_and_empty(self) -> None:
        assert _flatten_cognee_results(None) == []
        assert _flatten_cognee_results([]) == []
        assert _flatten_cognee_results({}) == [{}]


# ---------------------------------------------------------------------------
# Query Expansion
# ---------------------------------------------------------------------------
class TestExpandQuery:
    def test_short_query_returns_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert asyncio.run(expand_query("魔女会")) == ["魔女会"]

    def test_no_api_key_returns_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = asyncio.run(expand_query("how does the system work"))
        assert result == ["how does the system work"]

    def test_anthropic_not_installed_returns_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        result = asyncio.run(expand_query("how does the system work"))
        assert result == ["how does the system work"]

    @staticmethod
    def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch, messages_cls: type) -> None:
        mod = types.ModuleType("anthropic")

        class _Client:
            def __init__(self, api_key: str | None = None):
                self.messages = messages_cls()

        mod.AsyncAnthropic = _Client  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "anthropic", mod)

    def test_mock_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        class _Block:
            type = "tool_use"
            name = "expand_query"
            input = {"alternative_queries": ["alt query one", "alt query two"]}

        class _Response:
            content = [_Block()]

        class _Messages:
            async def create(self, **kwargs: Any) -> _Response:
                return _Response()

        self._install_fake_anthropic(monkeypatch, _Messages)

        result = asyncio.run(expand_query("how does the system work"))
        assert result[0] == "how does the system work"
        assert "alt query one" in result
        assert "alt query two" in result
        assert len(result) == 3

    def test_duplicates_removed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        class _Block:
            type = "tool_use"
            name = "expand_query"
            input = {"alternative_queries": ["How Does The System Work"]}

        class _Response:
            content = [_Block()]

        class _Messages:
            async def create(self, **kwargs: Any) -> _Response:
                return _Response()

        self._install_fake_anthropic(monkeypatch, _Messages)

        result = asyncio.run(expand_query("how does the system work"))
        # 大小写不同视为重复，只保留原查询
        assert result == ["how does the system work"]

    def test_anthropic_error_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:
                raise RuntimeError("boom")

        self._install_fake_anthropic(monkeypatch, _Messages)

        result = asyncio.run(expand_query("how does the system work"))
        assert result == ["how does the system work"]


# ---------------------------------------------------------------------------
# Engine-level hybrid search (fake routes, no real cognee)
# ---------------------------------------------------------------------------
class TestHybridSearchEngine:
    def _make_engine(self, monkeypatch: pytest.MonkeyPatch, keyword=None, vector=None, graph=None) -> AOFHybridSearch:
        engine = AOFHybridSearch()
        if keyword is not None:
            monkeypatch.setattr(engine, "_keyword_search", async_seq(keyword))
        if vector is not None:
            monkeypatch.setattr(engine, "_vector_search", async_seq(vector))
        if graph is not None:
            monkeypatch.setattr(engine, "_graph_search", async_seq(graph))
        return engine

    def test_merges_all_routes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = self._make_engine(
            monkeypatch,
            keyword=[make_result("k1", "keyword hit one", source="keyword")],
            vector=[make_result("v1", "vector hit one", source="vector")],
            graph=[make_result("g1", "graph hit one", type_="entity", source="graph",
                               provenance={"route": "graph"})],
        )
        result = asyncio.run(engine.hybrid_search("query", dataset_id="d", dataset_name="n"))
        assert result.query == "query"
        assert result.keyword_count == 1
        assert result.vector_count == 1
        assert result.graph_count == 1
        assert len(result.results) == 3
        # 融合后统一标记为 rrf，且分数恒大于 0
        assert all(r.source == "rrf" for r in result.results)
        assert all(r.score > 0.0 for r in result.results)

    def test_include_graph_false_skips_graph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = self._make_engine(
            monkeypatch,
            keyword=[make_result("k1", "keyword hit one", source="keyword")],
            vector=[make_result("v1", "vector hit one", source="vector")],
        )

        def _fail_graph(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("graph route should not be called")

        monkeypatch.setattr(engine, "_graph_search", _fail_graph)
        result = asyncio.run(engine.hybrid_search("query", include_graph=False))
        assert result.graph_count == 0
        assert len(result.results) == 2

    def test_all_routes_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = self._make_engine(monkeypatch, keyword=[], vector=[], graph=[])
        result = asyncio.run(engine.hybrid_search("query"))
        assert result.results == []
        assert result.keyword_count == 0
        assert result.vector_count == 0
        assert result.graph_count == 0

    def test_regression_single_route_graph_only_still_scores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回归：只有图谱路有结果时（keyword/vector 路失败为空），
        score 不允许退化为 0，结果不允许为空."""
        graph = [make_result("g1", "graph hit one", type_="entity", source="graph",
                             provenance={"route": "graph"})]
        engine = self._make_engine(monkeypatch, keyword=[], vector=[], graph=graph)
        result = asyncio.run(engine.hybrid_search("query"))
        assert len(result.results) == 1
        assert result.results[0].score > 0.0
        assert result.results[0].source == "rrf"

    def test_pagination_offset_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 5 个不同 slug / 不重叠词 → 类型多样性层最多保留 ceil(5*0.6)=3 条
        keyword = [
            make_result("k1", "alpha one", source="keyword"),
            make_result("k2", "beta two", source="keyword"),
            make_result("k3", "gamma three", source="keyword"),
            make_result("k4", "delta four", source="keyword"),
            make_result("k5", "epsilon five", source="keyword"),
        ]
        engine = self._make_engine(monkeypatch, keyword=keyword, vector=[], graph=[])
        page1 = asyncio.run(engine.hybrid_search("query", limit=2, offset=0))
        page2 = asyncio.run(engine.hybrid_search("query", limit=2, offset=2))
        assert len(page1.results) == 2
        assert len(page2.results) == 1

    def test_expansion_falls_back_to_single_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        seen: list[str] = []

        async def _kw(q: str, datasets, limit: int):
            seen.append(q)
            return []

        engine = AOFHybridSearch()
        monkeypatch.setattr(engine, "_keyword_search", _kw)
        monkeypatch.setattr(engine, "_vector_search", async_seq([]))
        monkeypatch.setattr(engine, "_graph_search", async_seq([]))

        result = asyncio.run(engine.hybrid_search("a long query with several words", expansion=True))
        assert seen == ["a long query with several words"]
        assert result.expanded_queries == ["a long query with several words"]
