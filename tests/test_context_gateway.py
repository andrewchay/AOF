import pytest

from bridge.context_exchange import (
    ContextAssertion,
    ContextAssertionEvidence,
    ContextGateway,
    ContextPacket,
    ContextSpace,
    QuarantinedPacket,
    SqliteContextPacketRepository,
)
from bridge.decision_provenance import DecisionProvenanceStore


def _packet() -> ContextPacket:
    space = ContextSpace.create(space_id="project-alpha-draft", tenant_id="acme", visibility="shared-draft", purpose=["project-delivery"])
    evidence = ContextAssertionEvidence.create(evidence_id="evidence:meeting-1", source_ref="local:minutes:meeting-1", content_hash="sha256:abc", observed_at="2026-08-24T00:00:00Z", disclosure="reference")
    assertion = ContextAssertion.create(assertion_id="assertion:launch-date", category="decision", statement="The launch date is approved.", confidence=0.9, evidence=[evidence])
    return ContextPacket.create(packet_id="packet:meeting-1", producer="mycontext", submitted_by="alice", target_space=space, assertions=[assertion], consent_decision_id="decision:consent-1", consented_purpose=["project-delivery"], consent_expires_at="2026-12-31T00:00:00Z")


def test_gateway_quarantines_with_a_tenant_scoped_audit_receipt(tmp_path) -> None:
    repository = SqliteContextPacketRepository(tmp_path / "packets.sqlite3")
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    result = ContextGateway(repository=repository, decision_store=decisions).receive(_packet(), tenant_id="acme", actor="gateway:ingress", rationale="Local consent has been verified by the producer.")

    assert repository.get("packet:meeting-1", tenant_id="acme") == result
    assert repository.get("packet:meeting-1", tenant_id="other") is None
    assert result.receipt_decision_id.startswith("decision:")
    entry = decisions.get(result.receipt_decision_id)
    assert entry["decision"]["tenant_id"] == "acme"
    assert entry["decision"]["metadata"]["consent_decision_id"] == "decision:consent-1"


def test_gateway_receipt_is_idempotent_but_rejects_tenant_mismatch(tmp_path) -> None:
    repository = SqliteContextPacketRepository(tmp_path / "packets.sqlite3")
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    gateway = ContextGateway(repository=repository, decision_store=decisions)
    first = gateway.receive(_packet(), tenant_id="acme", actor="gateway:ingress", rationale="consented")
    second = gateway.receive(_packet(), tenant_id="acme", actor="gateway:ingress", rationale="retry")

    assert second == first
    assert len(decisions._entries()) == 1
    from bridge.context_exchange import ContextExchangeError

    with pytest.raises(ContextExchangeError, match="receiving tenant"):
        gateway.receive(_packet(), tenant_id="other", actor="gateway:ingress", rationale="wrong tenant")


def test_stored_packet_round_trip_detects_tampering(tmp_path) -> None:
    repository = SqliteContextPacketRepository(tmp_path / "packets.sqlite3")
    packet = _packet()
    repository.put(QuarantinedPacket(packet=packet, received_at="2026-08-24T00:00:00Z", receipt_decision_id="decision:receipt"))
    with repository._connect() as connection:
        connection.execute("UPDATE quarantined_context_packets SET packet_digest = 'sha256:wrong'")

    from bridge.context_exchange import ContextExchangeError

    with pytest.raises(ContextExchangeError, match="integrity"):
        repository.get(packet.packet_id, tenant_id="acme")
