"""Canonical resource model for the AOF Semantic IR."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .canonical import canonical_data, content_digest


class SemanticModelError(ValueError):
    """Raised when a semantic resource violates the public IR contract."""


class ResourceKind(str, Enum):
    ONTOLOGY = "Ontology"
    VOCABULARY = "Vocabulary"
    CONCEPT = "Concept"
    OBJECT_TYPE = "ObjectType"
    PROPERTY_TYPE = "PropertyType"
    RELATION_TYPE = "RelationType"
    METRIC = "Metric"
    DIMENSION = "Dimension"
    DATA_SOURCE = "DataSource"
    PHYSICAL_DATASET = "PhysicalDataset"
    LOGICAL_DATASET = "LogicalDataset"
    FIELD_BINDING = "FieldBinding"
    RELATION_BINDING = "RelationBinding"
    TRANSFORM = "Transform"
    RULE_SET = "RuleSet"
    CONSTRAINT_SET = "ConstraintSet"
    QUERY_TEMPLATE = "QueryTemplate"
    QUERY_CONTRACT = "QueryContract"
    RETRIEVAL_PROFILE = "RetrievalProfile"
    FUNCTION = "Function"
    ACTION_TYPE = "ActionType"
    WORKFLOW = "Workflow"
    POLICY = "Policy"


_KIND_SEGMENTS = {
    kind: re.sub(r"(?<!^)(?=[A-Z])", "-", kind.value).lower() for kind in ResourceKind
}
_RESOURCE_ID = re.compile(
    r"^aof://(?P<tenant>[a-z0-9][a-z0-9._-]*)/(?P<domain>[a-z0-9][a-z0-9._-]*)/"
    r"(?P<kind>[a-z0-9][a-z0-9-]*)/(?P<name>[a-z0-9][a-z0-9._-]*)$"
)


def _kind(value: ResourceKind | str) -> ResourceKind:
    try:
        return value if isinstance(value, ResourceKind) else ResourceKind(value)
    except ValueError as exc:
        raise SemanticModelError(f"unsupported resource kind: {value}") from exc


def validate_resource_id(resource_id: str, *, kind: ResourceKind | None = None) -> None:
    match = _RESOURCE_ID.fullmatch(resource_id)
    if not match:
        raise SemanticModelError(
            "resource_id must match aof://{tenant}/{domain}/{kind}/{name} using lowercase safe segments"
        )
    if kind is not None and match.group("kind") != _KIND_SEGMENTS[kind]:
        raise SemanticModelError(
            f"resource_id kind segment must be '{_KIND_SEGMENTS[kind]}' for {kind.value}"
        )


def _unique_sorted(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    result = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise SemanticModelError(f"{field_name} must contain non-empty strings")
        result.append(value.strip())
    return tuple(sorted(set(result)))


def _normalize_evidence(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for value in values:
        item = canonical_data(dict(value))
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise SemanticModelError("evidence entries require a non-empty evidence_id")
        evidence_id = evidence_id.strip()
        item["evidence_id"] = evidence_id
        if evidence_id in by_id and by_id[evidence_id] != item:
            raise SemanticModelError(f"conflicting evidence entries: {evidence_id}")
        by_id[evidence_id] = item
    return [by_id[evidence_id] for evidence_id in sorted(by_id)]


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class SemanticResource:
    resource_id: str
    kind: ResourceKind
    name: str
    domain: str
    owner: str
    display_name: str | None
    description: str
    tags: tuple[str, ...]
    depends_on: tuple[str, ...]
    evidence: tuple[Mapping[str, Any], ...]
    security_policy: Mapping[str, Any]
    valid_time: Mapping[str, Any]
    spec: Mapping[str, Any]
    revision_id: str
    schema_version: str = "aof.semantic/v1"

    @classmethod
    def create(
        cls,
        *,
        resource_id: str,
        kind: ResourceKind | str,
        name: str,
        domain: str,
        owner: str,
        display_name: str | None = None,
        description: str = "",
        tags: Iterable[str] = (),
        depends_on: Iterable[str] = (),
        evidence: Iterable[Mapping[str, Any]] = (),
        security_policy: Mapping[str, Any] | None = None,
        valid_time: Mapping[str, Any] | None = None,
        spec: Mapping[str, Any] | None = None,
        schema_version: str = "aof.semantic/v1",
    ) -> "SemanticResource":
        normalized_kind = _kind(kind)
        validate_resource_id(resource_id, kind=normalized_kind)
        for field_name, value in {"name": name, "domain": domain, "owner": owner}.items():
            if not isinstance(value, str) or not value.strip():
                raise SemanticModelError(f"{field_name} must be a non-empty string")
        normalized_dependencies = _unique_sorted(depends_on, "depends_on")
        for dependency in normalized_dependencies:
            validate_resource_id(dependency)
            if dependency == resource_id:
                raise SemanticModelError("a semantic resource cannot depend on itself")
        payload = {
            "schema_version": schema_version,
            "resource_id": resource_id,
            "kind": normalized_kind.value,
            "name": name.strip(),
            "domain": domain.strip(),
            "owner": owner.strip(),
            "display_name": display_name.strip() if display_name else None,
            "description": description.strip(),
            "tags": list(_unique_sorted(tags, "tags")),
            "depends_on": list(normalized_dependencies),
            "evidence": _normalize_evidence(evidence),
            "security_policy": canonical_data(dict(security_policy or {})),
            "valid_time": canonical_data(dict(valid_time or {})),
            "spec": canonical_data(dict(spec or {})),
        }
        revision_id = content_digest(payload)
        return cls(
            resource_id=resource_id,
            kind=normalized_kind,
            name=payload["name"],
            domain=payload["domain"],
            owner=payload["owner"],
            display_name=payload["display_name"],
            description=payload["description"],
            tags=tuple(payload["tags"]),
            depends_on=tuple(payload["depends_on"]),
            evidence=tuple(_freeze(item) for item in payload["evidence"]),
            security_policy=_freeze(payload["security_policy"]),
            valid_time=_freeze(payload["valid_time"]),
            spec=_freeze(payload["spec"]),
            revision_id=revision_id,
            schema_version=schema_version,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SemanticResource":
        supplied_revision = value.get("revision_id")
        resource = cls.create(
            resource_id=str(value.get("resource_id", "")),
            kind=str(value.get("kind", "")),
            name=str(value.get("name", "")),
            domain=str(value.get("domain", "")),
            owner=str(value.get("owner", "")),
            display_name=value.get("display_name"),
            description=str(value.get("description", "")),
            tags=value.get("tags", ()),
            depends_on=value.get("depends_on", ()),
            evidence=value.get("evidence", ()),
            security_policy=value.get("security_policy", {}),
            valid_time=value.get("valid_time", {}),
            spec=value.get("spec", {}),
            schema_version=str(value.get("schema_version", "aof.semantic/v1")),
        )
        if supplied_revision is not None and supplied_revision != resource.revision_id:
            raise SemanticModelError("revision_id does not match canonical semantic content")
        return resource

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "resource_id": self.resource_id,
            "kind": self.kind.value,
            "name": self.name,
            "domain": self.domain,
            "owner": self.owner,
            "display_name": self.display_name,
            "description": self.description,
            "tags": list(self.tags),
            "depends_on": list(self.depends_on),
            "evidence": [canonical_data(item) for item in self.evidence],
            "security_policy": canonical_data(self.security_policy),
            "valid_time": canonical_data(self.valid_time),
            "spec": canonical_data(self.spec),
            "revision_id": self.revision_id,
        }
