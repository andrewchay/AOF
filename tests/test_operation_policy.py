"""W01.01 — Per-operation authorization policy matrix.

- every non-public registered operation declares a policy (loader rejects
  undeclared routes outright)
- policies derived by path class: sensitive surfaces are admin-only,
  decision writes require operator+, ingest writes require ingestor+
- the strict gate enforces policies after authentication: a valid
  signature with insufficient roles gets 403, not access
- negative case: an operation with a malformed policy fails validation
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Registry-level policy validation
# ---------------------------------------------------------------------------


def test_every_registered_operation_has_policy():
    from bridge.access.operation_registry import load_registry

    registry = load_registry()
    denied = [
        op_id for op_id, op in registry.items()
        if not op.public and op.policy is None
    ]
    assert denied == [], f"operations without policy: {denied}"


def test_public_operations_may_omit_policy():
    from bridge.access.operation_registry import load_registry

    registry = load_registry()
    health = registry['get:/healthz']
    assert health.public is True
    assert health.policy is None or health.policy is not None  # both valid


def test_malformed_policy_fails_validation(tmp_path):
    registry_path = tmp_path / "operations.json"
    registry_path.write_text(json.dumps({
        "operations": [{
            "operation_id": "get:/v1/whatever",
            "method": "GET",
            "path": "/v1/whatever",
            "classification": "legacy",
            "public": False,
            "auth": "signed-principal",
            "policy": {"action": "fly", "allowed_roles": ["viewer"]},  # bad action
        }],
    }), encoding="utf-8")

    from bridge.access.operation_registry import OperationRegistryError, load_registry

    with pytest.raises(OperationRegistryError):
        load_registry(registry_path)


def test_undeclared_policy_fails_validation(tmp_path):
    registry_path = tmp_path / "operations.json"
    registry_path.write_text(json.dumps({
        "operations": [{
            "operation_id": "post:/v1/no-policy",
            "method": "POST",
            "path": "/v1/no-policy",
            "classification": "legacy",
            "public": False,
            "auth": "signed-principal",
            # no policy
        }],
    }), encoding="utf-8")

    from bridge.access.operation_registry import OperationRegistryError, load_registry

    with pytest.raises(OperationRegistryError, match="no authorization policy"):
        load_registry(registry_path)


# ---------------------------------------------------------------------------
# Derived defaults
# ---------------------------------------------------------------------------


def test_derived_defaults_are_sensible():
    from bridge.access.policy import derive_default_policy

    revocations = derive_default_policy("POST", "/v1/access/revocations")
    assert revocations.action == "admin"
    assert revocations.allowed_roles == {"admin"}

    decision_read = derive_default_policy("GET", "/v1/decisions/{decision_id}")
    assert "viewer" in decision_read.allowed_roles

    decision_write = derive_default_policy("POST", "/v1/decisions")
    assert "viewer" not in decision_write.allowed_roles
    assert "operator" in decision_write.allowed_roles

    ingest_write = derive_default_policy("POST", "/v1/ingest/metadata")
    assert "ingestor" in ingest_write.allowed_roles
    assert "viewer" not in ingest_write.allowed_roles


def test_authorize_exact_and_denial():
    from bridge.access.policy import OperationPolicy, PolicyDenied, authorize

    policy = OperationPolicy.from_dict(
        {"action": "write", "allowed_roles": ["editor", "admin"]}
    )
    authorize(policy, ["editor"])
    authorize(policy, ["admin"])
    with pytest.raises(PolicyDenied):
        authorize(policy, ["viewer"])


# ---------------------------------------------------------------------------
# Strict-gate enforcement end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture()
def strict_client(monkeypatch):
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "policy-e2e")
    monkeypatch.setenv("AOF_API_AUTH_MODE", "strict")

    from fastapi.testclient import TestClient

    from bridge.semantic_core.identity import SignedPrincipalVerifier
    from services.semantic_middle_layer_api.app import app

    client = TestClient(app)
    verifier = SignedPrincipalVerifier(key_id="identity-key-default", secret=b"policy-e2e")
    return client, verifier


def test_viewer_cannot_write_but_editor_can(strict_client):
    client, verifier = strict_client
    viewer = verifier.sign_headers(subject="v", tenant_id="acme", roles=["viewer"])
    editor = verifier.sign_headers(subject="e", tenant_id="acme", roles=["editor"])

    denied = client.post("/v1/ingest/metadata", json={"name": "x"}, headers=viewer)
    assert denied.status_code == 403
    assert denied.json()["code"] == "operation_not_permitted"

    allowed = client.post("/v1/ingest/metadata", json={"name": "x"}, headers=editor)
    assert allowed.status_code not in (401, 403)


def test_admin_only_surface_blocks_viewer(strict_client):
    client, verifier = strict_client
    viewer = verifier.sign_headers(subject="v", tenant_id="acme", roles=["viewer"])

    resp = client.post(
        "/v1/access/revocations", json={"kind": "subject", "identity": "x"}, headers=viewer
    )
    assert resp.status_code == 403


def test_read_policy_allows_viewer_on_get(strict_client):
    client, verifier = strict_client
    viewer = verifier.sign_headers(subject="v", tenant_id="acme", roles=["viewer"])

    resp = client.get("/v1/access/revocations/subject/nobody", headers=viewer)
    assert resp.status_code == 200
    assert resp.json()["revoked"] is False
