"""W02.01 — Tests for the SQLite decision ledger repository."""

from __future__ import annotations

import json
import os
import sqlite3
import threading

import pytest

from bridge.decision_provenance import (
    DecisionProvenanceStore,
    LedgerIntegrityError,
)
from bridge.persistence.decision_ledger_repository import (
    LedgerConflictError,
    LedgerEntry,
    SQLiteDecisionLedgerRepository,
    _canonical_json,
    _hash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    repo: SQLiteDecisionLedgerRepository,
    tenant_id: str,
    decision_id: str,
    conclusion: str = "ok",
) -> LedgerEntry:
    head = repo.head(tenant_id)
    sequence = (head[0] + 1) if head else 1
    previous_hash = head[1] if head else None
    decision = {
        "id": decision_id,
        "recorded_at": "2026-09-05T00:00:00+00:00",
        "agent_id": "agent",
        "decision_type": "test",
        "conclusion": conclusion,
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


def _append_with_retry(
    repo: SQLiteDecisionLedgerRepository,
    tenant_id: str,
    decision_id: str,
    conclusion: str = "ok",
    max_retries: int = 10,
) -> LedgerEntry:
    """Append with retry on sequence conflict (handles concurrent writers)."""
    for attempt in range(max_retries):
        entry = _make_entry(repo, tenant_id, decision_id, conclusion)
        try:
            return repo.append(entry)
        except LedgerConflictError:
            if attempt == max_retries - 1:
                raise
            continue
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# SQLite repository tests
# ---------------------------------------------------------------------------


def test_sqlite_append_and_get(tmp_path):
    repo = SQLiteDecisionLedgerRepository(tmp_path / "ledger.sqlite")
    entry = _make_entry(repo, "tenant-a", "decision:1")
    repo.append(entry)

    fetched = repo.get("decision:1")
    assert fetched is not None
    assert fetched.decision_id == "decision:1"
    assert fetched.tenant_id == "tenant-a"
    assert fetched.payload["decision"]["conclusion"] == "ok"


def test_sqlite_concurrent_append(tmp_path):
    """8 threads appending to the same tenant must not corrupt the chain."""
    repo = SQLiteDecisionLedgerRepository(tmp_path / "ledger.sqlite")
    errors = []

    def write(i: int) -> None:
        try:
            _append_with_retry(repo, "tenant-x", f"decision:{i}")
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent appends raised: {errors}"
    result = repo.verify_chain("tenant-x")
    assert result["valid"]
    assert result["entries_checked"] == 8


def test_sqlite_idempotent_append(tmp_path):
    """Re-appending the same decision_id returns the existing entry."""
    repo = SQLiteDecisionLedgerRepository(tmp_path / "ledger.sqlite")
    entry = _make_entry(repo, "tenant-a", "decision:1")
    first = repo.append(entry)
    second = repo.append(entry)
    assert first == second
    assert repo.verify_chain("tenant-a")["entries_checked"] == 1


def test_sqlite_cross_tenant_chain_isolation(tmp_path):
    """Each tenant has an independent chain head and sequence."""
    repo = SQLiteDecisionLedgerRepository(tmp_path / "ledger.sqlite")
    repo.append(_make_entry(repo, "tenant-a", "decision:a1"))
    repo.append(_make_entry(repo, "tenant-b", "decision:b1"))
    repo.append(_make_entry(repo, "tenant-a", "decision:a2"))

    head_a = repo.head("tenant-a")
    head_b = repo.head("tenant-b")
    assert head_a == (2, repo.get("decision:a2").entry_hash)
    assert head_b == (1, repo.get("decision:b1").entry_hash)

    assert repo.verify_chain("tenant-a")["valid"]
    assert repo.verify_chain("tenant-b")["valid"]
    assert repo.verify_chain()["valid"]


def test_sqlite_tamper_detection(tmp_path):
    """Modifying payload_json directly must cause verify_chain to fail."""
    repo = SQLiteDecisionLedgerRepository(tmp_path / "ledger.sqlite")
    repo.append(_make_entry(repo, "tenant-a", "decision:1"))

    # Tamper with the stored payload
    with sqlite3.connect(str(tmp_path / "ledger.sqlite")) as conn:
        conn.execute(
            "UPDATE decision_entries SET payload_json = ? WHERE decision_id = ?",
            (
                json.dumps(
                    {
                        "decision": {
                            "id": "decision:1",
                            "conclusion": "TAMPERED",
                        },
                        "previous_hash": None,
                    }
                ),
                "decision:1",
            ),
        )

    result = repo.verify_chain("tenant-a")
    assert not result["valid"]
    assert result["failed_decision_id"] == "decision:1"


def test_sqlite_migration_roundtrip(tmp_path):
    """JSONL → SQLite → verify → read back identical entries."""
    jsonl_path = tmp_path / "legacy.jsonl"
    store = DecisionProvenanceStore(jsonl_path)
    entries = []
    for i in range(3):
        entries.append(
            store.record(
                agent_id="agent",
                decision_type="migrate",
                conclusion=f"c-{i}",
                rationale="r",
                tenant_id="tenant-m",
            )
        )

    sqlite_path = tmp_path / "migrated.sqlite"
    repo = SQLiteDecisionLedgerRepository(sqlite_path)
    manifest = repo.migrate_from_jsonl(jsonl_path)

    assert manifest["input_lines"] == 3
    assert manifest["imported"] == 3
    assert manifest["quarantined"] == 0
    assert manifest["duplicates"] == 0

    assert repo.verify_chain("tenant-m")["valid"]
    for original in entries:
        fetched = repo.get(original["decision"]["id"])
        assert fetched is not None
        assert fetched.payload["decision"]["conclusion"] == original["decision"]["conclusion"]


def test_sqlite_migration_quarantine(tmp_path):
    """Corrupt and tenant-less records are quarantined during migration."""
    jsonl_path = tmp_path / "corrupt.jsonl"
    lines = [
        json.dumps({"decision": {"id": "d1", "tenant_id": "t1", "conclusion": "ok"}, "previous_hash": None, "integrity": {"hash": _hash({"decision": {"id": "d1", "tenant_id": "t1", "conclusion": "ok"}, "previous_hash": None})}}),
        '{"invalid json',
        json.dumps({"decision": {"id": "d2", "conclusion": "no-tenant"}, "previous_hash": None, "integrity": {"hash": _hash({"decision": {"id": "d2", "conclusion": "no-tenant"}, "previous_hash": None})}}),
    ]
    jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    repo = SQLiteDecisionLedgerRepository(tmp_path / "out.sqlite")
    manifest = repo.migrate_from_jsonl(jsonl_path)

    assert manifest["input_lines"] == 3
    assert manifest["imported"] == 1
    assert manifest["quarantined"] == 2

    with sqlite3.connect(str(tmp_path / "out.sqlite")) as conn:
        rows = conn.execute("SELECT reason FROM decision_ledger_quarantine").fetchall()
    reasons = {row[0] for row in rows}
    assert "invalid_json" in reasons
    assert "missing_tenant_id" in reasons


# ---------------------------------------------------------------------------
# Compatibility-layer tests (SQLite backend)
# ---------------------------------------------------------------------------


def test_store_sqlite_backend_roundtrip(tmp_path):
    """DecisionProvenanceStore with .sqlite path uses the SQLite backend."""
    store = DecisionProvenanceStore(tmp_path / "ledger.sqlite")
    entry = store.record(
        agent_id="agent",
        decision_type="test",
        conclusion="ok",
        rationale="r",
        tenant_id="tenant-s",
    )
    assert entry["decision"]["conclusion"] == "ok"
    assert store.verify_integrity()["valid"]
    assert store.get(entry["decision"]["id"]) is not None


def test_store_sqlite_tamper_fails_closed(tmp_path):
    """Tampering with the SQLite store must cause reads to fail closed."""
    store = DecisionProvenanceStore(tmp_path / "ledger.sqlite")
    entry = store.record(
        agent_id="agent",
        decision_type="tamper",
        conclusion="original",
        rationale="r",
        tenant_id="tenant-s",
    )
    decision_id = entry["decision"]["id"]

    sqlite_path = tmp_path / "ledger.sqlite"
    with sqlite3.connect(str(sqlite_path)) as conn:
        conn.execute(
            "UPDATE decision_entries SET payload_json = ? WHERE decision_id = ?",
            (
                json.dumps(
                    {
                        "decision": {
                            "id": decision_id,
                            "conclusion": "TAMPERED",
                        },
                        "previous_hash": None,
                    }
                ),
                decision_id,
            ),
        )

    with pytest.raises(LedgerIntegrityError):
        DecisionProvenanceStore(sqlite_path).get(decision_id)


def test_store_backend_selection_by_suffix(tmp_path, monkeypatch):
    """Path suffix selects backend when env var is absent."""
    monkeypatch.delenv("AOF_DECISION_LEDGER_BACKEND", raising=False)

    jsonl_store = DecisionProvenanceStore(tmp_path / "ledger.jsonl")
    assert jsonl_store._backend.__class__.__name__ == "_JSONLLedgerBackend"

    sqlite_store = DecisionProvenanceStore(tmp_path / "ledger.sqlite")
    assert sqlite_store._backend.__class__.__name__ == "_SQLiteLedgerBackend"


def test_store_backend_selection_by_env(tmp_path, monkeypatch):
    """Environment variable overrides suffix-based selection."""
    monkeypatch.setenv("AOF_DECISION_LEDGER_BACKEND", "jsonl")
    store = DecisionProvenanceStore(tmp_path / "ledger.sqlite")
    assert store._backend.__class__.__name__ == "_JSONLLedgerBackend"

    monkeypatch.setenv("AOF_DECISION_LEDGER_BACKEND", "sqlite")
    store = DecisionProvenanceStore(tmp_path / "ledger.jsonl")
    assert store._backend.__class__.__name__ == "_SQLiteLedgerBackend"
