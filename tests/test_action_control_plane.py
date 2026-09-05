"""Signed transport-neutral action control-plane contracts."""

from __future__ import annotations

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionConnectorRegistry,
    ActionControlPlane,
    SignedPrincipalVerifier,
    SqliteActionRunRepository,
)
from tests.test_action_planning import _action_runtime
from tests.test_action_runs import SuccessfulCrmConnector


def _headers(*roles: str) -> dict[str, str]:
    return SignedPrincipalVerifier(
        key_id="identity-v1", secret=b"action-identity-secret"
    ).sign_headers(subject=roles[0], tenant_id="acme", roles=roles)


def test_signed_action_control_plane_plans_approves_and_executes(tmp_path) -> None:
    compilation_repository, action, policy = _action_runtime(tmp_path)
    connector = SuccessfulCrmConnector()
    connectors = ActionConnectorRegistry()
    connectors.register("crm-core", connector)
    control = ActionControlPlane(
        compilation_repository,
        verifier=SignedPrincipalVerifier(
            key_id="identity-v1", secret=b"action-identity-secret"
        ),
        action_runs=SqliteActionRunRepository(tmp_path / "action-runs.sqlite3"),
        connectors=connectors,
        decision_store=DecisionProvenanceStore(tmp_path / "action-decisions.jsonl"),
    )
    request = {
        "channel": "production",
        "action_type_id": action.resource_id,
        "object_ids": ["customer:alice"],
        "inputs": {"reason": "confirmed fraud"},
        "purpose": "fraud-response",
        "idempotency_key": "control-plane-001",
        "policy_resource_id": policy.resource_id,
    }

    plan = control.plan(request, headers=_headers("risk-operator"))
    submitted = control.submit(
        {
            **request,
            "expected_plan_digest": plan["plan_digest"],
            "rationale": "Submit the exact reviewed plan.",
        },
        headers=_headers("risk-operator"),
    )
    approved = control.approve(
        {
            "run_id": submitted["run_id"],
            "rationale": "Approve bounded customer action.",
        },
        headers=_headers("risk-reviewer"),
    )
    succeeded = control.execute(
        {"run_id": approved["run_id"]}, headers=_headers("action-executor")
    )

    assert plan["release_id"] == "crm-actions@1.0.0"
    assert submitted["status"] == "awaiting_approval"
    assert approved["status"] == "approved"
    assert succeeded["status"] == "succeeded"
    assert control.get_run(
        succeeded["run_id"], headers=_headers("auditor")
    )["run_digest"] == succeeded["run_digest"]
    assert connector.calls == 1


def test_rest_action_boundary_uses_signed_release_pinned_plan(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import services.semantic_middle_layer_api.app as api_module

    repository, action, policy = _action_runtime(tmp_path)
    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    monkeypatch.setenv("AOF_COMPILER_STATE_DIR", str(tmp_path / "compiler"))
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "action-identity-secret")
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_KEY_ID", "identity-v1")
    request = {
        "channel": "production",
        "action_type_id": action.resource_id,
        "object_ids": ["customer:alice"],
        "inputs": {"reason": "confirmed fraud"},
        "purpose": "fraud-response",
        "idempotency_key": "rest-action-001",
        "policy_resource_id": policy.resource_id,
    }

    response = TestClient(api_module.app).post(
        "/v1/semantic/actions/plan",
        json=request,
        headers=_headers("risk-operator"),
    )

    assert repository.get_channel("production") is not None
    assert response.status_code == 200
    assert response.json()["release_id"] == "crm-actions@1.0.0"
    assert response.json()["plan_digest"].startswith("sha256:")
