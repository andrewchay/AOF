"""Deterministic v1 contracts for private-to-governed context exchange.

These are deliberately transport-neutral.  A packet may be made by MyContext
or another local-first client, but AOF only accepts the minimum, consented
payload and never needs access to the source vault.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from bridge.semantic_core.canonical import canonical_data, content_digest


class ContextExchangeError(ValueError):
    """Raised when a context packet or approval boundary is unsafe."""


class ContextVisibility(str, Enum):
    PRIVATE = "private"
    SHARED_DRAFT = "shared-draft"
    TENANT_GOVERNED = "tenant-governed"
    PUBLIC_GOVERNED = "public-governed"


_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_EXCHANGEABLE_CATEGORIES = frozenset({"decision", "document", "event", "fact", "public-source"})


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextExchangeError(f"{name} must be a non-empty string")
    return value.strip()


def _safe_id(value: str, name: str) -> str:
    normalized = _required(value, name)
    if not _SAFE_ID.fullmatch(normalized):
        raise ContextExchangeError(f"{name} must use safe lowercase characters")
    return normalized


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ContextSpace:
    """A disclosure boundary; it is not itself a permission grant."""

    space_id: str
    tenant_id: str
    visibility: ContextVisibility
    purpose: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        space_id: str,
        tenant_id: str,
        visibility: ContextVisibility | str,
        purpose: Iterable[str],
    ) -> "ContextSpace":
        try:
            normalized_visibility = ContextVisibility(visibility)
        except ValueError as exc:
            raise ContextExchangeError(f"unsupported context visibility: {visibility}") from exc
        normalized_purpose = tuple(sorted({_required(item, "purpose") for item in purpose}))
        if not normalized_purpose:
            raise ContextExchangeError("a context space requires at least one purpose")
        normalized_tenant = _safe_id(tenant_id, "tenant_id")
        # A public release is published *by* a tenant under an additional public
        # policy; treating "public" as a tenant would silently bypass tenancy.
        if normalized_tenant == "public":
            raise ContextExchangeError("public is a visibility, not a tenant_id")
        return cls(_safe_id(space_id, "space_id"), normalized_tenant, normalized_visibility, normalized_purpose)

    def to_dict(self) -> dict[str, Any]:
        return {"space_id": self.space_id, "tenant_id": self.tenant_id, "visibility": self.visibility.value, "purpose": list(self.purpose)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextSpace":
        if set(value) != {"space_id", "tenant_id", "visibility", "purpose"}:
            raise ContextExchangeError("context space fields do not match the v1 contract")
        return cls.create(
            space_id=str(value["space_id"]), tenant_id=str(value["tenant_id"]),
            visibility=str(value["visibility"]), purpose=value["purpose"],
        )


@dataclass(frozen=True)
class ContextAssertionEvidence:
    """A minimum-disclosure, replayable reference to source material."""

    evidence_id: str
    source_ref: str
    content_hash: str
    observed_at: str
    disclosure: str
    excerpt: str | None = None

    @classmethod
    def create(
        cls,
        *,
        evidence_id: str,
        source_ref: str,
        content_hash: str,
        observed_at: str,
        disclosure: str,
        excerpt: str | None = None,
    ) -> "ContextAssertionEvidence":
        if disclosure not in {"reference", "redacted-excerpt"}:
            raise ContextExchangeError("evidence disclosure must be reference or redacted-excerpt")
        if disclosure == "redacted-excerpt" and not (isinstance(excerpt, str) and excerpt.strip()):
            raise ContextExchangeError("redacted-excerpt evidence requires an excerpt")
        if disclosure == "reference" and excerpt is not None:
            raise ContextExchangeError("reference evidence must not carry source content")
        normalized_hash = _required(content_hash, "content_hash")
        if not normalized_hash.startswith("sha256:"):
            raise ContextExchangeError("content_hash must use sha256")
        return cls(
            _safe_id(evidence_id, "evidence_id"),
            _required(source_ref, "source_ref"),
            normalized_hash,
            _required(observed_at, "observed_at"),
            disclosure,
            excerpt.strip() if isinstance(excerpt, str) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_ref": self.source_ref,
            "content_hash": self.content_hash,
            "observed_at": self.observed_at,
            "disclosure": self.disclosure,
            "excerpt": self.excerpt,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextAssertionEvidence":
        if set(value) != {"evidence_id", "source_ref", "content_hash", "observed_at", "disclosure", "excerpt"}:
            raise ContextExchangeError("assertion evidence fields do not match the v1 contract")
        return cls.create(
            evidence_id=str(value["evidence_id"]), source_ref=str(value["source_ref"]),
            content_hash=str(value["content_hash"]), observed_at=str(value["observed_at"]),
            disclosure=str(value["disclosure"]), excerpt=value["excerpt"],
        )


@dataclass(frozen=True)
class ContextAssertion:
    assertion_id: str
    category: str
    statement: str
    confidence: float
    evidence: tuple[ContextAssertionEvidence, ...]
    valid_time: Mapping[str, Any]

    @classmethod
    def create(
        cls,
        *,
        assertion_id: str,
        category: str,
        statement: str,
        confidence: float,
        evidence: Iterable[ContextAssertionEvidence],
        valid_time: Mapping[str, Any] | None = None,
    ) -> "ContextAssertion":
        if category not in _EXCHANGEABLE_CATEGORIES:
            raise ContextExchangeError("assertion category is not exportable by the v1 default policy")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise ContextExchangeError("confidence must be between 0 and 1")
        normalized_evidence = tuple(sorted(evidence, key=lambda item: item.evidence_id))
        if not normalized_evidence:
            raise ContextExchangeError("an assertion requires evidence")
        if len({item.evidence_id for item in normalized_evidence}) != len(normalized_evidence):
            raise ContextExchangeError("assertion evidence ids must be unique")
        return cls(
            _safe_id(assertion_id, "assertion_id"),
            category,
            _required(statement, "statement"),
            float(confidence),
            normalized_evidence,
            _freeze(canonical_data(dict(valid_time or {}))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "category": self.category,
            "statement": self.statement,
            "confidence": self.confidence,
            "evidence": [item.to_dict() for item in self.evidence],
            "valid_time": canonical_data(self.valid_time),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextAssertion":
        if set(value) != {"assertion_id", "category", "statement", "confidence", "evidence", "valid_time"}:
            raise ContextExchangeError("assertion fields do not match the v1 contract")
        evidence = value["evidence"]
        if not isinstance(evidence, list):
            raise ContextExchangeError("assertion evidence must be a list")
        valid_time = value["valid_time"]
        if not isinstance(valid_time, Mapping):
            raise ContextExchangeError("assertion valid_time must be an object")
        return cls.create(
            assertion_id=str(value["assertion_id"]), category=str(value["category"]),
            statement=str(value["statement"]), confidence=value["confidence"],
            evidence=[ContextAssertionEvidence.from_dict(item) for item in evidence], valid_time=valid_time,
        )


@dataclass(frozen=True)
class ContextPacket:
    """A consented import proposal. Packets can only enter a quarantine space."""

    packet_id: str
    producer: str
    submitted_by: str
    target_space: ContextSpace
    assertions: tuple[ContextAssertion, ...]
    consent_decision_id: str
    consented_purpose: tuple[str, ...]
    consent_expires_at: str
    packet_digest: str

    @classmethod
    def create(
        cls,
        *,
        packet_id: str,
        producer: str,
        submitted_by: str,
        target_space: ContextSpace,
        assertions: Iterable[ContextAssertion],
        consent_decision_id: str,
        consented_purpose: Iterable[str],
        consent_expires_at: str,
    ) -> "ContextPacket":
        if target_space.visibility is not ContextVisibility.SHARED_DRAFT:
            raise ContextExchangeError("packets must target a shared-draft quarantine space")
        normalized_assertions = tuple(sorted(assertions, key=lambda item: item.assertion_id))
        if not normalized_assertions:
            raise ContextExchangeError("a packet requires at least one assertion")
        if len({item.assertion_id for item in normalized_assertions}) != len(normalized_assertions):
            raise ContextExchangeError("packet assertion ids must be unique")
        normalized_purpose = tuple(sorted({_required(item, "consented_purpose") for item in consented_purpose}))
        if not normalized_purpose:
            raise ContextExchangeError("a packet requires consented purpose")
        if not set(normalized_purpose).issubset(target_space.purpose):
            raise ContextExchangeError("consented purpose must be within the target space purpose")
        payload = {
            "api_version": "aof.context-packet/v1",
            "packet_id": _safe_id(packet_id, "packet_id"),
            "producer": _required(producer, "producer"),
            "submitted_by": _required(submitted_by, "submitted_by"),
            "target_space": target_space.to_dict(),
            "assertions": [item.to_dict() for item in normalized_assertions],
            "consent_decision_id": _required(consent_decision_id, "consent_decision_id"),
            "consented_purpose": list(normalized_purpose),
            "consent_expires_at": _required(consent_expires_at, "consent_expires_at"),
        }
        return cls(
            packet_id=payload["packet_id"], producer=payload["producer"], submitted_by=payload["submitted_by"],
            target_space=target_space, assertions=normalized_assertions, consent_decision_id=payload["consent_decision_id"],
            consented_purpose=normalized_purpose, consent_expires_at=payload["consent_expires_at"],
            packet_digest=content_digest(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "api_version": "aof.context-packet/v1", "packet_id": self.packet_id, "producer": self.producer,
            "submitted_by": self.submitted_by, "target_space": self.target_space.to_dict(),
            "assertions": [item.to_dict() for item in self.assertions], "consent_decision_id": self.consent_decision_id,
            "consented_purpose": list(self.consented_purpose), "consent_expires_at": self.consent_expires_at,
        }
        return {**payload, "packet_digest": self.packet_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextPacket":
        fields = {
            "api_version", "packet_id", "producer", "submitted_by", "target_space", "assertions",
            "consent_decision_id", "consented_purpose", "consent_expires_at", "packet_digest",
        }
        if set(value) != fields or value.get("api_version") != "aof.context-packet/v1":
            raise ContextExchangeError("packet fields do not match the v1 contract")
        target_space = value["target_space"]
        assertions = value["assertions"]
        if not isinstance(target_space, Mapping) or not isinstance(assertions, list):
            raise ContextExchangeError("packet target_space and assertions have invalid types")
        packet = cls.create(
            packet_id=str(value["packet_id"]), producer=str(value["producer"]),
            submitted_by=str(value["submitted_by"]), target_space=ContextSpace.from_dict(target_space),
            assertions=[ContextAssertion.from_dict(item) for item in assertions],
            consent_decision_id=str(value["consent_decision_id"]), consented_purpose=value["consented_purpose"],
            consent_expires_at=str(value["consent_expires_at"]),
        )
        if value["packet_digest"] != packet.packet_digest:
            raise ContextExchangeError("packet_digest does not match packet content")
        return packet


@dataclass(frozen=True)
class ApprovalDecision:
    decision_id: str
    actor: str
    role: str
    conclusion: str


def required_approval_roles(target: ContextVisibility) -> frozenset[str]:
    if target is ContextVisibility.TENANT_GOVERNED:
        return frozenset({"privacy-reviewer", "domain-approver", "publisher"})
    if target is ContextVisibility.PUBLIC_GOVERNED:
        return frozenset({"privacy-reviewer", "domain-approver", "public-reviewer", "publisher"})
    raise ContextExchangeError("only governed spaces have a publication approval matrix")


def validate_transition_approvals(
    *,
    source: ContextSpace,
    target: ContextSpace,
    submitted_by: str,
    approvals: Iterable[ApprovalDecision],
    has_public_consent: bool = False,
) -> None:
    """Validate the v1 separation-of-duties matrix before a later publish step."""

    if source.tenant_id != target.tenant_id:
        raise ContextExchangeError("context promotion cannot cross tenants")
    if target.visibility not in {ContextVisibility.TENANT_GOVERNED, ContextVisibility.PUBLIC_GOVERNED}:
        raise ContextExchangeError("target must be a governed context space")
    expected_source = (
        ContextVisibility.SHARED_DRAFT
        if target.visibility is ContextVisibility.TENANT_GOVERNED
        else ContextVisibility.TENANT_GOVERNED
    )
    if source.visibility is not expected_source:
        raise ContextExchangeError(
            f"{target.visibility.value} promotion requires {expected_source.value} source content"
        )
    if target.visibility is ContextVisibility.PUBLIC_GOVERNED and not has_public_consent:
        raise ContextExchangeError("public-governed promotion requires newly granted public consent")
    approved = {item.role: item for item in approvals if item.conclusion == "approved"}
    missing = required_approval_roles(target.visibility) - set(approved)
    if missing:
        raise ContextExchangeError(f"missing required approvals: {', '.join(sorted(missing))}")
    publisher = approved["publisher"]
    if publisher.actor == submitted_by:
        raise ContextExchangeError("submitter cannot be the final publisher")
