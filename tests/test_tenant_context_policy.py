import pytest

from bridge.context_exchange import MyContextExportBundle, MyContextSubmissionService, SqliteContextPacketRepository, TenantContextPolicy
from bridge.context_exchange.contracts import ContextExchangeError
from bridge.decision_provenance import DecisionProvenanceStore


def _policy() -> TenantContextPolicy:
    return TenantContextPolicy.from_dict({
        "tenant_id": "acme",
        "spaces": {"project-alpha": {"draft_space_id": "project-alpha-draft", "allowed_purposes": ["project-delivery", "delivery-support"]}},
        "source_routes": [
            {"source_prefix": "local:personal:", "disposition": "private", "draft_space": None, "allowed_purposes": [], "sensitivity_labels": ["personal"]},
            {"source_prefix": "sharepoint:/Projects/Alpha/", "disposition": "share-eligible", "draft_space": "project-alpha", "allowed_purposes": ["project-delivery"], "sensitivity_labels": []},
            {"source_prefix": "sharepoint:/HR/", "disposition": "deny", "draft_space": None, "allowed_purposes": [], "sensitivity_labels": ["employee-data"]},
        ],
    })


def _export_payload(source_ref: str, purpose: str = "project-delivery", export_id: str = "route-1") -> dict:
    return {
        "api_version": "mycontext.context-export/v1", "export_id": export_id, "submitted_by": "alice",
        "consent_decision_id": "decision:consent-1", "consented_purpose": [purpose], "consent_expires_at": "2030-01-01T00:00:00Z",
        "assertions": [{"assertion_id": "assertion:one", "category": "fact", "statement": "Approved summary.", "confidence": 0.8,
                        "evidence": [{"evidence_id": "evidence:one", "source_ref": source_ref, "content_hash": "sha256:abc", "observed_at": "2026-01-01T00:00:00Z", "disclosure": "reference", "excerpt": None}], "valid_time": {}}],
    }


def _export(source_ref: str, purpose: str = "project-delivery") -> MyContextExportBundle:
    return MyContextExportBundle.from_dict(_export_payload(source_ref, purpose))


def test_share_eligible_source_routes_to_its_configured_draft_space() -> None:
    routed = _policy().route_export(_export("sharepoint:/Projects/Alpha/plan.docx"))
    assert routed.draft_space_id == "project-alpha-draft"
    assert routed.allowed_purposes == ("project-delivery",)


@pytest.mark.parametrize("source_ref", ["local:personal:note-1", "sharepoint:/HR/review.docx", "unknown:document-1"])
def test_private_denied_and_unknown_sources_cannot_be_exported(source_ref: str) -> None:
    with pytest.raises(ContextExchangeError, match="private or denied"):
        _policy().route_export(_export(source_ref))


def test_route_rejects_consent_purpose_not_allowed_for_source() -> None:
    with pytest.raises(ContextExchangeError, match="consented purpose"):
        _policy().route_export(_export("sharepoint:/Projects/Alpha/plan.docx", "delivery-support"))


def test_yaml_policy_routes_a_submission_to_shared_draft(tmp_path) -> None:
    policy = TenantContextPolicy.from_yaml_file("config/context-policy/acme.example.yaml")
    service = MyContextSubmissionService(
        repository=SqliteContextPacketRepository(tmp_path / "packets.sqlite3"),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    receipt = service.submit_routed(
        _export_payload("sharepoint:/Projects/Alpha/plan.docx", export_id="route-2"),
        policy=policy, actor="session:acme:alice", rationale="User confirmed the selected redacted summary.",
    )
    assert receipt.status == "quarantined"
    assert receipt.target_space["space_id"] == "project-alpha-draft"
