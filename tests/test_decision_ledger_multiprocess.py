"""W02.02 — Multi-process concurrency verification for the SQLite decision ledger.

Acceptance (plan section 6.6 / 13): 8 processes x 1000 appends on the same
tenant must produce a valid chain with no gaps and no duplicate decision ids;
same-key contention must yield exactly one entry; cross-tenant writers must
not interfere with each other's chains.

Scale is configurable via AOF_LEDGER_STRESS_SCALE (entries per process,
default 1000 per the acceptance contract).
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import time

import pytest

from bridge.persistence.decision_ledger_repository import (
    LedgerConflictError,
    LedgerEntry,
    SQLiteDecisionLedgerRepository,
    _hash,
)

_SCALE = int(os.environ.get("AOF_LEDGER_STRESS_SCALE", "1000"))
_PROCESSES = 8


def _build_entry(repo, tenant_id: str, decision_id: str) -> LedgerEntry:
    head = repo.head(tenant_id)
    sequence = (head[0] + 1) if head else 1
    previous_hash = head[1] if head else None
    decision = {
        "id": decision_id,
        "recorded_at": "2026-09-05T00:00:00+00:00",
        "agent_id": "agent",
        "decision_type": "stress",
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


def _worker_same_tenant(db_path: str, worker_idx: int, count: int, queue) -> None:
    """Each process owns its repo/connections; appends `count` unique decisions."""
    repo = SQLiteDecisionLedgerRepository(db_path)
    appended = 0
    for i in range(count):
        decision_id = f"decision:w{worker_idx}-{i}"
        for attempt in range(200):
            entry = _build_entry(repo, "tenant-stress", decision_id)
            try:
                repo.append(entry)
                appended += 1
                break
            except LedgerConflictError:
                continue  # head moved: rebuild entry with fresh head
            except sqlite3.OperationalError:
                time.sleep(min(0.005 * (attempt + 1), 0.2))
        else:
            queue.put(("error", f"worker {worker_idx} exhausted retries at {i}"))
            return
    queue.put(("ok", appended))


def _worker_same_key(db_path: str, worker_idx: int, attempts: int, queue) -> None:
    """All processes race to append the SAME decision_id."""
    repo = SQLiteDecisionLedgerRepository(db_path)
    for _ in range(attempts):
        entry = _build_entry(repo, "tenant-key", "decision:contended-key")
        try:
            repo.append(entry)
            queue.put(("ok", worker_idx))
            return
        except LedgerConflictError:
            continue
        except sqlite3.OperationalError:
            time.sleep(0.01)
    queue.put(("ok", worker_idx))  # idempotent: entry already exists


def _worker_tenant(db_path: str, tenant: str, count: int, queue) -> None:
    repo = SQLiteDecisionLedgerRepository(db_path)
    appended = 0
    for i in range(count):
        for attempt in range(200):
            entry = _build_entry(repo, tenant, f"{tenant}:decision:{i}")
            try:
                repo.append(entry)
                appended += 1
                break
            except LedgerConflictError:
                continue
            except sqlite3.OperationalError:
                time.sleep(min(0.005 * (attempt + 1), 0.2))
    queue.put(("ok", appended))


def _run_pool(target, args_list):
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    procs = [ctx.Process(target=target, args=a + (queue,)) for a in args_list]
    for p in procs:
        p.start()
    results = [queue.get() for _ in procs]
    for p in procs:
        p.join(timeout=120)
    return results


@pytest.mark.slow
def test_multiprocess_same_tenant_chain_valid(tmp_path):
    """8 processes x N appends on one tenant: valid chain, no gaps, no dup ids."""
    db = str(tmp_path / "ledger.sqlite")
    SQLiteDecisionLedgerRepository(db)  # init schema before forking workers

    started = time.monotonic()
    results = _run_pool(
        _worker_same_tenant,
        [(db, w, _SCALE) for w in range(_PROCESSES)],
    )
    elapsed = time.monotonic() - started

    errors = [r for r in results if r[0] == "error"]
    assert not errors, f"worker errors: {errors}"
    total = sum(r[1] for r in results)
    expected = _PROCESSES * _SCALE
    assert total == expected, f"appended {total} != expected {expected}"

    repo = SQLiteDecisionLedgerRepository(db)
    result = repo.verify_chain("tenant-stress")
    assert result["valid"], f"chain invalid after {_PROCESSES}p x {_SCALE}: {result}"
    assert result["entries_checked"] == expected

    # no duplicate decision ids (DB UNIQUE is the guard; verify explicitly)
    with sqlite3.connect(db) as conn:
        dupes = conn.execute(
            "SELECT decision_id, COUNT(*) c FROM decision_entries "
            "WHERE tenant_id = 'tenant-stress' GROUP BY decision_id HAVING c > 1"
        ).fetchall()
    assert not dupes, f"duplicate decision ids: {dupes[:5]}"
    print(f"\n[stress] {_PROCESSES}p x {_SCALE} = {expected} entries in {elapsed:.1f}s")


def test_multiprocess_same_key_single_winner(tmp_path):
    """Same decision_id raced by 4 processes: exactly one entry, no duplicates."""
    db = str(tmp_path / "ledger.sqlite")
    SQLiteDecisionLedgerRepository(db)

    results = _run_pool(_worker_same_key, [(db, w, 50) for w in range(4)])
    assert all(r[0] == "ok" for r in results)

    with sqlite3.connect(db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM decision_entries "
            "WHERE tenant_id = 'tenant-key' AND decision_id = 'decision:contended-key'"
        ).fetchone()[0]
    assert count == 1, f"same-key contention produced {count} entries, want exactly 1"


def test_multiprocess_cross_tenant_isolation(tmp_path):
    """Concurrent writers on different tenants: independent valid chains."""
    db = str(tmp_path / "ledger.sqlite")
    SQLiteDecisionLedgerRepository(db)

    per_tenant = max(_SCALE // 4, 50)
    results = _run_pool(
        _worker_tenant,
        [(db, f"tenant-{w}", per_tenant) for w in range(4)],
    )
    assert all(r[0] == "ok" and r[1] == per_tenant for r in results)

    repo = SQLiteDecisionLedgerRepository(db)
    for w in range(4):
        result = repo.verify_chain(f"tenant-{w}")
        assert result["valid"], f"tenant-{w} chain invalid: {result}"
        assert result["entries_checked"] == per_tenant
