# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Signed enterprise runtime control never trusts tenant or actor request fields."""

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    EnterpriseRuntimeControlPlane,
    SignedPrincipalVerifier,
    SqliteIncrementalReasoningRuntime,
)


def _headers(role: str, subject: str, tenant: str = "acme") -> dict[str, str]:
    return SignedPrincipalVerifier(
        key_id="runtime-identity", secret=b"runtime-secret"
    ).sign_headers(subject=subject, tenant_id=tenant, roles=[role])


def test_signed_reasoning_control_is_tenant_isolated_and_actor_bound(tmp_path) -> None:
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    control = EnterpriseRuntimeControlPlane(
        verifier=SignedPrincipalVerifier(
            key_id="runtime-identity", secret=b"runtime-secret"
        ),
        reasoning=SqliteIncrementalReasoningRuntime(
            tmp_path / "reasoning.sqlite3", decision_store=decisions
        ),
    )
    payload = {
        "ruleset_id": "risk@1",
        "program": "at_risk(X) :- signal(X).",
        "change": {
            "change_id": "signal-alice",
            "tenant_id": "spoofed",
            "actor": "admin:spoofed",
            "assertions": [{"predicate": "signal", "terms": ["alice"]}],
            "retractions": [],
            "source": {"type": "event", "id": "signal-alice"},
        },
    }

    with pytest.raises(Exception, match="signed principal headers are incomplete"):
        control.apply_reasoning(payload, headers={})
    run = control.apply_reasoning(
        payload, headers=_headers("reasoner", "reasoning-worker")
    )
    facts = control.query_reasoning(
        ruleset_id="risk@1",
        predicate="at_risk",
        headers=_headers("viewer", "auditor"),
    )
    other = control.list_reasoning_runs(
        headers=_headers("viewer", "auditor", tenant="other")
    )

    assert run["tenant_id"] == "acme"
    assert run["actor"] == "reasoner:reasoning-worker"
    assert facts["facts"][0]["terms"] == ["alice"]
    assert other == {"runs": [], "count": 0}


def test_signed_runtime_rest_exposes_reasoning_evidence(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    import services.semantic_middle_layer_api.app as api_module

    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_KEY_ID", "runtime-identity")
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "runtime-secret")
    monkeypatch.setenv(
        "AOF_REASONING_RUNTIME_DATABASE", str(tmp_path / "reasoning.sqlite3")
    )
    monkeypatch.setenv(
        "AOF_DECISION_PROVENANCE_FILE", str(tmp_path / "decisions.jsonl")
    )
    client = TestClient(api_module.app)
    payload = {
        "ruleset_id": "risk@1",
        "program": "at_risk(X) :- signal(X).",
        "change": {
            "change_id": "signal-alice-rest",
            "assertions": [{"predicate": "signal", "terms": ["alice"]}],
            "retractions": [],
            "source": {"type": "event", "id": "signal-alice-rest"},
        },
    }

    unsigned = client.post("/v1/semantic/reasoning-runs", json=payload)
    created = client.post(
        "/v1/semantic/reasoning-runs",
        json=payload,
        headers=_headers("reasoner", "runtime-worker"),
    )
    facts = client.get(
        "/v1/semantic/reasoning/facts",
        params={"ruleset_id": "risk@1", "predicate": "at_risk"},
        headers=_headers("viewer", "auditor"),
    )

    assert unsigned.status_code == 401
    assert created.status_code == 201
    assert created.json()["audit_decision_id"].startswith("decision:reasoning:")
    assert facts.json()["facts"][0]["terms"] == ["alice"]
