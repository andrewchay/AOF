"""Signed, tenant-isolated compiler control-plane contracts."""

from __future__ import annotations

import asyncio
import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    KnowledgeRelease,
    PrincipalVerificationError,
    ResourceKind,
    SemanticResource,
    SignedPrincipalVerifier,
)
from bridge.semantic_core.compilers import (
    CompilerControlPlane,
    SqliteCompilationRunRepository,
    default_compiler_registry,
)


def _headers(role: str, subject: str, tenant: str = "acme") -> dict[str, str]:
    return SignedPrincipalVerifier(key_id="compiler-identity", secret=b"identity-secret").sign_headers(
        subject=subject, tenant_id=tenant, roles=[role]
    )


def _payload() -> dict:
    concept = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
    )
    policy = SemanticResource.create(
        resource_id="aof://acme/platform/policy/compiler-production",
        kind=ResourceKind.POLICY,
        name="compiler-production",
        domain="platform",
        owner="platform-governance",
        spec={
            "policy_type": "compiler",
            "allowed_compilers": {
                "semantic-json": ["semantic-json@1"],
                "mcp": ["mcp@1"],
            },
        },
    )
    release = KnowledgeRelease.build(
        release_id="sales-knowledge@1.0.0",
        resources=[concept],
        scope={"tenant_id": "acme"},
    )
    return {
        "release": release.to_dict(),
        "resources": [concept.to_dict()],
        "policy": policy.to_dict(),
        "targets": ["mcp"],
    }


def test_signed_control_plane_plans_runs_replays_and_promotes(tmp_path) -> None:
    control = CompilerControlPlane(
        tmp_path / "compiler",
        verifier=SignedPrincipalVerifier(key_id="compiler-identity", secret=b"identity-secret"),
        registry=default_compiler_registry(),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    payload = _payload()

    plan = control.plan(payload, headers=_headers("compiler", "ci"))
    report = control.evaluate(payload, headers=_headers("compiler", "ci"))
    assert plan["valid"] is True and report["conforms"] is True

    first = control.execute(
        {
            **payload,
            "run_id": "sales-compile-001",
            "expected_plan_digest": plan["plan_digest"],
            "rationale": "Compile through the signed control plane.",
        },
        headers=_headers("compiler", "ci"),
    )
    assert first["tenant_id"] == "acme"
    replay = control.replay(
        {
            **payload,
            "source_run_id": first["run_id"],
            "run_id": "sales-compile-002",
            "rationale": "Independently reproduce the signed run.",
        },
        headers=_headers("compiler", "replay-ci"),
    )
    approval = control.approve_promotion(
        {
            "run_id": replay["run_id"],
            "channel": "production",
            "rationale": "Approve exact reproduced artifacts.",
        },
        headers=_headers("reviewer", "alice"),
    )
    pointer = control.promote(
        {
            "run_id": replay["run_id"],
            "channel": "production",
            "approval_decision_id": approval["decision"]["id"],
            "rationale": "Move the governed production pointer.",
        },
        headers=_headers("publisher", "bob"),
    )

    assert replay["reproducible"] is True
    assert pointer["run_id"] == replay["run_id"]
    assert control.get_channel("production", headers=_headers("viewer", "auditor")) == pointer


def test_control_plane_rejects_bad_signature_and_cross_tenant_content(tmp_path) -> None:
    control = CompilerControlPlane(
        tmp_path / "compiler",
        verifier=SignedPrincipalVerifier(key_id="compiler-identity", secret=b"identity-secret"),
        registry=default_compiler_registry(),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    bad = _headers("compiler", "ci")
    bad["x-aof-principal-signature"] = "bad"
    with pytest.raises(PrincipalVerificationError, match="signature"):
        control.plan(_payload(), headers=bad)

    with pytest.raises(ValueError, match="tenant"):
        control.plan(_payload(), headers=_headers("compiler", "ci", tenant="beta"))


def test_control_plane_reopens_transactional_state_after_restart(tmp_path) -> None:
    root = tmp_path / "compiler"
    verifier = SignedPrincipalVerifier(
        key_id="compiler-identity", secret=b"identity-secret"
    )
    control = CompilerControlPlane(
        root,
        verifier=verifier,
        registry=default_compiler_registry(),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
        repository_factory=SqliteCompilationRunRepository,
    )
    payload = _payload()
    plan = control.plan(payload, headers=_headers("compiler", "ci"))
    run = control.execute(
        {
            **payload,
            "run_id": "sales-compile-restart-001",
            "expected_plan_digest": plan["plan_digest"],
            "rationale": "Persist a run across control-plane restart.",
        },
        headers=_headers("compiler", "ci"),
    )

    restarted = CompilerControlPlane(
        root,
        verifier=verifier,
        registry=default_compiler_registry(),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
        repository_factory=SqliteCompilationRunRepository,
    )
    assert restarted.get_run(
        run["run_id"], headers=_headers("viewer", "auditor")
    )["run_digest"] == run["run_digest"]


def test_rest_compiler_control_plane_uses_signed_principals(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import services.semantic_middle_layer_api.app as api_module

    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "identity-secret")
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_KEY_ID", "compiler-identity")
    client = TestClient(api_module.app)
    payload = _payload()

    plan = client.post(
        "/v1/semantic/compiler/plan", json=payload, headers=_headers("compiler", "ci")
    )
    assert plan.status_code == 200
    first = client.post(
        "/v1/semantic/compiler/runs",
        json={
            **payload,
            "run_id": "sales-compile-api-001",
            "expected_plan_digest": plan.json()["plan_digest"],
            "rationale": "Signed REST compilation.",
        },
        headers=_headers("compiler", "ci"),
    )
    assert first.status_code == 201
    replay = client.post(
        "/v1/semantic/compiler/runs/replay",
        json={
            **payload,
            "source_run_id": first.json()["run_id"],
            "run_id": "sales-compile-api-002",
            "rationale": "Signed REST replay.",
        },
        headers=_headers("compiler", "replay-ci"),
    )
    approval = client.post(
        "/v1/semantic/compiler/channels/approvals",
        json={
            "run_id": replay.json()["run_id"],
            "channel": "production",
            "rationale": "Signed approval.",
        },
        headers=_headers("reviewer", "alice"),
    )
    pointer = client.post(
        "/v1/semantic/compiler/channels/promote",
        json={
            "run_id": replay.json()["run_id"],
            "channel": "production",
            "approval_decision_id": approval.json()["decision"]["id"],
            "rationale": "Signed pointer promotion.",
        },
        headers=_headers("publisher", "bob"),
    )

    assert replay.status_code == 201 and replay.json()["reproducible"] is True
    assert approval.status_code == 201
    assert pointer.status_code == 200 and pointer.json()["run_id"] == replay.json()["run_id"]
    assert client.get(
        "/v1/semantic/compiler/channels/production", headers=_headers("viewer", "auditor")
    ).json()["pointer_digest"] == pointer.json()["pointer_digest"]


def test_mcp_compiler_tools_share_the_signed_control_plane(tmp_path, monkeypatch) -> None:
    import mcp_server

    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "identity-secret")
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_KEY_ID", "compiler-identity")
    monkeypatch.setenv("AOF_COMPILER_STATE_DIR", str(tmp_path / "compiler"))
    monkeypatch.setenv("AOF_DECISION_PROVENANCE_FILE", str(tmp_path / "decisions.jsonl"))
    server = mcp_server.build_server()
    expected = {
        "aof_semantic_compile_plan",
        "aof_semantic_compile_evaluate",
        "aof_semantic_compile_run",
        "aof_semantic_compile_replay",
        "aof_semantic_compile_approve_promotion",
        "aof_semantic_compile_promote",
        "aof_semantic_compile_rollback",
        "aof_semantic_compile_get_run",
        "aof_semantic_compile_get_channel",
    }
    assert expected.issubset(server.tools)

    result = asyncio.run(
        server.tools["aof_semantic_compile_plan"].handler(
            {**_payload(), "principal_headers": _headers("compiler", "mcp-ci")}
        )
    )
    assert result["valid"] is True
    assert result["compiler_lock"] == {"mcp": "mcp@1", "semantic-json": "semantic-json@1"}
