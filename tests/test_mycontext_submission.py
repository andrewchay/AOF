import pytest

from bridge.context_exchange import MyContextSubmissionService, SqliteContextPacketRepository
from bridge.context_exchange.contracts import ContextExchangeError
from bridge.decision_provenance import DecisionProvenanceStore


def _export(*, expires_at: str = "2030-12-31T00:00:00Z") -> dict:
    return {
        "api_version": "mycontext.context-export/v1",
        "export_id": "handoff-1",
        "submitted_by": "local-user",
        "consent_decision_id": "decision:local-consent-1",
        "consented_purpose": ["project-delivery"],
        "consent_expires_at": expires_at,
        "assertions": [{
            "assertion_id": "assertion:launch-date",
            "category": "decision",
            "statement": "Launch date approved.",
            "confidence": 0.9,
            "evidence": [{
                "evidence_id": "evidence:meeting-1",
                "source_ref": "local:minutes:meeting-1",
                "content_hash": "sha256:abc",
                "observed_at": "2026-08-24T00:00:00Z",
                "disclosure": "redacted-excerpt",
                "excerpt": "Launch date approved.",
            }],
            "valid_time": {},
        }],
    }


def _service(tmp_path):
    return MyContextSubmissionService(
        repository=SqliteContextPacketRepository(tmp_path / "packets.sqlite3"),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )


def test_submit_quarantines_mycontext_export_and_returns_auditable_receipt(tmp_path) -> None:
    receipt = _service(tmp_path).submit(
        _export(), tenant_id="acme", actor="session:acme:alice",
        rationale="User confirmed the redacted export.", draft_space_id="project-alpha-draft",
        allowed_purpose=("project-delivery",),
    )

    assert receipt.status == "quarantined"
    assert receipt.packet_id == "packet:handoff-1"
    assert receipt.receipt_decision_id.startswith("decision:")
    assert receipt.target_space["visibility"] == "shared-draft"


def test_submit_rejects_expired_consent_before_quarantine(tmp_path) -> None:
    with pytest.raises(ContextExchangeError, match="expired"):
        _service(tmp_path).submit(
            _export(expires_at="2020-01-01T00:00:00Z"), tenant_id="acme", actor="session:acme:alice",
            rationale="User confirmed.", draft_space_id="project-alpha-draft", allowed_purpose=("project-delivery",),
        )
