import pytest

from bridge.context_exchange import (
    ApprovalDecision,
    PublicAssertionCandidate,
    PublicAssertionService,
    PublicKnowledgeQueryControl,
    PublicKnowledgeQueryError,
    PublicKnowledgeQueryService,
    PublicKnowledgeRevocationService,
    PublicSourceRecord,
    PublicSourceRights,
    SqlitePublicAssertionRepository,
    SqlitePublicKnowledgeRepository,
)
from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import SemanticGovernanceService, SignedPrincipalVerifier
from bridge.semantic_core.compilers import CompilerRegistry, SemanticBundleCompiler
from bridge.semantic_core.query_runs import HmacQueryEvidenceAttestor, SqliteQueryRunRepository


def _control(tmp_path) -> tuple[PublicKnowledgeQueryControl, SqliteQueryRunRepository]:
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    knowledge = SqlitePublicKnowledgeRepository(tmp_path / "knowledge.sqlite3")
    governance = SemanticGovernanceService(tmp_path / "governance", decision_store=decisions, compiler_registry=CompilerRegistry([SemanticBundleCompiler()]))
    assertions = PublicAssertionService(repository=SqlitePublicAssertionRepository(tmp_path / "candidates.sqlite3"), knowledge_repository=knowledge, governance=governance, decision_store=decisions)
    source = PublicSourceRecord(source_id="notice-1", source_url="https://public.example/notice-1", title="Notice", content="Launch is 2026-10-01.", observed_at="2026-08-24T00:00:00Z", rights=PublicSourceRights.create(license_id="CC-BY-4.0", redistribution="allowed"))
    candidate = PublicAssertionCandidate.create(candidate_id="candidate:launch-date", tenant_id="acme", submitted_by="alice", subject_key="product:launch-date", statement="Launch is 2026-10-01.", confidence=0.9, source=source)
    assertions.intake(candidate, actor="connector:public", rationale="Collected public notice.")
    approvals = [ApprovalDecision("external:privacy", "pat", "privacy-reviewer", "approved"), ApprovalDecision("external:domain", "dev", "domain-approver", "approved"), ApprovalDecision("external:public", "sue", "public-reviewer", "approved"), ApprovalDecision("external:publisher", "priya", "publisher", "approved")]
    assertions.publish(candidate_id=candidate.candidate_id, tenant_id="acme", proposal_id="public-launch-date", release_id="public-context@1", approvals=approvals, rationale="Verified public notice.")
    verifier = SignedPrincipalVerifier(key_id="test", secret=b"test-secret")
    runs = SqliteQueryRunRepository(tmp_path / "query-runs.sqlite3")
    control = PublicKnowledgeQueryControl(query_service=PublicKnowledgeQueryService(knowledge), verifier=verifier, decision_store=decisions, query_runs=runs, evidence_attestor=HmacQueryEvidenceAttestor(key_id="test:public", secret=b"public-secret"), allowed_purposes={"research"})
    return control, runs


def test_public_query_requires_signed_principal_and_persists_attested_evidence(tmp_path) -> None:
    control, runs = _control(tmp_path)
    headers = control.verifier.sign_headers(subject="reader", tenant_id="acme", roles=["viewer"])
    result = control.execute({"query_run_id": "public-query-001", "query": "launch", "purpose": "research", "rationale": "Public research."}, headers=headers)

    assert result["status"] == "succeeded"
    assert result["evidence_package"]["field_evidence"][0]["source_content_hash"].startswith("sha256:")
    assert runs.get("public-query-001", tenant_id="acme").attestation is not None


def test_disallowed_purpose_persists_a_failed_query_run(tmp_path) -> None:
    control, runs = _control(tmp_path)
    headers = control.verifier.sign_headers(subject="reader", tenant_id="acme", roles=["viewer"])
    with pytest.raises(PublicKnowledgeQueryError, match="purpose"):
        control.execute({"query_run_id": "public-query-002", "query": "launch", "purpose": "marketing", "rationale": "Not authorized."}, headers=headers)

    assert runs.get("public-query-002", tenant_id="acme").status == "failed"


def test_revocation_excludes_new_reads_and_makes_strict_replay_fail(tmp_path) -> None:
    control, runs = _control(tmp_path)
    headers = control.verifier.sign_headers(subject="reader", tenant_id="acme", roles=["viewer"])
    source = control.execute({"query_run_id": "public-query-003", "query": "launch", "purpose": "research", "rationale": "Public research."}, headers=headers)
    PublicKnowledgeRevocationService(repository=control.query_service.repository, decision_store=control.decision_store).revoke(tenant_id="acme", candidate_id="candidate:launch-date", actor="public-reviewer:sue", rationale="Source was withdrawn.")

    assert control.execute({"query_run_id": "public-query-004", "query": "launch", "purpose": "research", "rationale": "Current public research."}, headers=headers)["governed_result"]["result"]["records"] == []
    with pytest.raises(PublicKnowledgeQueryError, match="strict replay"):
        control.replay({"query_run_id": "public-query-005", "source_query_run_id": "public-query-003", "expected_source_digest": source["run_digest"], "rationale": "Verify historical result."}, headers=headers)

    assert runs.get("public-query-005", tenant_id="acme").status == "failed"
