import pytest

from bridge.context_exchange import (
    ApprovalDecision,
    ContextAssertion,
    ContextAssertionEvidence,
    ContextExchangeError,
    ContextPacket,
    ContextSpace,
    validate_transition_approvals,
)


def _draft() -> ContextSpace:
    return ContextSpace.create(space_id="project-alpha-draft", tenant_id="acme", visibility="shared-draft", purpose=["project-delivery"])


def _assertion() -> ContextAssertion:
    evidence = ContextAssertionEvidence.create(evidence_id="evidence:meeting-1", source_ref="local:minutes:meeting-1", content_hash="sha256:abc", observed_at="2026-08-24T00:00:00Z", disclosure="redacted-excerpt", excerpt="The launch date was approved.")
    return ContextAssertion.create(assertion_id="assertion:launch-date", category="decision", statement="The launch date is approved.", confidence=0.9, evidence=[evidence])


def test_packet_is_canonical_and_requires_draft_quarantine() -> None:
    packet = ContextPacket.create(packet_id="packet:meeting-1", producer="mycontext", submitted_by="alice", target_space=_draft(), assertions=[_assertion()], consent_decision_id="decision:consent-1", consented_purpose=["project-delivery"], consent_expires_at="2026-12-31T00:00:00Z")

    assert packet.to_dict()["packet_digest"] == packet.packet_digest
    with pytest.raises(ContextExchangeError, match="shared-draft"):
        ContextPacket.create(packet_id="packet:meeting-2", producer="mycontext", submitted_by="alice", target_space=ContextSpace.create(space_id="project-alpha", tenant_id="acme", visibility="tenant-governed", purpose=["project-delivery"]), assertions=[_assertion()], consent_decision_id="decision:consent-1", consented_purpose=["project-delivery"], consent_expires_at="2026-12-31T00:00:00Z")


def test_packet_rejects_unconsented_purpose_and_private_profile_categories() -> None:
    with pytest.raises(ContextExchangeError, match="within the target"):
        ContextPacket.create(packet_id="packet:meeting-1", producer="mycontext", submitted_by="alice", target_space=_draft(), assertions=[_assertion()], consent_decision_id="decision:consent-1", consented_purpose=["sales"], consent_expires_at="2026-12-31T00:00:00Z")
    with pytest.raises(ContextExchangeError, match="not exportable"):
        ContextAssertion.create(assertion_id="assertion:tone", category="persona", statement="Uses short replies.", confidence=0.9, evidence=[_assertion().evidence[0]])


def test_evidence_never_allows_plain_source_content_under_reference_disclosure() -> None:
    with pytest.raises(ContextExchangeError, match="must not carry source content"):
        ContextAssertionEvidence.create(evidence_id="evidence:one", source_ref="local:message:one", content_hash="sha256:abc", observed_at="2026-08-24T00:00:00Z", disclosure="reference", excerpt="raw source")


def test_tenant_promotion_requires_independent_final_publisher() -> None:
    target = ContextSpace.create(space_id="project-alpha", tenant_id="acme", visibility="tenant-governed", purpose=["project-delivery"])
    approvals = [ApprovalDecision("decision:privacy", "pat", "privacy-reviewer", "approved"), ApprovalDecision("decision:domain", "dev", "domain-approver", "approved"), ApprovalDecision("decision:publish", "priya", "publisher", "approved")]
    validate_transition_approvals(source=_draft(), target=target, submitted_by="alice", approvals=approvals)
    approvals[-1] = ApprovalDecision("decision:publish", "alice", "publisher", "approved")
    with pytest.raises(ContextExchangeError, match="final publisher"):
        validate_transition_approvals(source=_draft(), target=target, submitted_by="alice", approvals=approvals)


def test_public_promotion_requires_fresh_public_consent_and_extra_review() -> None:
    source = ContextSpace.create(space_id="project-alpha", tenant_id="acme", visibility="tenant-governed", purpose=["project-delivery"])
    target = ContextSpace.create(space_id="project-alpha-public", tenant_id="acme", visibility="public-governed", purpose=["project-delivery"])
    approvals = [ApprovalDecision("decision:privacy", "pat", "privacy-reviewer", "approved"), ApprovalDecision("decision:domain", "dev", "domain-approver", "approved"), ApprovalDecision("decision:public", "sue", "public-reviewer", "approved"), ApprovalDecision("decision:publish", "priya", "publisher", "approved")]
    with pytest.raises(ContextExchangeError, match="public consent"):
        validate_transition_approvals(source=source, target=target, submitted_by="alice", approvals=approvals)
    validate_transition_approvals(source=source, target=target, submitted_by="alice", approvals=approvals, has_public_consent=True)
