"""Read-only adapter from a MyContext local export to an AOF ContextPacket.

This module deliberately accepts an explicit export envelope rather than a
MyContext SQLite path.  AOF must never gain implicit access to a personal vault.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import (
    ContextAssertion,
    ContextAssertionEvidence,
    ContextExchangeError,
    ContextPacket,
    ContextSpace,
)


class MyContextExportError(ContextExchangeError):
    """Raised when the producer export is not a minimum-disclosure v1 envelope."""


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MyContextExportError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class MyContextExportBundle:
    """A consented local export with no vault path and no raw-record payload."""

    export_id: str
    submitted_by: str
    consent_decision_id: str
    consented_purpose: tuple[str, ...]
    consent_expires_at: str
    assertions: tuple[ContextAssertion, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MyContextExportBundle":
        fields = {
            "api_version",
            "export_id",
            "submitted_by",
            "consent_decision_id",
            "consented_purpose",
            "consent_expires_at",
            "assertions",
        }
        if set(value) != fields or value.get("api_version") != "mycontext.context-export/v1":
            raise MyContextExportError("export fields do not match mycontext.context-export/v1")
        raw_assertions = value["assertions"]
        raw_purpose = value["consented_purpose"]
        if not isinstance(raw_assertions, list) or not isinstance(raw_purpose, list):
            raise MyContextExportError("export assertions and consented_purpose must be lists")
        try:
            assertions = tuple(ContextAssertion.from_dict(item) for item in raw_assertions)
        except ContextExchangeError as exc:
            raise MyContextExportError(str(exc)) from exc
        if not assertions:
            raise MyContextExportError("export requires at least one assertion")
        purpose = tuple(sorted({_required(item, "consented_purpose") for item in raw_purpose}))
        if not purpose:
            raise MyContextExportError("export requires consented purpose")
        return cls(
            export_id=_required(value["export_id"], "export_id"),
            submitted_by=_required(value["submitted_by"], "submitted_by"),
            consent_decision_id=_required(value["consent_decision_id"], "consent_decision_id"),
            consented_purpose=purpose,
            consent_expires_at=_required(value["consent_expires_at"], "consent_expires_at"),
            assertions=assertions,
        )

    @classmethod
    def from_json(cls, value: str) -> "MyContextExportBundle":
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MyContextExportError("export is not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise MyContextExportError("export must be a JSON object")
        return cls.from_dict(payload)

    def to_context_packet(self, *, target_space: ContextSpace) -> ContextPacket:
        return ContextPacket.create(
            packet_id=f"packet:{self.export_id}",
            producer="mycontext",
            submitted_by=self.submitted_by,
            target_space=target_space,
            assertions=self.assertions,
            consent_decision_id=self.consent_decision_id,
            consented_purpose=self.consented_purpose,
            consent_expires_at=self.consent_expires_at,
        )


def minimal_evidence(
    *,
    evidence_id: str,
    source_ref: str,
    content_hash: str,
    observed_at: str,
    redacted_excerpt: str | None = None,
) -> ContextAssertionEvidence:
    """Build the only two evidence forms AOF will accept from MyContext."""

    return ContextAssertionEvidence.create(
        evidence_id=evidence_id,
        source_ref=source_ref,
        content_hash=content_hash,
        observed_at=observed_at,
        disclosure="redacted-excerpt" if redacted_excerpt is not None else "reference",
        excerpt=redacted_excerpt,
    )
