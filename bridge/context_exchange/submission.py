"""Controlled ingress for consented MyContext exports.

The caller supplies the authenticated receiving identity and tenant context;
the export itself is never trusted to choose either.  Successful submissions
are always quarantined in ``shared-draft`` and return the provenance receipt
created by :class:`ContextGateway`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import ContextExchangeError, ContextSpace, ContextVisibility
from .gateway import ContextGateway, SqliteContextPacketRepository
from .mycontext_exporter import MyContextExportBundle
from .tenant_policy import TenantContextPolicy
from bridge.decision_provenance import DecisionProvenanceStore


def _future_timestamp(value: str) -> None:
    """Reject expired or timezone-less consent before anything is persisted."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextExchangeError("consent_expires_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContextExchangeError("consent_expires_at must include a timezone")
    if parsed <= datetime.now(timezone.utc):
        raise ContextExchangeError("consent has expired")


@dataclass(frozen=True)
class ContextSubmissionReceipt:
    """The stable, minimum receipt returned to the submitting client."""

    packet_id: str
    packet_digest: str
    receipt_decision_id: str
    status: str
    target_space: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "packet_digest": self.packet_digest,
            "receipt_decision_id": self.receipt_decision_id,
            "status": self.status,
            "target_space": dict(self.target_space),
        }


class MyContextSubmissionService:
    """Accept an explicit export envelope through AOF's trusted ingress."""

    def __init__(self, *, repository: SqliteContextPacketRepository, decision_store: DecisionProvenanceStore) -> None:
        self._gateway = ContextGateway(repository=repository, decision_store=decision_store)

    def submit(
        self,
        export: Mapping[str, Any],
        *,
        tenant_id: str,
        actor: str,
        rationale: str,
        draft_space_id: str,
        allowed_purpose: tuple[str, ...],
    ) -> ContextSubmissionReceipt:
        """Convert, verify and quarantine one local export.

        ``tenant_id`` and ``actor`` must come from the authenticated AOF
        session or trusted host integration, not fields supplied by MyContext.
        """

        if not isinstance(export, Mapping):
            raise ContextExchangeError("context export must be an object")
        bundle = MyContextExportBundle.from_dict(export)
        _future_timestamp(bundle.consent_expires_at)
        target = ContextSpace.create(
            space_id=draft_space_id,
            tenant_id=tenant_id,
            visibility=ContextVisibility.SHARED_DRAFT,
            purpose=allowed_purpose,
        )
        packet = bundle.to_context_packet(target_space=target)
        quarantined = self._gateway.receive(packet, tenant_id=tenant_id, actor=actor, rationale=rationale)
        return ContextSubmissionReceipt(
            packet_id=packet.packet_id,
            packet_digest=packet.packet_digest,
            receipt_decision_id=quarantined.receipt_decision_id,
            status="quarantined",
            target_space=packet.target_space.to_dict(),
        )

    def submit_routed(
        self,
        export: Mapping[str, Any],
        *,
        policy: TenantContextPolicy,
        actor: str,
        rationale: str,
    ) -> ContextSubmissionReceipt:
        """Submit only when every selected evidence source is share-eligible."""

        bundle = MyContextExportBundle.from_dict(export)
        route = policy.route_export(bundle)
        return self.submit(
            export,
            tenant_id=policy.tenant_id,
            actor=actor,
            rationale=rationale,
            draft_space_id=route.draft_space_id,
            allowed_purpose=route.allowed_purposes,
        )
