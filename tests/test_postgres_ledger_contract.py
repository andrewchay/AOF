"""W08.04 — PostgreSQL ledger repository: same contract as SQLite.

Plan 5.3/11: SQLite (reference-local) and PostgreSQL (enterprise) must
share one domain contract. These tests run the same scenario set against
a REAL PostgreSQL instance (docker compose infra) — no mocks.

Requires the infra stack: deploy/docker-compose.infra.yml (postgres up).
Skips cleanly when PostgreSQL is unreachable (e.g. contributor laptops
without the stack) — CI with the stack always runs them.
"""

from __future__ import annotations

import socket

import pytest

pytest.importorskip(
    "psycopg", reason="psycopg not installed; PostgreSQL contract tests skipped"
)

from bridge.persistence.decision_ledger_repository import (  # noqa: E402
    LedgerConflictError,
    SQLiteDecisionLedgerRepository,
    _hash,
)
from bridge.persistence.postgres_ledger_repository import (  # noqa: E402
    PostgresDecisionLedgerRepository,
)

PG_DSN = "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control"


def _pg_available() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 5433), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _make_entry(repo, tenant_id: str, decision_id: str) -> object:
    head = repo.head(tenant_id)
    sequence = (head[0] + 1) if head else 1
    previous_hash = head[1] if head else None
    decision = {
        "id": decision_id,
        "recorded_at": "2026-09-07T00:00:00+00:00",
        "agent_id": "agent",
        "decision_type": "contract",
        "conclusion": "ok",
        "rationale": "r",
        "status": "completed",
        "tenant_id": tenant_id,
        "session_id": None,
        "evidence": [],
        "parent_decision_ids": [],
        "output_entities": [],
        "tags": [],
        "policies": [],
        "metadata": {},
    }
    payload = {"decision": decision, "previous_hash": previous_hash}
    from bridge.persistence.decision_ledger_repository import LedgerEntry

    return LedgerEntry(
        tenant_id=tenant_id,
        sequence=sequence,
        decision_id=decision_id,
        payload_digest=_hash(decision),
        previous_hash=previous_hash,
        entry_hash=_hash(payload),
        payload=payload,
        recorded_at=decision["recorded_at"],
    )


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    if request.param == "sqlite":
        yield SQLiteDecisionLedgerRepository(tmp_path / "ledger.sqlite")
    elif _pg_available():
        repo = PostgresDecisionLedgerRepository(PG_DSN)
        # real DB persists across tests: reset to a clean slate per test
        with repo._connect() as connection:
            connection.execute("TRUNCATE decision_entries, ledger_heads")
            connection.commit()
        yield repo
    else:
        pytest.skip("PostgreSQL infra stack not running (deploy/docker-compose.infra.yml)")


def test_append_and_get_both_backends(repo):
    entry = _make_entry(repo, "t1", "decision:pg-1")
    appended = repo.append(entry)
    fetched = repo.get("decision:pg-1")
    assert fetched is not None
    assert fetched.decision_id == appended.decision_id
    assert fetched.entry_hash == appended.entry_hash
    assert fetched.payload == appended.payload


def test_head_advances_both_backends(repo):
    repo.append(_make_entry(repo, "t1", "decision:pg-2"))
    head = repo.head("t1")
    assert head is not None and head[0] == 1
    repo.append(_make_entry(repo, "t1", "decision:pg-3"))
    assert repo.head("t1")[0] == 2


def test_sequence_conflict_rejected_both_backends(repo):
    repo.append(_make_entry(repo, "t1", "decision:pg-4"))
    stale = _make_entry(repo, "t1", "decision:pg-stale")
    object.__setattr__(stale, "sequence", 1)  # already used
    with pytest.raises(LedgerConflictError):
        repo.append(stale)


def test_idempotent_append_both_backends(repo):
    entry = _make_entry(repo, "t1", "decision:pg-5")
    first = repo.append(entry)
    second = repo.append(entry)  # same id: return existing unchanged
    assert second.decision_id == first.decision_id
    assert second.entry_hash == first.entry_hash
    # and the ledger did not grow a duplicate
    assert len(repo.entries(tenant_id="t1")) == 1


def test_tenant_chain_isolation_both_backends(repo):
    repo.append(_make_entry(repo, "ta", "decision:pg-6"))
    repo.append(_make_entry(repo, "tb", "decision:pg-7"))
    # both chains start at sequence 1 independently
    assert repo.head("ta")[0] == 1
    assert repo.head("tb")[0] == 1


def test_chain_verification_both_backends(repo):
    for i in range(4):
        repo.append(_make_entry(repo, "tv", f"decision:pg-v{i}"))
    result = repo.verify_chain("tv")
    assert result["valid"]
    assert result["entries_checked"] == 4
