import json

import pytest

from bridge.context_exchange import (
    ContextSpace,
    MyContextExportBundle,
    MyContextExportError,
    minimal_evidence,
)


def _export() -> dict:
    evidence = minimal_evidence(
        evidence_id="evidence:meeting-1",
        source_ref="local:minutes:meeting-1",
        content_hash="sha256:abc",
        observed_at="2026-08-24T00:00:00Z",
        redacted_excerpt="Launch date approved.",
    )
    return {
        "api_version": "mycontext.context-export/v1",
        "export_id": "meeting-1",
        "submitted_by": "alice",
        "consent_decision_id": "decision:local-consent-1",
        "consented_purpose": ["project-delivery"],
        "consent_expires_at": "2026-12-31T00:00:00Z",
        "assertions": [
            {
                "assertion_id": "assertion:launch-date",
                "category": "decision",
                "statement": "The launch date is approved.",
                "confidence": 0.9,
                "evidence": [evidence.to_dict()],
                "valid_time": {},
            }
        ],
    }


def test_read_only_export_generates_a_minimum_disclosure_context_packet() -> None:
    bundle = MyContextExportBundle.from_json(json.dumps(_export()))
    draft = ContextSpace.create(space_id="project-alpha-draft", tenant_id="acme", visibility="shared-draft", purpose=["project-delivery"])
    packet = bundle.to_context_packet(target_space=draft)

    assert packet.packet_id == "packet:meeting-1"
    assert packet.producer == "mycontext"
    assert packet.assertions[0].evidence[0].excerpt == "Launch date approved."


def test_export_rejects_persona_and_raw_payload_shapes() -> None:
    payload = _export()
    payload["assertions"][0]["category"] = "persona"
    with pytest.raises(MyContextExportError, match="not exportable"):
        MyContextExportBundle.from_dict(payload)

    payload = _export()
    payload["vault_path"] = "/private/user/mycontext.sqlite"
    with pytest.raises(MyContextExportError, match="fields"):
        MyContextExportBundle.from_dict(payload)
