"""Immutable query-run persistence, replay identity, and evidence signatures."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from bridge.semantic_core.query_runs import (
    HmacQueryEvidenceAttestor,
    QueryRun,
    QueryRunError,
    SqliteQueryRunRepository,
)


def _run(*, run_id: str = "query-001", replay_of: str | None = None) -> QueryRun:
    return QueryRun.build(
        query_run_id=run_id,
        tenant_id="acme",
        actor="principal:alice",
        request={
            "channel": "production",
            "capability": "semantic_search",
            "query": "customer",
            "purpose": "support",
            "parameters": {},
            "policy_resource_id": "aof://acme/platform/policy/query-trusted",
        },
        compilation_run_id="compile-001",
        compilation_run_digest="sha256:compile",
        release_id="release-001",
        release_digest="sha256:release",
        plan_digest="sha256:plan",
        policy_report_digest="sha256:policy",
        governed_result={"governed_result_digest": "sha256:result"},
        decisions={"execution": "decision-001"},
        evidence_package={"package_digest": "sha256:evidence"},
        replay_of=replay_of,
        recorded_at="2026-08-18T00:00:00+00:00",
    )


def test_sqlite_query_run_is_immutable_and_tenant_isolated(tmp_path) -> None:
    repository = SqliteQueryRunRepository(tmp_path / "query-runs.sqlite3")
    run = _run()

    assert repository.put(run).run_digest == run.run_digest
    assert repository.put(run).run_digest == run.run_digest
    assert repository.get("query-001", tenant_id="acme") == run
    assert repository.get("query-001", tenant_id="other") is None

    conflicting = _run(replay_of="query-000")
    with pytest.raises(QueryRunError, match="cannot be overwritten"):
        repository.put(conflicting)


def test_query_run_detects_storage_tampering(tmp_path) -> None:
    path = tmp_path / "query-runs.sqlite3"
    repository = SqliteQueryRunRepository(path)
    repository.put(_run())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE query_runs SET payload = replace(payload, 'sha256:plan', 'sha256:evil')"
        )

    with pytest.raises(QueryRunError, match="run_digest"):
        repository.get("query-001", tenant_id="acme")


def test_query_evidence_attestation_binds_run_tenant_and_package() -> None:
    run = _run()
    attestor = HmacQueryEvidenceAttestor(key_id="query-key", secret=b"query-secret")

    attestation = attestor.sign(run)

    assert attestor.verify(attestation, run=run) is True
    assert attestor.verify(attestation, run=_run(run_id="query-002")) is False
    tampered = {**attestation, "evidence_package_digest": "sha256:other"}
    assert attestor.verify(tampered, run=run) is False


def test_query_run_repository_verifies_and_restores_online_backup(tmp_path) -> None:
    repository = SqliteQueryRunRepository(tmp_path / "live.sqlite3")
    run = _run()
    repository.put(run)

    assert repository.schema_version() == 1
    assert repository.verify_all() == {
        "valid": True,
        "query_run_count": 1,
        "errors": [],
    }

    backup = repository.backup_to(tmp_path / "backups" / "query-runs.sqlite3")
    restored = SqliteQueryRunRepository(backup)
    assert restored.get(run.query_run_id, tenant_id="acme") == run


def test_query_run_repository_serializes_concurrent_idempotent_writes(tmp_path) -> None:
    repository = SqliteQueryRunRepository(tmp_path / "query-runs.sqlite3")
    run = _run()

    with ThreadPoolExecutor(max_workers=8) as pool:
        stored = list(pool.map(lambda _: repository.put(run), range(32)))

    assert {item.run_digest for item in stored} == {run.run_digest}
    assert repository.verify_all()["query_run_count"] == 1
