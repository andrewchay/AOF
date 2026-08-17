"""Production-hardening contracts for governed Knowledge Releases."""

from __future__ import annotations

import pytest

from bridge.semantic_core import KnowledgeRelease, ResourceKind, SemanticResource
from bridge.semantic_core.attestations import HmacReleaseAttestor
from bridge.semantic_core.compilers import default_compiler_registry
from bridge.semantic_core.governance import (
    SemanticGovernanceError,
    SemanticGovernancePolicy,
    SemanticGovernanceService,
)
from bridge.semantic_core.releases import ReleaseError, SqliteReleaseRepository


def _release(tenant: str, *, description: str = "") -> KnowledgeRelease:
    resource = SemanticResource.create(
        resource_id=f"aof://{tenant}/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
        description=description,
    )
    return KnowledgeRelease.build(
        release_id="sales-knowledge@1.0.0",
        resources=[resource],
        scope={"tenant_id": tenant, "domain": "sales"},
    )


def test_sqlite_repository_is_transactional_immutable_and_tenant_isolated(tmp_path) -> None:
    repository = SqliteReleaseRepository(tmp_path / "releases.sqlite3")
    acme = _release("acme")
    beta = _release("beta")

    assert repository.publish(acme, tenant_id="acme").release_digest == acme.release_digest
    assert repository.publish(acme, tenant_id="acme").release_digest == acme.release_digest
    assert repository.get(acme.release_id, tenant_id="acme").release_digest == acme.release_digest
    assert repository.get(acme.release_id, tenant_id="beta") is None
    assert repository.publish(beta, tenant_id="beta").release_digest == beta.release_digest

    with pytest.raises(ReleaseError, match="cannot be overwritten"):
        repository.publish(_release("acme", description="tampered replacement"), tenant_id="acme")
    with pytest.raises(ReleaseError, match="scope tenant_id"):
        repository.publish(acme, tenant_id="beta")


def test_release_attestation_is_keyed_verifiable_and_tamper_evident() -> None:
    release = _release("acme")
    attestor = HmacReleaseAttestor(key_id="release-key-2026-08", secret=b"test-secret")

    attestation = attestor.sign(
        release,
        actor="publisher:carol",
        decision_id="decision:publish-1",
        tenant_id="acme",
    )

    assert attestation["algorithm"] == "hmac-sha256"
    assert attestation["key_id"] == "release-key-2026-08"
    assert attestor.verify(attestation, release=release) is True
    assert attestor.verify({**attestation, "actor": "attacker"}, release=release) is False
    assert attestor.verify(attestation, release=_release("acme", description="different")) is False


def test_governance_enforces_roles_separation_and_signed_transactional_publish(tmp_path) -> None:
    repository = SqliteReleaseRepository(tmp_path / "releases.sqlite3")
    attestor = HmacReleaseAttestor(key_id="release-key", secret=b"test-secret")
    service = SemanticGovernanceService(
        tmp_path / "governance",
        compiler_registry=default_compiler_registry(),
        release_repository=repository,
        access_policy=SemanticGovernancePolicy(),
        release_attestor=attestor,
    )
    resource = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
    )
    proposal = service.create_proposal(
        proposal_id="signed-sales",
        release_id="signed-sales@1.0.0",
        resources=[resource],
        actor="editor:alice",
        rationale="Governed publication.",
    )
    assert proposal["tenant_id"] == "acme"
    service.validate(proposal["proposal_id"], actor="validator:system")
    with pytest.raises(SemanticGovernanceError, match="role.*approve"):
        service.approve(proposal["proposal_id"], actor="editor:alice", rationale="self approve")
    service.approve(proposal["proposal_id"], actor="reviewer:bob", rationale="independent review")
    service.compile(proposal["proposal_id"], actor="compiler:system", targets=["mcp"])
    with pytest.raises(SemanticGovernanceError, match="separation of duties"):
        service.publish(proposal["proposal_id"], actor="publisher:bob")

    published = service.publish(proposal["proposal_id"], actor="publisher:carol")
    release = repository.get(published["release_id"], tenant_id="acme")
    assert release is not None
    assert attestor.verify(published["attestation"], release=release) is True
