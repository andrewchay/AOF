# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
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

    # W06.02 (D09): sessions default to private — a different subject in the
    # same tenant can no longer read the run/evaluation/memory (404 hides
    # existence). The owner (analyst-user) retains full access.
    denied_evaluation = client.get(
        "/v1/agentic/runs/api-agentic-001/evaluation", headers=headers("viewer")
    )
    assert denied_evaluation.status_code == 404

    denied_replay = client.post(
        "/v1/agentic/runs/api-agentic-001/replay",
        json={"run_id": "api-agentic-002"},
        headers=headers("operator"),
    )
    assert denied_replay.status_code == 404

    denied_memory = client.get(
        "/v1/agentic/sessions/finance-review/memory", headers=headers("viewer")
    )
    assert denied_memory.status_code == 404

    # Owner reads their own session data
    evaluation = client.get(
        "/v1/agentic/runs/api-agentic-001/evaluation", headers=headers("analyst")
    )
    assert evaluation.status_code == 200
    assert evaluation.json()["score"] == 1.0

    memory = client.get(
        "/v1/agentic/sessions/finance-review/memory", headers=headers("analyst")
    )
    assert memory.status_code == 200
    assert memory.json()["count"] == 2

    # Explicit share via API: owner promotes to team visibility and grants
    # viewer-user read access -> now allowed
    non_owner_share = client.put(
        "/v1/agentic/sessions/finance-review/visibility",
        json={"visibility": "team"},
        headers=headers("viewer"),
    )
    assert non_owner_share.status_code == 404  # non-owner cannot manage ACL

    shared = client.put(
        "/v1/agentic/sessions/finance-review/visibility",
        json={"visibility": "team"},
        headers=headers("analyst"),
    )
    assert shared.status_code == 200
    granted = client.put(
        "/v1/agentic/sessions/finance-review/acl",
        json={"subject_kind": "subject", "subject_id": "viewer-user", "can_read": True},
        headers=headers("analyst"),
    )
    assert granted.status_code == 200
    granted_memory = client.get(
        "/v1/agentic/sessions/finance-review/memory", headers=headers("viewer")
    )
    assert granted_memory.status_code == 200
    assert granted_memory.json()["count"] == 2

    # Tenant isolation still holds for other tenants
    other = client.get(
        "/v1/agentic/sessions/finance-review/memory", headers=headers("viewer", "other")
    )
    assert other.status_code == 404


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
