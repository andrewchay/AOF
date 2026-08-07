"""bridge/graph_retrieval 单元测试.

覆盖：
- 实体提取（英文去停用词 / 中文 2-4 字窗口 / 空查询）
- 种子节点匹配（完全匹配 / 子串匹配 / 阈值过滤 / max_seeds）
- 图谱构建（节点 / 边 / 默认关系 / 脏数据跳过）
- 邻居扩展（1 跳 / 2 跳 / 不存在的种子 / 上限）
- graph_path_retrieval（直接传 nodes/edges，不依赖 cognee）

运行:
    python -m pytest tests/test_graph_retrieval.py -v
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from bridge.graph_retrieval import (
    GraphPathHit,
    _path_text,
    build_graph,
    expand_from_seed,
    extract_query_entities,
    graph_path_retrieval,
    graph_retrieval,
    match_seed_nodes,
)


def sample_nodes() -> list[dict[str, Any]]:
    return [
        {"id": "alpha", "label": "Alpha", "type": "entity"},
        {"id": "beta", "label": "Beta", "type": "entity"},
        {"id": "gamma", "label": "Gamma", "type": "entity"},
        {"id": "delta", "label": "Delta", "type": "entity"},
    ]


def sample_edges() -> list[dict[str, Any]]:
    return [
        {"source": "alpha", "target": "beta", "relation": "knows"},
        {"source": "alpha", "target": "delta", "relation": "likes"},
        {"source": "beta", "target": "gamma", "relation": "owns"},
    ]


# ---------------------------------------------------------------------------
# Entity Extraction
# ---------------------------------------------------------------------------
class TestExtractQueryEntities:
    def test_english_stopwords_filtered(self) -> None:
        ents = extract_query_entities("Garry Tan and YC relationship")
        assert "Garry" in ents
        assert "Tan" in ents
        assert "YC" in ents
        assert "and" not in [e.lower() for e in ents]
        assert "relationship" not in [e.lower() for e in ents]

    def test_chinese_windows(self) -> None:
        ents = extract_query_entities("八重神子与御影炉心")
        assert "八重神子与御影炉心" in ents
        assert "八重" in ents
        assert "神子" in ents
        assert "御影炉心" in ents

    def test_empty_query(self) -> None:
        assert extract_query_entities("") == []
        assert extract_query_entities("   ") == []

    def test_ordering_by_length_desc(self) -> None:
        ents = extract_query_entities("ABC DEFG")
        # 长词优先
        lengths = [len(e) for e in ents]
        assert lengths == sorted(lengths, reverse=True)


# ---------------------------------------------------------------------------
# Seed Matching
# ---------------------------------------------------------------------------
class TestMatchSeedNodes:
    def test_exact_match_scores_highest(self) -> None:
        nodes = [{"id": "n1", "label": "Garry Tan", "type": "person"}]
        seeds = match_seed_nodes("Garry Tan", nodes)
        assert len(seeds) == 1
        assert seeds[0]["score"] == 10.0
        assert seeds[0]["matched"] == "garry tan"

    def test_substring_match(self) -> None:
        nodes = [{"id": "n1", "label": "People's Army", "type": "org"}]
        seeds = match_seed_nodes("People", nodes)
        assert len(seeds) == 1
        assert 0.0 < seeds[0]["score"] < 10.0

    def test_empty_nodes(self) -> None:
        assert match_seed_nodes("query", []) == []

    def test_max_seeds(self) -> None:
        nodes = [{"id": f"n{i}", "label": f"Alice{i}", "type": "person"} for i in range(10)]
        seeds = match_seed_nodes("Alice", nodes, max_seeds=3)
        assert len(seeds) == 3

    def test_no_match_below_threshold(self) -> None:
        nodes = [{"id": "n1", "label": "CompletelyUnrelatedNode", "type": "x"}]
        seeds = match_seed_nodes("queryterm that does not match", nodes)
        assert seeds == []


# ---------------------------------------------------------------------------
# Graph Building
# ---------------------------------------------------------------------------
class TestBuildGraph:
    def test_builds_nodes_and_edges(self) -> None:
        G = build_graph(sample_nodes(), sample_edges())
        assert set(G.nodes) == {"alpha", "beta", "gamma", "delta"}
        assert G.has_edge("alpha", "beta")
        assert G["alpha"]["beta"]["relation"] == "knows"

    def test_node_labels_and_types(self) -> None:
        G = build_graph(sample_nodes(), sample_edges())
        assert G.nodes["alpha"]["label"] == "Alpha"
        assert G.nodes["alpha"]["node_type"] == "entity"

    def test_skips_invalid_nodes_and_edges(self) -> None:
        nodes = [{"label": "no-id"}, {"id": "", "label": "empty-id"}]
        edges = [{"source": "a"}, {"target": "b"}, {"source": "", "target": "x"}]
        G = build_graph(nodes, edges)
        assert G.number_of_nodes() == 0
        assert G.number_of_edges() == 0

    def test_default_relation(self) -> None:
        nodes = [{"id": "a"}, {"id": "b"}]
        edges = [{"source": "a", "target": "b"}]
        G = build_graph(nodes, edges)
        assert G["a"]["b"]["relation"] == "related_to"


# ---------------------------------------------------------------------------
# Path Text Rendering
# ---------------------------------------------------------------------------
class TestPathText:
    def test_renders_path(self) -> None:
        G = build_graph(sample_nodes(), sample_edges())
        assert _path_text(G, ["alpha", "beta"]) == "Alpha --knows--> Beta"
        assert _path_text(G, ["alpha", "beta", "gamma"]) == "Alpha --knows--> Beta --owns--> Gamma"

    def test_unknown_nodes_fallback_to_id(self) -> None:
        G = build_graph(sample_nodes(), sample_edges())
        assert _path_text(G, ["alpha", "missing"]) == "Alpha --related_to--> missing"


# ---------------------------------------------------------------------------
# Neighbor Expansion
# ---------------------------------------------------------------------------
class TestExpandFromSeed:
    def test_one_hop(self) -> None:
        G = build_graph(sample_nodes(), sample_edges())
        paths = expand_from_seed(G, "alpha", max_hops=1)
        assert len(paths) == 2  # beta, delta
        assert all(p["hops"] == 1 for p in paths)
        assert paths[0]["path"] == ["alpha", "beta"]
        assert paths[0]["seed"] == "Alpha"
        assert paths[0]["relation"] == "knows"

    def test_two_hops(self) -> None:
        G = build_graph(sample_nodes(), sample_edges())
        paths = expand_from_seed(G, "alpha", max_hops=2)
        hops = {p["hops"] for p in paths}
        assert hops == {1, 2}
        two_hop = [p for p in paths if p["hops"] == 2]
        assert two_hop[0]["path"] == ["alpha", "beta", "gamma"]
        assert two_hop[0]["score"] == pytest.approx(0.6)

    def test_missing_seed(self) -> None:
        G = build_graph(sample_nodes(), sample_edges())
        assert expand_from_seed(G, "not_exist") == []

    def test_max_per_hop_cap(self) -> None:
        nodes = [{"id": f"hub", "label": "Hub"}]
        edges = [{"source": "hub", "target": f"n{i}"} for i in range(20)]
        G = build_graph(nodes, edges)
        paths = expand_from_seed(G, "hub", max_hops=1, max_per_hop=3)
        assert len(paths) == 3


# ---------------------------------------------------------------------------
# Main Retrieval
# ---------------------------------------------------------------------------
class TestGraphPathRetrieval:
    def test_returns_hits_with_provenance(self) -> None:
        hits = asyncio.run(graph_path_retrieval(
            "Alpha and Beta relationship",
            nodes=sample_nodes(),
            edges=sample_edges(),
        ))
        assert len(hits) >= 1
        h = hits[0]
        assert h.seed == "Alpha"
        assert h.text == "Alpha --knows--> Beta"
        assert h.hops == 1
        assert h.score > 0.0
        assert h.provenance["route"] == "graph"
        assert h.provenance["graph_path"] == ["alpha", "beta"]
        assert h.provenance["relation"] == "knows"
        assert h.provenance["seed_node"] == "Alpha"

    def test_sorted_by_hops_then_score(self) -> None:
        hits = asyncio.run(graph_path_retrieval(
            "Alpha",
            nodes=sample_nodes(),
            edges=sample_edges(),
        ))
        assert len(hits) == 3
        hops = [h.hops for h in hits]
        assert hops == [1, 1, 2]  # 1 跳在前，2 跳在后

    def test_limit(self) -> None:
        hits = asyncio.run(graph_path_retrieval(
            "Alpha",
            nodes=sample_nodes(),
            edges=sample_edges(),
            limit=2,
        ))
        assert len(hits) == 2

    def test_no_nodes_or_edges(self) -> None:
        assert asyncio.run(graph_path_retrieval("query", nodes=[], edges=[])) == []

    def test_no_seed_match(self) -> None:
        hits = asyncio.run(graph_path_retrieval(
            "zzzzz no match here",
            nodes=sample_nodes(),
            edges=sample_edges(),
        ))
        assert hits == []

    def test_duplicate_paths_deduped(self) -> None:
        # alpha → beta 存在两条相同关系边，应只产生一条路径
        nodes = [{"id": "alpha", "label": "Alpha"}, {"id": "beta", "label": "Beta"}]
        edges = [
            {"source": "alpha", "target": "beta", "relation": "knows"},
            {"source": "alpha", "target": "beta", "relation": "knows"},
        ]
        hits = asyncio.run(graph_path_retrieval("Alpha", nodes=nodes, edges=edges))
        assert len(hits) == 1

    def test_score_scaled_by_seed_match(self) -> None:
        nodes = [
            {"id": "alpha", "label": "Alpha", "type": "entity"},
            {"id": "beta", "label": "Beta", "type": "entity"},
        ]
        edges = [{"source": "alpha", "target": "beta", "relation": "knows"}]
        hits = asyncio.run(graph_path_retrieval("Alpha", nodes=nodes, edges=edges))
        assert len(hits) == 1
        # 完全匹配种子 score=10.0 → 路径分 = 1.0 * 10.0/10 = 1.0
        assert hits[0].score == pytest.approx(1.0)

    @patch("bridge.graph_retrieval.graph_path_retrieval")
    def test_graph_retrieval_convenience(self, mock_gpr) -> None:
        mock_gpr.return_value = [
            GraphPathHit(seed="s", node="n", path=["s", "n"], text="s --r--> n")
        ]
        hits = asyncio.run(graph_retrieval("q", dataset_name="d"))
        assert len(hits) == 1
        mock_gpr.assert_awaited_once()
