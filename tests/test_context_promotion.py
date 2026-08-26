import pytest

from bridge.context_exchange import (
    ApprovalDecision,
    ContextAssertion,
    ContextAssertionEvidence,
    ContextExchangeError,
    ContextGateway,
    ContextPacket,
    ContextPromotionService,
    ContextSpace,
    SqliteContextPacketRepository,
)
from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import SemanticGovernanceService
from bridge.semantic_core.compilers import CompilerRegistry, SemanticBundleCompiler


def _packet() -> ContextPacket:
    draft = ContextSpace.create(space_id="project-alpha-draft", tenant_id="acme", visibility="shared-draft", purpose=["project-delivery"])
    evidence = ContextAssertionEvidence.create(evidence_id="evidence:meeting-1", source_ref="local:minutes:meeting-1", content_hash="sha256:abc", observed_at="2026-08-24T00:00:00Z", disclosure="reference")
    assertion = ContextAssertion.create(assertion_id="assertion:launch-date", category="decision", statement="The launch date is approved.", confidence=0.9, evidence=[evidence])
    return ContextPacket.create(packet_id="packet:meeting-1", producer="mycontext", submitted_by="alice", target_space=draft, assertions=[assertion], consent_decision_id="decision:consent-1", consented_purpose=["project-delivery"], consent_expires_at="2026-12-31T00:00:00Z")


def _service(tmp_path):
    repository = SqliteContextPacketRepository(tmp_path / "packets.sqlite3")
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    ContextGateway(repository=repository, decision_store=decisions).receive(_packet(), tenant_id="acme", actor="gateway:ingress", rationale="consented")
    governance = SemanticGovernanceService(tmp_path / "governance", decision_store=decisions, compiler_registry=CompilerRegistry([SemanticBundleCompiler()]))
    return ContextPromotionService(repository=repository, governance=governance, decision_store=decisions), repository, decisions


def _approvals(*, public: bool = False) -> list[ApprovalDecision]:
    values = [
        ApprovalDecision("external:privacy", "pat", "privacy-reviewer", "approved"),
        ApprovalDecision("external:domain", "dev", "domain-approver", "approved"),
        ApprovalDecision("external:publisher", "priya", "publisher", "approved"),
    ]
    if public:
        values.append(ApprovalDecision("external:public", "sue", "public-reviewer", "approved"))
    return values


def test_tenant_review_promotes_a_quarantined_packet_to_a_governed_release(tmp_path) -> None:
    service, repository, decisions = _service(tmp_path)
    target = ContextSpace.create(space_id="project-alpha", tenant_id="acme", visibility="tenant-governed", purpose=["project-delivery"])
    published = service.promote(packet_id="packet:meeting-1", tenant_id="acme", target_space=target, proposal_id="packet-meeting-1-tenant", release_id="context-alpha@1", approvals=_approvals(), rationale="Approved project decision.")

    assert published.visibility == "tenant-governed"
    assert repository.get_publication("packet:meeting-1", tenant_id="acme", visibility="tenant-governed") == published
    assert any(item["decision"]["decision_type"] == "context_privacy-reviewer_approval" for item in decisions._entries())


def test_public_release_requires_tenant_release_and_fresh_consent(tmp_path) -> None:
    service, _, _ = _service(tmp_path)
    tenant = ContextSpace.create(space_id="project-alpha", tenant_id="acme", visibility="tenant-governed", purpose=["project-delivery"])
    public = ContextSpace.create(space_id="project-alpha-public", tenant_id="acme", visibility="public-governed", purpose=["project-delivery"])
    service.promote(packet_id="packet:meeting-1", tenant_id="acme", target_space=tenant, proposal_id="packet-meeting-1-tenant", release_id="context-alpha@1", approvals=_approvals(), rationale="Approved project decision.")

    with pytest.raises(ContextExchangeError, match="public consent"):
        service.promote(packet_id="packet:meeting-1", tenant_id="acme", target_space=public, proposal_id="packet-meeting-1-public", release_id="context-alpha-public@1", approvals=_approvals(public=True), rationale="Publish public decision.")
    released = service.promote(packet_id="packet:meeting-1", tenant_id="acme", target_space=public, proposal_id="packet-meeting-1-public", release_id="context-alpha-public@1", approvals=_approvals(public=True), rationale="Publish public decision.", has_public_consent=True)

    assert released.visibility == "public-governed"
    assert released.release_id == "context-alpha-public@1"
