"""Production lifecycle: rotate, restart, detect tampering, and restore evidence."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    LocalSigningKeyProvider,
    ProviderQueryEvidenceAttestor,
    QueryControlPlane,
    SignedPrincipalVerifier,
    SqliteQueryRunRepository,
)
from bridge.semantic_core.compilers import SqliteCompilationRunRepository
from tests.test_trusted_query_execution import _trusted_query_runtime


def _headers() -> dict[str, str]:
    return SignedPrincipalVerifier(
        key_id="identity-v1", secret=b"production-identity-secret"
    ).sign_headers(subject="alice", tenant_id="acme", roles=["analyst"])


def test_trusted_runtime_recovers_rotated_evidence_after_tampering(tmp_path) -> None:
    compiler_root = tmp_path / "compiler"
    _trusted_query_runtime(
        tmp_path,
        compiler_root=compiler_root,
        repository_factory=SqliteCompilationRunRepository,
    )
    compilation_repository = SqliteCompilationRunRepository(compiler_root / "acme")
    query_repository = SqliteQueryRunRepository(compiler_root / "query-runs.sqlite3")
    provider = LocalSigningKeyProvider(
        {"query-v1": b"query-evidence-secret-v1", "query-v2": b"query-evidence-secret-v2"},
        current_key_id="query-v1",
    )
    attestor = ProviderQueryEvidenceAttestor(provider)
    verifier = SignedPrincipalVerifier(
        key_id="identity-v1", secret=b"production-identity-secret"
    )
    control = QueryControlPlane(
        compiler_root,
        verifier=verifier,
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
        query_runs=query_repository,
        evidence_attestor=attestor,
        compilation_repository_factory=SqliteCompilationRunRepository,
    )
    request = {
        "channel": "production",
        "capability": "semantic_search",
        "query": "customer",
        "purpose": "support",
        "policy_resource_id": "aof://acme/platform/policy/query-trusted",
        "rationale": "Exercise the production evidence lifecycle.",
    }

    old = control.execute({**request, "query_run_id": "query-old"}, headers=_headers())
    provider.rotate("query-v2")
    new = control.execute({**request, "query_run_id": "query-new"}, headers=_headers())

    assert old["attestation"]["key_id"] == "query-v1"
    assert new["attestation"]["key_id"] == "query-v2"
    restarted = QueryControlPlane(
        compiler_root,
        verifier=verifier,
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
        query_runs=SqliteQueryRunRepository(compiler_root / "query-runs.sqlite3"),
        evidence_attestor=attestor,
        compilation_repository_factory=SqliteCompilationRunRepository,
    )
    assert restarted.get_run("query-old", headers=_headers())["run_digest"] == old["run_digest"]
    assert restarted.get_run("query-new", headers=_headers())["run_digest"] == new["run_digest"]

    persisted = query_repository.get("query-new", tenant_id="acme")
    assert persisted is not None
    with ThreadPoolExecutor(max_workers=8) as pool:
        writes = list(pool.map(lambda _: query_repository.put(persisted), range(32)))
    assert {item.run_digest for item in writes} == {persisted.run_digest}

    recovery_root = tmp_path / "recovery" / "compiler"
    compilation_repository.backup_to(recovery_root / "acme")
    query_repository.backup_to(recovery_root / "query-runs.sqlite3")

    artifact = next((compiler_root / "acme" / "artifacts").rglob("*.json"))
    artifact.write_text("{}", encoding="utf-8")
    with sqlite3.connect(compiler_root / "query-runs.sqlite3") as connection:
        connection.execute(
            "UPDATE query_runs SET run_digest = 'sha256:tampered' "
            "WHERE query_run_id = 'query-new'"
        )
    assert compilation_repository.verify_all()["valid"] is False
    assert query_repository.verify_all()["valid"] is False

    restored_compilation = SqliteCompilationRunRepository(recovery_root / "acme")
    restored_queries = SqliteQueryRunRepository(recovery_root / "query-runs.sqlite3")
    assert restored_compilation.verify_all()["valid"] is True
    assert restored_queries.verify_all()["valid"] is True
    recovered = QueryControlPlane(
        recovery_root,
        verifier=verifier,
        decision_store=DecisionProvenanceStore(tmp_path / "recovery-decisions.jsonl"),
        query_runs=restored_queries,
        evidence_attestor=attestor,
        compilation_repository_factory=SqliteCompilationRunRepository,
    )
    assert recovered.get_run("query-old", headers=_headers())["attestation"]["key_id"] == "query-v1"
    assert recovered.get_run("query-new", headers=_headers())["attestation"]["key_id"] == "query-v2"
