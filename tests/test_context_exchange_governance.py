"""W03.03 — Context Exchange governance flow, end to end.

Acceptance: 私有导出 → 可信tenant路由 → quarantine → 独立批准 → 发布 →
受限查询；未知来源默认私有。

Flow exercised:
1. private export: a source without a matching tenant route CANNOT be
   exported (default private)
2. trusted tenant routing: share-eligible routes nominate ONE draft space
   with intersecting purposes
3. quarantine: the submitted packet lands in shared-draft quarantine with
   an immutable decision receipt (never a query source directly)
4. independent approval: privacy-reviewer + domain-approver + publisher
   all required; submitter cannot be the final publisher
5. publication: promotion drives the AOF release governance pipeline and
   records an immutable publication
6. restricted query: the publication is retrievable only within the
   tenant, bound to release id + digest
"""

from __future__ import annotations


import pytest

from bridge.context_exchange import (
    ApprovalDecision,
    ContextExchangeError,
    ContextPromotionService,
    ContextSpace,
    ContextVisibility,
    MyContextSubmissionService,
    SqliteContextPacketRepository,
    TenantContextPolicy,
    minimal_evidence,
)
from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    SemanticGovernancePolicy,
    SemanticGovernanceService,
    SqliteReleaseRepository,
)


def _export_bundle(*, export_id: str, source_ref: str) -> dict:
    assertion = {
        "assertion_id": "assertion-001",
        "category": "decision",
        "statement": "ACME 2026-09 revenue is 80 against a 100 budget",
        "confidence": 0.9,
        "evidence": [minimal_evidence(
            evidence_id="ev-001",
            source_ref=source_ref,
            content_hash="sha256:" + "a" * 64,
            observed_at="2026-09-01T00:00:00+00:00",
            redacted_excerpt="revenue deviation reviewed",
        ).to_dict()],
        "valid_time": {"start": "2026-09-01", "end": "2026-09-30"},
    }
    return {
        "api_version": "mycontext.context-export/v1",
        "export_id": export_id,
        "submitted_by": "analyst:wang",
        "consent_decision_id": "consent:001",
        "consented_purpose": ["fraud-review"],
        "consent_expires_at": "2027-01-01T00:00:00+00:00",
        "assertions": [assertion],
    }


def _tenant_policy() -> TenantContextPolicy:
    return TenantContextPolicy.from_dict({
        "tenant_id": "acme",
        "spaces": {
            "fraud-draft": {
                "draft_space_id": "space:acme:fraud-draft",
                "allowed_purposes": ["fraud-review", "risk-report"],
            },
        },
        "source_routes": [
            {
                "source_prefix": "db:acme-finance:",
                "disposition": "share-eligible",
                "draft_space": "fraud-draft",
                "allowed_purposes": ["fraud-review"],
                "sensitivity_labels": ["finance"],
            },
            {
                "source_prefix": "vault:private:",
                "disposition": "private",
                "draft_space": None,
                "allowed_purposes": [],
                "sensitivity_labels": [],
            },
        ],
    })


def _services(tmp_path, monkeypatch):
    # promotion records decision-chain receipts; jsonl backend keeps the
    # decision store file-based for this suite. monkeypatch restores env.
    monkeypatch.setenv("AOF_DECISION_LEDGER_BACKEND", "jsonl")
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    repository = SqliteContextPacketRepository(tmp_path / "packets.sqlite3")
    from bridge.semantic_core.compilers import default_compiler_registry

    governance = SemanticGovernanceService(
        tmp_path / "governance",
        compiler_registry=default_compiler_registry(),
        release_repository=SqliteReleaseRepository(tmp_path / "releases.sqlite3"),
        access_policy=SemanticGovernancePolicy(),
    )
    submission = MyContextSubmissionService(repository=repository, decision_store=decisions)
    promotion = ContextPromotionService(
        repository=repository, governance=governance, decision_store=decisions
    )
    return submission, promotion, repository, decisions


# ---------------------------------------------------------------------------
# 1+2. 未知来源默认私有；可信路由
# ---------------------------------------------------------------------------


def test_unknown_source_is_private_by_default(tmp_path, monkeypatch):
    submission, _promotion, _repository, _decisions = _services(tmp_path, monkeypatch)
    policy = _tenant_policy()

    # db:unknown-* has no matching route -> PRIVATE, cannot export
    with pytest.raises(ContextExchangeError, match="private or denied"):
        submission.submit_routed(
            _export_bundle(export_id="exp-unknown", source_ref="db:unknown-ledger:tx/1"),
            policy=policy, actor="analyst:wang", rationale="try unknown source",
        )


def test_denied_source_cannot_export(tmp_path, monkeypatch):
    submission, _promotion, _repository, _decisions = _services(tmp_path, monkeypatch)
    policy = _tenant_policy()

    with pytest.raises(ContextExchangeError, match="private or denied"):
        submission.submit_routed(
            _export_bundle(export_id="exp-denied", source_ref="vault:private:session/7"),
            policy=policy, actor="analyst:wang", rationale="try private source",
        )


def test_share_eligible_route_selects_draft_space_and_purpose(tmp_path, monkeypatch):
    policy = _tenant_policy()
    route = policy.route_export(
        # need a bundle-like object: use the real exporter
        __import__("bridge.context_exchange", fromlist=["MyContextExportBundle"])
        .MyContextExportBundle.from_dict(
            _export_bundle(export_id="exp-route", source_ref="db:acme-finance:ledger/2026-09")
        )
    )
    assert route.draft_space_id == "space:acme:fraud-draft"
    assert route.allowed_purposes == ("fraud-review",)
    assert route.matched_source_prefixes == ("db:acme-finance:",)


# ---------------------------------------------------------------------------
# 3. quarantine
# ---------------------------------------------------------------------------


def test_submission_quarantines_with_receipt(tmp_path, monkeypatch):
    submission, _promotion, repository, decisions = _services(tmp_path, monkeypatch)
    policy = _tenant_policy()

    receipt = submission.submit_routed(
        _export_bundle(export_id="exp-001", source_ref="db:acme-finance:ledger/2026-09"),
        policy=policy, actor="gateway:ingress", rationale="consented export",
    )

    assert receipt.status == "quarantined"
    assert receipt.packet_id == "packet:exp-001"
    # quarantine receipt is an auditable decision
    stored = decisions.get(receipt.receipt_decision_id)
    assert stored is not None
    assert stored["decision"]["decision_type"] == "context_packet_quarantined"
    # quarantined packet is retrievable for review within the tenant
    quarantined = repository.get("packet:exp-001", tenant_id="acme")
    assert quarantined is not None
    assert quarantined.packet.target_space.visibility is ContextVisibility.SHARED_DRAFT


def test_quarantine_idempotent_and_tamper_proof(tmp_path, monkeypatch):
    submission, _promotion, repository, _decisions = _services(tmp_path, monkeypatch)
    policy = _tenant_policy()
    export = _export_bundle(export_id="exp-idem", source_ref="db:acme-finance:ledger/x")

    first = submission.submit_routed(export, policy=policy, actor="gateway:ingress", rationale="r")
    second = submission.submit_routed(export, policy=policy, actor="gateway:ingress", rationale="r")
    assert first.packet_digest == second.packet_digest

    # same packet_id with different content must be rejected
    tampered = _export_bundle(export_id="exp-idem", source_ref="db:acme-finance:ledger/y")
    with pytest.raises(ContextExchangeError, match="cannot be overwritten"):
        submission.submit_routed(tampered, policy=policy, actor="gateway:ingress", rationale="r")


# ---------------------------------------------------------------------------
# 4+5. 独立批准 → 发布
# ---------------------------------------------------------------------------


def _approved_promotion(tmp_path, monkeypatch):
    submission, promotion, repository, _decisions = _services(tmp_path, monkeypatch)
    policy = _tenant_policy()
    submission.submit_routed(
        _export_bundle(export_id="exp-promote", source_ref="db:acme-finance:ledger/2026-09"),
        policy=policy, actor="gateway:ingress", rationale="consented export",
    )
    target = ContextSpace.create(
        space_id="space:acme:fraud-governed",
        tenant_id="acme",
        visibility=ContextVisibility.TENANT_GOVERNED,
        purpose=("fraud-review",),
    )
    return promotion, repository, target


def test_promotion_requires_full_approval_matrix(tmp_path, monkeypatch):
    promotion, _repository, target = _approved_promotion(tmp_path, monkeypatch)

    approvals = [
        ApprovalDecision(decision_id="apr-1", actor="li:privacy", role="privacy-reviewer", conclusion="approved"),
        # domain-approver missing
        ApprovalDecision(decision_id="apr-2", actor="zhao:pub", role="publisher", conclusion="approved"),
    ]
    with pytest.raises(ContextExchangeError, match="missing required approvals"):
        promotion.promote(
            packet_id="packet:exp-promote", tenant_id="acme", target_space=target,
            proposal_id="ctx-proposal-1", release_id="ctx@1.0.0",
            approvals=approvals, rationale="incomplete matrix",
        )


def test_submitter_cannot_be_publisher(tmp_path, monkeypatch):
    promotion, _repository, target = _approved_promotion(tmp_path, monkeypatch)

    approvals = [
        ApprovalDecision(decision_id="apr-1", actor="li:privacy", role="privacy-reviewer", conclusion="approved"),
        ApprovalDecision(decision_id="apr-2", actor="wang:domain", role="domain-approver", conclusion="approved"),
        # submitted_by was "analyst:wang" -> same actor cannot publish
        ApprovalDecision(decision_id="apr-3", actor="analyst:wang", role="publisher", conclusion="approved"),
    ]
    with pytest.raises(ContextExchangeError, match="submitter cannot be the final publisher"):
        promotion.promote(
            packet_id="packet:exp-promote", tenant_id="acme", target_space=target,
            proposal_id="ctx-proposal-2", release_id="ctx@1.0.0",
            approvals=approvals, rationale="self publishing attempt",
        )


def test_full_approval_promotes_to_governed_release(tmp_path, monkeypatch):
    promotion, repository, target = _approved_promotion(tmp_path, monkeypatch)

    approvals = [
        ApprovalDecision(decision_id="apr-1", actor="li:privacy", role="privacy-reviewer", conclusion="approved"),
        ApprovalDecision(decision_id="apr-2", actor="chen:domain", role="domain-approver", conclusion="approved"),
        ApprovalDecision(decision_id="apr-3", actor="zhao:pub", role="publisher", conclusion="approved"),
    ]
    publication = promotion.promote(
        packet_id="packet:exp-promote", tenant_id="acme", target_space=target,
        proposal_id="ctx-proposal-3", release_id="ctx@1.0.0",
        approvals=approvals, rationale="complete independent review",
    )

    assert publication.visibility == ContextVisibility.TENANT_GOVERNED.value
    assert publication.release_id == "ctx@1.0.0"
    assert publication.release_digest
    # the publication is durable and retrievable within the tenant
    stored = repository.get_publication(
        "packet:exp-promote", tenant_id="acme", visibility=ContextVisibility.TENANT_GOVERNED.value
    )
    assert stored is not None
    assert stored.release_digest == publication.release_digest


def test_cross_tenant_publication_rejected(tmp_path, monkeypatch):
    promotion, _repository, target = _approved_promotion(tmp_path, monkeypatch)
    foreign = ContextSpace.create(
        space_id="space:other:fraud-governed",
        tenant_id="other-tenant",
        visibility=ContextVisibility.TENANT_GOVERNED,
        purpose=("fraud-review",),
    )
    approvals = [
        ApprovalDecision(decision_id="apr-1", actor="li:privacy", role="privacy-reviewer", conclusion="approved"),
        ApprovalDecision(decision_id="apr-2", actor="chen:domain", role="domain-approver", conclusion="approved"),
        ApprovalDecision(decision_id="apr-3", actor="zhao:pub", role="publisher", conclusion="approved"),
    ]
    with pytest.raises(ContextExchangeError):
        promotion.promote(
            packet_id="packet:exp-promote", tenant_id="acme", target_space=foreign,
            proposal_id="ctx-proposal-4", release_id="ctx@1.0.0",
            approvals=approvals, rationale="cross tenant attempt",
        )


# ---------------------------------------------------------------------------
# 6. 受限查询：发布仅租户内可查
# ---------------------------------------------------------------------------


def test_publication_query_is_tenant_scoped(tmp_path, monkeypatch):
    promotion, repository, target = _approved_promotion(tmp_path, monkeypatch)
    approvals = [
        ApprovalDecision(decision_id="apr-1", actor="li:privacy", role="privacy-reviewer", conclusion="approved"),
        ApprovalDecision(decision_id="apr-2", actor="chen:domain", role="domain-approver", conclusion="approved"),
        ApprovalDecision(decision_id="apr-3", actor="zhao:pub", role="publisher", conclusion="approved"),
    ]
    promotion.promote(
        packet_id="packet:exp-promote", tenant_id="acme", target_space=target,
        proposal_id="ctx-proposal-5", release_id="ctx@1.0.0",
        approvals=approvals, rationale="ok",
    )

    # same tenant: visible
    assert repository.get_publication(
        "packet:exp-promote", tenant_id="acme", visibility="tenant-governed"
    ) is not None
    # other tenant: NOT visible (tenant isolation on the publication ledger)
    assert repository.get_publication(
        "packet:exp-promote", tenant_id="other-tenant", visibility="tenant-governed"
    ) is None
    # un-governed visibility level: not queryable
    assert repository.get_publication(
        "packet:exp-promote", tenant_id="acme", visibility="public-governed"
    ) is None
