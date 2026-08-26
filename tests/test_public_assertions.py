import pytest

from bridge.context_exchange import (
    ApprovalDecision,
    PublicAssertionCandidate,
    PublicAssertionError,
    PublicAssertionService,
    PublicKnowledgeQueryService,
    PublicSourceRecord,
    PublicSourceRights,
    SqlitePublicAssertionRepository,
    SqlitePublicKnowledgeRepository,
)
from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import SemanticGovernanceService
from bridge.semantic_core.compilers import CompilerRegistry, SemanticBundleCompiler


def _candidate(*, candidate_id: str = "candidate:launch-date", statement: str = "The public launch date is 2026-10-01.", redistribution: str = "allowed") -> PublicAssertionCandidate:
    source = PublicSourceRecord(source_id="notice-1", source_url="https://public.example/notice-1", title="Notice", content="The public launch date is 2026-10-01.", observed_at="2026-08-24T00:00:00Z", rights=PublicSourceRights.create(license_id="CC-BY-4.0", redistribution=redistribution))
    return PublicAssertionCandidate.create(candidate_id=candidate_id, tenant_id="acme", submitted_by="alice", subject_key="product:launch-date", statement=statement, confidence=0.9, source=source)


def _service(tmp_path) -> PublicAssertionService:
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    governance = SemanticGovernanceService(tmp_path / "governance", decision_store=decisions, compiler_registry=CompilerRegistry([SemanticBundleCompiler()]))
    knowledge = SqlitePublicKnowledgeRepository(tmp_path / "knowledge.sqlite3")
    return PublicAssertionService(repository=SqlitePublicAssertionRepository(tmp_path / "candidates.sqlite3"), knowledge_repository=knowledge, governance=governance, decision_store=decisions)


def _approvals() -> list[ApprovalDecision]:
    return [ApprovalDecision("external:privacy", "pat", "privacy-reviewer", "approved"), ApprovalDecision("external:domain", "dev", "domain-approver", "approved"), ApprovalDecision("external:public", "sue", "public-reviewer", "approved"), ApprovalDecision("external:publisher", "priya", "publisher", "approved")]


def test_public_candidate_requires_rights_and_public_review_before_release(tmp_path) -> None:
    service = _service(tmp_path)
    service.intake(_candidate(), actor="connector:public", rationale="Evidence was collected.")
    published = service.publish(candidate_id="candidate:launch-date", tenant_id="acme", proposal_id="public-launch-date", release_id="public-context@1", approvals=_approvals(), rationale="Public source verified.")

    assert published["state"] == "published"
    assert published["release"]["scope"]["visibility"] == "public-governed"


def test_restricted_source_cannot_be_published(tmp_path) -> None:
    service = _service(tmp_path)
    service.intake(_candidate(redistribution="restricted"), actor="connector:public", rationale="Evidence was collected.")
    with pytest.raises(PublicAssertionError, match="allowed redistribution"):
        service.publish(candidate_id="candidate:launch-date", tenant_id="acme", proposal_id="public-launch-date", release_id="public-context@1", approvals=_approvals(), rationale="Public source verified.")


def test_public_query_returns_release_and_evidence_and_blocks_conflicts(tmp_path) -> None:
    service = _service(tmp_path)
    service.intake(_candidate(), actor="connector:public", rationale="Evidence was collected.")
    service.publish(candidate_id="candidate:launch-date", tenant_id="acme", proposal_id="public-launch-date", release_id="public-context@1", approvals=_approvals(), rationale="Public source verified.")
    results = PublicKnowledgeQueryService(service.knowledge_repository).search(tenant_id="acme", text="launch")

    assert results[0]["release_digest"].startswith("sha256:")
    assert results[0]["source_url"] == "https://public.example/notice-1"
    conflict = _candidate(candidate_id="candidate:launch-date-conflict", statement="The public launch date is 2026-11-01.")
    service.intake(conflict, actor="connector:public", rationale="New source evidence.")
    with pytest.raises(PublicAssertionError, match="conflicts"):
        service.publish(candidate_id=conflict.candidate_id, tenant_id="acme", proposal_id="public-launch-date-conflict", release_id="public-context@2", approvals=_approvals(), rationale="Conflicting source.")
