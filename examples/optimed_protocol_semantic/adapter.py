"""Wrap an OptiMed semantic bundle without reinterpreting clinical meaning."""

from __future__ import annotations

import re
from typing import Any, Mapping

from bridge.semantic_core import ResourceKind, SemanticResource


OPTIMED_PROTOCOL_SEMANTIC_SCHEMA = "optimed.protocol-semantic/v1"


class OptiMedContractError(ValueError):
    """Raised when an OptiMed export cannot enter the generic AOF use case."""


def build_context_resource(
    payload: Mapping[str, Any],
    *,
    tenant_id: str,
    owner: str,
) -> SemanticResource:
    """Create an AOF resource that points to OptiMed's governed contract.

    AOF records the external revision, evidence envelope, security boundary, and
    generic resource lineage. It does not infer, approve, or project clinical
    concepts; those responsibilities remain with OptiMed.
    """

    if payload.get("schema_version") != OPTIMED_PROTOCOL_SEMANTIC_SCHEMA:
        raise OptiMedContractError("unsupported OptiMed semantic schema")
    study_id = str(payload.get("study_id") or "").strip()
    revision_id = str(payload.get("revision_id") or "").strip()
    if not study_id or not revision_id:
        raise OptiMedContractError("study_id and revision_id are required")
    entities = _objects(payload.get("entities"), "entities")
    relations = _objects(payload.get("relations"), "relations")
    approvals = _objects(payload.get("approvals", ()), "approvals")
    evidence = _objects(payload.get("evidence"), "evidence")
    normalized_evidence: list[dict[str, Any]] = []
    for item in evidence:
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            raise OptiMedContractError("OptiMed evidence requires evidence_id")
        normalized_evidence.append(
            {
                "evidence_id": evidence_id,
                "external_contract": OPTIMED_PROTOCOL_SEMANTIC_SCHEMA,
                "field_path": str(item.get("field_path") or ""),
                "support_status": str(item.get("support_status") or ""),
                "source_node_ids": list(item.get("source_node_ids") or []),
                "source_occurrence_ids": list(
                    item.get("source_occurrence_ids") or []
                ),
                "source_page": item.get("source_page"),
                "source_section": str(item.get("source_section") or ""),
            }
        )
    safe_study = re.sub(
        r"[^a-z0-9._-]+", "-", study_id.casefold()
    ).strip("-")
    if not safe_study:
        raise OptiMedContractError("study_id cannot form an AOF resource name")
    return SemanticResource.create(
        resource_id=(
            f"aof://{tenant_id}/clinical-protocol/context-assertion/{safe_study}"
        ),
        kind=ResourceKind.CONTEXT_ASSERTION,
        name=safe_study,
        domain="clinical-protocol",
        owner=owner,
        display_name=f"OptiMed Protocol Semantic IR: {study_id}",
        description=(
            "External clinical semantic bundle governed by OptiMed; AOF records "
            "generic lineage and release metadata only."
        ),
        tags=("optimed", "clinical-use-case", "external-governance"),
        evidence=normalized_evidence,
        security_policy={
            "authority": "OptiMed",
            "classification": "clinical-derived",
            "default_queryability": "blocked_until_aof_release",
        },
        spec={
            "external_contract": OPTIMED_PROTOCOL_SEMANTIC_SCHEMA,
            "external_revision_id": revision_id,
            "source_protocol_checksum": str(
                payload.get("source_protocol_checksum") or ""
            ),
            "profile_version": str(payload.get("profile_version") or ""),
            "entity_count": len(entities),
            "relation_count": len(relations),
            "approval_count": len(approvals),
            "clinical_authority": "OptiMed",
        },
    )


def _objects(value: object, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise OptiMedContractError(f"{field_name} must be an array")
    if any(not isinstance(item, Mapping) for item in value):
        raise OptiMedContractError(f"{field_name} must contain objects")
    return tuple(value)
