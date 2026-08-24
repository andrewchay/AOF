"""Rotation-aware signing shared by Release and Query evidence attestations."""

from __future__ import annotations

from bridge.semantic_core import KnowledgeRelease, ResourceKind, SemanticResource
from bridge.semantic_core.keys import (
    ExternalSigningProvider,
    LocalSigningKeyProvider,
    ProviderQueryEvidenceAttestor,
    ProviderReleaseAttestor,
)
from bridge.semantic_core.query_runs import QueryRun


def _release() -> KnowledgeRelease:
    resource = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
    )
    return KnowledgeRelease.build(
        release_id="sales@1.0.0",
        resources=[resource],
        scope={"tenant_id": "acme"},
    )


def _query_run(run_id: str) -> QueryRun:
    return QueryRun.build(
        query_run_id=run_id,
        tenant_id="acme",
        actor="principal:alice",
        request={"channel": "production", "query": "customer"},
        compilation_run_id="compile-001",
        compilation_run_digest="sha256:compile",
        release_id="sales@1.0.0",
        release_digest="sha256:release",
        plan_digest="sha256:plan",
        policy_report_digest="sha256:policy",
        governed_result={"governed_result_digest": "sha256:result"},
        decisions={"execution": "decision:001"},
        evidence_package={"package_digest": "sha256:evidence"},
        replay_of=None,
        recorded_at="2026-08-24T00:00:00+00:00",
    )


def test_release_and_query_attestations_survive_shared_provider_rotation() -> None:
    provider = LocalSigningKeyProvider(
        {"key-v1": b"old-signing-secret", "key-v2": b"new-signing-secret"},
        current_key_id="key-v1",
    )
    release_attestor = ProviderReleaseAttestor(provider)
    query_attestor = ProviderQueryEvidenceAttestor(provider)
    release = _release()
    query = _query_run("query-001")

    old_release = release_attestor.sign(
        release,
        actor="publisher:alice",
        decision_id="decision:release-1",
        tenant_id="acme",
    )
    old_query = query_attestor.sign(query)
    provider.rotate("key-v2")
    new_release = release_attestor.sign(
        release,
        actor="publisher:bob",
        decision_id="decision:release-2",
        tenant_id="acme",
    )
    new_query = query_attestor.sign(query)

    assert old_release["key_id"] == old_query["key_id"] == "key-v1"
    assert new_release["key_id"] == new_query["key_id"] == "key-v2"
    assert release_attestor.verify(old_release, release=release)
    assert release_attestor.verify(new_release, release=release)
    assert query_attestor.verify(old_query, run=query)
    assert query_attestor.verify(new_query, run=query)


def test_external_signer_boundary_never_returns_key_material() -> None:
    class FakeKmsClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def sign(self, key_id: str, payload: bytes) -> str:
            self.calls.append(("sign", key_id))
            return f"kms:{key_id}:{len(payload)}"

        def verify(self, key_id: str, payload: bytes, signature: str) -> bool:
            self.calls.append(("verify", key_id))
            return signature == f"kms:{key_id}:{len(payload)}"

    client = FakeKmsClient()
    provider = ExternalSigningProvider(
        client,
        algorithm="kms-asymmetric-sha256",
        current_key_id="kms-release-query-v1",
    )
    attestor = ProviderQueryEvidenceAttestor(provider)
    run = _query_run("query-kms-001")

    attestation = attestor.sign(run)

    assert attestor.verify(attestation, run=run)
    assert client.calls == [
        ("sign", "kms-release-query-v1"),
        ("verify", "kms-release-query-v1"),
    ]
    assert not hasattr(provider, "get")
