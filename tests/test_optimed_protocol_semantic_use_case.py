from __future__ import annotations

import pytest

from bridge.semantic_core import ResourceKind
from examples.optimed_protocol_semantic import (
    OptiMedContractError,
    build_context_resource,
)


def _payload() -> dict:
    return {
        "schema_version": "optimed.protocol-semantic/v1",
        "profile_version": "optimed.protocol-core/v1",
        "study_id": "STUDY-001",
        "source_protocol_checksum": "source-sha256",
        "revision_id": "optimed-revision-sha256",
        "evidence": [
            {
                "evidence_id": "optevd-001",
                "field_path": "endpoints[0]",
                "support_status": "supported",
                "source_node_ids": ["node-6"],
                "source_occurrence_ids": [],
                "source_page": 6,
                "source_section": "Endpoints",
            }
        ],
        "entities": [{"entity_id": "endpoint-001"}],
        "relations": [{"relation_id": "has-endpoint-001"}],
        "approvals": [{"decision_id": "decision-001"}],
    }


def test_builds_generic_context_resource_without_copying_clinical_logic() -> None:
    resource = build_context_resource(
        _payload(), tenant_id="acme", owner="clinical-data"
    )

    assert resource.kind is ResourceKind.CONTEXT_ASSERTION
    assert resource.resource_id == (
        "aof://acme/clinical-protocol/context-assertion/study-001"
    )
    assert resource.spec["clinical_authority"] == "OptiMed"
    assert resource.spec["external_revision_id"] == "optimed-revision-sha256"
    assert resource.spec["entity_count"] == 1
    assert resource.security_policy["default_queryability"] == (
        "blocked_until_aof_release"
    )
    assert resource.evidence[0]["field_path"] == "endpoints[0]"


def test_rejects_unknown_contracts_and_missing_evidence_identity() -> None:
    payload = _payload()
    payload["schema_version"] = "unknown/v1"
    with pytest.raises(OptiMedContractError, match="unsupported"):
        build_context_resource(payload, tenant_id="acme", owner="clinical-data")

    payload = _payload()
    payload["evidence"][0].pop("evidence_id")
    with pytest.raises(OptiMedContractError, match="evidence_id"):
        build_context_resource(payload, tenant_id="acme", owner="clinical-data")
