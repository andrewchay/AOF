# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with the
# Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""aof-semantic 只读数据资产服务测试：SemanticQuery 库 + MCP 工具层。

跑法：``python -m pytest tests/test_aof_semantic_query.py -q -o addopts=''``
依赖真实资产（data/aof_resource_index/resource_index.sqlite3 与 resources_full.json），
资产缺失时 importorskip 优雅跳过。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bridge.semantic_query import DEFAULT_INDEX_DB, DEFAULT_RESOURCES_JSON, SemanticQuery

pytestmark = pytest.mark.skipif(
    not (Path(DEFAULT_INDEX_DB).exists() and Path(DEFAULT_RESOURCES_JSON).exists()),
    reason="语义资产未构建（先跑 build_metric_registry/build_playbook_scenes + build_resource_index）",
)


@pytest.fixture(scope="module")
def sq() -> SemanticQuery:
    return SemanticQuery()


def test_search_kind_filter_returns_only_requested_kind(sq: SemanticQuery) -> None:
    out = sq.search("DAU", kind="Metric", k=5)
    assert out["results"], "DAU 应命中口径卡"
    assert all(r["kind"] == "Metric" for r in out["results"])


def test_route_returns_layered_grounding_plan(sq: SemanticQuery) -> None:
    plan = sq.route("预流失口径怎么算")
    assert plan["metrics"], "预流失应命中口径卡"
    assert plan["scenes"], "预流失应命中场景卡"
    assert any("churn-prediction" in s["resource_id"] for s in plan["scenes"])
    assert plan["guidance"] and "合同文件" in plan["guidance"][0]


def test_route_miss_falls_back_to_gap_guidance(sq: SemanticQuery) -> None:
    plan = sq.route("zzqq wxyz 0xdeadbeef")
    assert not plan["metrics"] and not plan["scenes"] and not plan["tables"]
    assert plan["route_mode"] == "vocab_match"
    assert any("ontology_gaps" in g for g in plan["guidance"])


def test_get_returns_full_card_with_valid_refs(sq: SemanticQuery) -> None:
    card = sq.get("aof://mihoyo/hk4e/playbook/churn-prediction")
    assert card["kind"] == "Playbook"
    refs = card["spec"]["refs"]
    assert refs.get("口径") and Path(refs["口径"]).exists()
    metric_card = sq.get("aof://mihoyo/hk4e/metric/return-retention/zongze/付费")
    assert metric_card["spec"]["red_lines"], "付费口径卡应携带红线"


def test_get_unknown_id_raises_keyerror(sq: SemanticQuery) -> None:
    with pytest.raises(KeyError):
        sq.get("aof://mihoyo/hk4e/metric/not/exist")


def test_status_reports_assets(sq: SemanticQuery) -> None:
    st = sq.status()
    assert st["index_exists"] and st["index_total"] and st["index_total"] >= 5852
    assert st["index_kind_counts"]["Metric"] == 284
    assert st["kuzu_exists"]


def test_families_counts_metric_cards(sq: SemanticQuery) -> None:
    fam = sq.families()
    assert sum(fam.values()) == 284
    assert "活跃与流转族" in fam


# ---------------------------------------------------------------------------
# MCP 工具层（handler 直调，模式同 test_trusted_query_execution.py）
# ---------------------------------------------------------------------------

def _call(server, name: str, args: dict) -> dict:
    result = asyncio.run(server._handle_tool_call(name, args))
    assert result.get("isError") is False, result
    return json.loads(result["content"][0]["text"])


def test_server_lists_four_tools() -> None:
    from tools.aof_semantic_mcp import build_server

    server = build_server()
    assert set(server.tools) == {"aof_route", "aof_search", "aof_get", "aof_status"}


def test_server_route_and_get_roundtrip() -> None:
    from tools.aof_semantic_mcp import build_server

    server = build_server()
    plan = _call(server, "aof_route", {"question": "伪流失"})
    assert plan["metrics"] and "人流失" in plan["metrics"][0]["display_name"]
    rid = plan["metrics"][0]["resource_id"]
    card = _call(server, "aof_get", {"resource_id": rid})
    assert card["resource_id"] == rid


def test_server_missing_required_argument_is_invalid_arguments() -> None:
    from tools.aof_semantic_mcp import build_server

    server = build_server()
    result = asyncio.run(server._handle_tool_call("aof_search", {}))
    assert result["isError"] is True
    err = json.loads(result["content"][0]["text"])
    assert err["code"] == "invalid_arguments"


def test_server_unknown_resource_maps_to_resource_not_found() -> None:
    from tools.aof_semantic_mcp import build_server

    server = build_server()
    result = asyncio.run(server._handle_tool_call("aof_get", {"resource_id": "aof://nope"}))
    assert result["isError"] is True
    err = json.loads(result["content"][0]["text"])
    assert err["code"] == "resource_not_found"
