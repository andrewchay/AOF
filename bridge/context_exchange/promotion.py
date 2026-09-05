"""Promotion of reviewed quarantine context into immutable AOF releases."""

from __future__ import annotations

from collections.abc import Iterable

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import ResourceKind, SemanticGovernanceService, SemanticResource
from bridge.semantic_core.canonical import content_digest

from .contracts import (
    ApprovalDecision,
    ContextExchangeError,
    ContextSpace,
    ContextVisibility,
    required_approval_roles,
    validate_transition_approvals,
)
from .gateway import ContextPublication, SqliteContextPacketRepository


class ContextPromotionService:
    """Applies Context Exchange approval gates before AOF release governance."""

    def __init__(self, *, repository: SqliteContextPacketRepository, governance: SemanticGovernanceService, decision_store: DecisionProvenanceStore) -> None:
        self.repository = repository
        self.governance = governance
        self.decision_store = decision_store

    def promote(
        self,
        *,
        packet_id: str,
        tenant_id: str,
        target_space: ContextSpace,
        proposal_id: str,
        release_id: str,
        approvals: Iterable[ApprovalDecision],
        rationale: str,
        has_public_consent: bool = False,
    ) -> ContextPublication:
        packet_record = self.repository.get(packet_id, tenant_id=tenant_id)
        if packet_record is None:
            raise ContextExchangeError("quarantined packet not found")
        packet = packet_record.packet
        if target_space.tenant_id != tenant_id:
            raise ContextExchangeError("target space tenant does not match packet tenant")
        source, parent_release = self._source_for(packet_id, tenant_id, packet.target_space, target_space.visibility)
        approval_values = tuple(approvals)
        validate_transition_approvals(
            source=source, target=target_space, submitted_by=packet.submitted_by,
            approvals=approval_values, has_public_consent=has_public_consent,
        )
        review_ids = self._record_reviews(packet_id, tenant_id, packet_record.receipt_decision_id, target_space.visibility, approval_values, rationale)
        resources = _resources_for(packet, target_space)
        proposal = self.governance.create_proposal(
            proposal_id=proposal_id, release_id=release_id, resources=resources,
            actor=f"editor:{packet.submitted_by}", rationale=rationale, parent_release=parent_release,
            scope={"tenant_id": tenant_id, "context_space": target_space.space_id, "visibility": target_space.visibility.value, "purpose": list(target_space.purpose), "context_review_decision_ids": review_ids},
        )
        by_role = {item.role: item for item in approval_values if item.conclusion == "approved"}
        self.governance.validate(proposal["proposal_id"], actor=f"validator:{by_role['privacy-reviewer'].actor}")
        self.governance.approve(proposal["proposal_id"], actor=f"reviewer:{by_role['domain-approver'].actor}", rationale=rationale)
        self.governance.compile(proposal["proposal_id"], actor="compiler:context-gateway", targets=["semantic-json"])
        published = self.governance.publish(proposal["proposal_id"], actor=f"publisher:{by_role['publisher'].actor}")
        release = published["release"]
        return self.repository.put_publication(
            ContextPublication(packet_id, source.space_id, target_space.space_id, target_space.visibility.value, release["release_id"], release["release_digest"], published["publish_decision_id"]),
            tenant_id=tenant_id,
        )

    def _source_for(self, packet_id: str, tenant_id: str, draft: ContextSpace, target: ContextVisibility) -> tuple[ContextSpace, str | None]:
        if target is ContextVisibility.TENANT_GOVERNED:
            return draft, None
        if target is ContextVisibility.PUBLIC_GOVERNED:
            published = self.repository.get_publication(packet_id, tenant_id=tenant_id, visibility=ContextVisibility.TENANT_GOVERNED.value)
            if published is None:
                raise ContextExchangeError("public promotion requires an existing tenant-governed publication")
            source = ContextSpace.create(space_id=published.target_space, tenant_id=tenant_id, visibility="tenant-governed", purpose=draft.purpose)
            return source, published.release_id
        raise ContextExchangeError("target must be tenant-governed or public-governed")

    def _record_reviews(self, packet_id: str, tenant_id: str, receipt_id: str, target: ContextVisibility, approvals: tuple[ApprovalDecision, ...], rationale: str) -> list[str]:
        by_role = {item.role: item for item in approvals if item.conclusion == "approved"}
        parents = [receipt_id]
        decisions: list[str] = []
        for role in sorted(required_approval_roles(target)):
            approval = by_role[role]
            entry = self.decision_store.record(
                agent_id=f"{role}:{approval.actor}", decision_type=f"context_{role}_approval",
                conclusion=f"approved {packet_id} for {target.value}", rationale=rationale,
                parent_decision_ids=parents, tenant_id=tenant_id,
                evidence=[{"id": packet_id, "type": "context_packet"}],
                policies=["policy:context-exchange-v1"], tags=["context-exchange", "approval", role],
                metadata={"external_approval_id": approval.decision_id, "target_visibility": target.value},
            )
            decision_id = entry["decision"]["id"]
            decisions.append(decision_id)
            parents = [decision_id]
        return decisions


def _resources_for(packet, target_space: ContextSpace) -> tuple[SemanticResource, ...]:
    resources = []
    for assertion in packet.assertions:
        digest = content_digest({"packet": packet.packet_id, "assertion": assertion.assertion_id}).split(":", 1)[1][:20]
        name = f"ctx-{digest}"
        resources.append(
            SemanticResource.create(
                resource_id=f"aof://{target_space.tenant_id}/context/context-assertion/{name}",
                kind=ResourceKind.CONTEXT_ASSERTION, name=name, domain="context", owner=packet.submitted_by,
                description=assertion.statement, tags=["context-exchange", assertion.category, target_space.visibility.value],
                evidence=[item.to_dict() for item in assertion.evidence],
                security_policy={"visibility": target_space.visibility.value, "context_space": target_space.space_id},
                valid_time=assertion.valid_time,
                spec={"packet_id": packet.packet_id, "assertion_id": assertion.assertion_id, "category": assertion.category, "confidence": assertion.confidence, "purpose": list(target_space.purpose)},
            )
        )
    return tuple(resources)
