"""Decision-grade provenance: causal trace, precedents, impact and integrity."""

from __future__ import annotations

import asyncio

import pytest

from bridge.decision_provenance import DecisionProvenanceError, DecisionProvenanceStore


def _record(store: DecisionProvenanceStore, **overrides):
    payload = {
        "agent_id": "agent:planner", "decision_type": "deployment_approval",
        "conclusion": "approve", "rationale": "All release gates passed.",
        "evidence": [{"id": "artifact:release-gates", "content_hash": "sha256:abc"}],
        "tags": ["release", "low-risk"], "policies": ["policy:release-v1"],
        "output_entities": [{"id": "deployment:2026-08-17", "type": "deployment"}],
    }
    payload.update(overrides)
    return store.record(**payload)


def test_causal_chain_impact_precedent_and_audit_trail(tmp_path):
    store = DecisionProvenanceStore(tmp_path / "ledger.jsonl")
    root = _record(store, decision_id="decision:assess", decision_type="risk_assessment")
    child = _record(store, decision_id="decision:approve", parent_decision_ids=[root["decision"]["id"]])
    _record(store, decision_id="decision:approve-previous", tags=["release", "medium-risk"])

    chain = store.causal_chain(child["decision"]["id"])
    assert [node["decision"]["id"] for node in chain["nodes"]] == ["decision:approve", "decision:assess"]
    assert chain["edges"] == [{"from": "decision:assess", "to": "decision:approve", "type": "wasInformedBy"}]

    impact = store.impact(root["decision"]["id"])
    assert impact["affected_decision_count"] == 1
    assert {item["decision_id"] for item in impact["affected_entities"]} == {"decision:assess", "decision:approve"}

    precedents = store.find_precedents("deployment_approval", tags=["release", "low-risk"])
    assert precedents[0]["decision"]["decision"]["id"] == "decision:approve"
    assert precedents[0]["similarity"] == 1.0

    trail = store.audit_trail(child["decision"]["id"])
    assert trail["@context"]["prov"] == "http://www.w3.org/ns/prov#"
    assert trail["compliance"]["evidence_complete"] is True
    assert trail["integrity"]["valid"] is True


def test_parent_must_exist_and_hash_tampering_is_detected(tmp_path):
    store = DecisionProvenanceStore(tmp_path / "ledger.jsonl")
    with pytest.raises(DecisionProvenanceError, match="unknown parent"):
        _record(store, parent_decision_ids=["decision:missing"])

    _record(store, decision_id="decision:one")
    with pytest.raises(DecisionProvenanceError, match="already exists"):
        _record(store, decision_id="decision:one")
    text = store.path.read_text(encoding="utf-8").replace("approve", "reject", 1)
    store.path.write_text(text, encoding="utf-8")
    assert store.verify_integrity()["valid"] is False


def test_mcp_tools_are_registered_and_operate_on_configured_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("AOF_DECISION_PROVENANCE_FILE", str(tmp_path / "mcp.jsonl"))
    from mcp_server import build_server
    server = build_server()
    assert {"aof_record_decision", "aof_decision_audit_trail", "aof_find_decision_precedents"}.issubset(server.tools)
    created = asyncio.run(server.tools["aof_record_decision"].handler({
        "agent_id": "agent:mcp", "decision_type": "routing", "conclusion": "use graph",
        "rationale": "The request asks for causal path evidence.", "tags": ["graph"],
    }))
    trail = asyncio.run(server.tools["aof_decision_audit_trail"].handler({"decision_id": created["decision"]["id"]}))
    assert trail["integrity"]["valid"] is True


def test_rest_api_exposes_decision_lifecycle(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import services.semantic_middle_layer_api.app as api_module

    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    client = TestClient(api_module.app)
    created = client.post("/v1/decisions", json={
        "decision_id": "decision:api-root", "agent_id": "agent:api",
        "decision_type": "review", "conclusion": "approved", "rationale": "Evidence passed.",
        "evidence": [{"id": "report:1"}], "policies": ["policy:review"],
    })
    assert created.status_code == 201
    child = client.post("/v1/decisions", json={
        "decision_id": "decision:api-child", "agent_id": "agent:api",
        "decision_type": "release", "conclusion": "deploy", "rationale": "Review approved.",
        "parent_decision_ids": ["decision:api-root"], "output_entities": [{"id": "deploy:1"}],
    })
    assert child.status_code == 201
    assert client.get("/v1/decisions/decision:api-child/causal-chain").json()["edges"][0]["from"] == "decision:api-root"
    assert client.post("/v1/decisions/impact", json={"decision_id": "decision:api-root"}).json()["affected_decision_count"] == 1
    assert client.get("/v1/decisions/decision:api-child/audit-trail").json()["integrity"]["valid"] is True
