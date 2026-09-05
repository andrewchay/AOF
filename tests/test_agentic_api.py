from __future__ import annotations

from fastapi.testclient import TestClient

import services.semantic_middle_layer_api.app as api_module
from bridge.semantic_core import SignedPrincipalVerifier


def body(run_id="api-agentic-001"):
    return {
        "run_id": run_id,
        "session_id": "finance-review",
        "query": "收入指标与哪些业务实体有关？",
        "purpose": "weekly-review",
        "channel": "production",
        "release_id": "finance@1",
        "release_digest": "sha256:release",
        "policy_resource_id": "aof://acme/finance/policy/query",
        "allowed_capabilities": ["semantic_sql", "graph_search", "rag_retrieve"],
        "max_steps": 3,
    }


def headers(role, tenant="acme"):
    return SignedPrincipalVerifier(
        key_id="identity-key-default", secret=b"agentic-api-secret"
    ).sign_headers(subject=f"{role}-user", tenant_id=tenant, roles=[role])


def executor(capability, context):
    return {
        "output": {"summary": f"{capability} result"},
        "evidence": [{
            "evidence_id": f"artifact:{capability}",
            "release_id": context["release_id"],
            "release_digest": context["release_digest"],
        }],
    }


def test_agentic_api_routes_runs_evaluates_replays_and_isolates_memory(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "agentic-api-secret")
    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    monkeypatch.setenv("AOF_AGENTIC_RUN_DATABASE", str(tmp_path / "agentic.sqlite3"))
    monkeypatch.setattr(api_module.app.state, "agentic_capability_executor", executor, raising=False)
    client = TestClient(api_module.app)

    route = client.post("/v1/agentic/route", json=body(), headers=headers("analyst"))
    assert route.status_code == 200
    assert route.json()["capabilities"] == ["semantic_sql", "graph_search"]

    run = client.post("/v1/agentic/runs", json=body(), headers=headers("analyst"))
    assert run.status_code == 201
    assert run.json()["status"] == "succeeded"
    assert run.json()["decision_id"].startswith("decision:")

    evaluation = client.get(
        "/v1/agentic/runs/api-agentic-001/evaluation", headers=headers("viewer")
    )
    assert evaluation.status_code == 200
    assert evaluation.json()["score"] == 1.0

    replay = client.post(
        "/v1/agentic/runs/api-agentic-001/replay",
        json={"run_id": "api-agentic-002"},
        headers=headers("operator"),
    )
    assert replay.status_code == 201
    assert replay.json()["reproducible"] is True

    memory = client.get(
        "/v1/agentic/sessions/finance-review/memory", headers=headers("viewer")
    )
    assert memory.status_code == 200
    assert memory.json()["count"] == 2
    other = client.get(
        "/v1/agentic/sessions/finance-review/memory", headers=headers("viewer", "other")
    )
    assert other.status_code == 200
    assert other.json()["count"] == 0


def test_agentic_run_requires_authorized_signed_role(tmp_path, monkeypatch):
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "agentic-api-secret")
    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    monkeypatch.setenv("AOF_AGENTIC_RUN_DATABASE", str(tmp_path / "agentic.sqlite3"))
    monkeypatch.setattr(api_module.app.state, "agentic_capability_executor", executor, raising=False)
    response = TestClient(api_module.app).post(
        "/v1/agentic/runs", json=body(), headers=headers("viewer")
    )
    assert response.status_code == 401
    assert "lacks role" in response.json()["detail"]
