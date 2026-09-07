"""W08.04 — PostgreSQL implementation of the DecisionLedgerRepository SPI.

Same contract as SQLiteDecisionLedgerRepository (append with head lock,
idempotency by (tenant, decision_id), per-tenant hash chains, quarantine),
adapted to PostgreSQL:

- the SQLite BEGIN IMMEDIATE head lock becomes SELECT ... FOR UPDATE on
  the ledger_heads row inside a transaction (row-level lock)
- idempotent append uses ON CONFLICT (same PG-native syntax)
- WAL / busy_timeout are PG defaults and dropped
- the quarantine table mirrors the SQLite one

Contract tests run the SAME test suite against both backends
(test_postgres_ledger_contract.py), satisfying plan 5.3/11: SQLite and
PostgreSQL share one domain contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bridge.persistence.decision_ledger_repository import (
    LedgerConflictError,
    LedgerEntry,
    _canonical_json,
    _hash,
)
from bridge.persistence.sqlite_support import managed_sqlite_connection  # noqa: F401 (contract parity doc)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_entries (
    tenant_id      TEXT NOT NULL,
    sequence       INTEGER NOT NULL,
    decision_id    TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    previous_hash  TEXT,
    entry_hash     TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    recorded_at    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, sequence),
    UNIQUE (tenant_id, decision_id)
);
CREATE TABLE IF NOT EXISTS ledger_heads (
    tenant_id  TEXT PRIMARY KEY,
    sequence   INTEGER NOT NULL,
    head_hash  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_ledger_quarantine (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT NOT NULL,
    line_number  INTEGER,
    raw_payload  TEXT NOT NULL,
    reason       TEXT NOT NULL,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_SCHEMA_MIGRATION_MARKER = "decision_entries"


class PostgresDecisionLedgerRepository:
    """PostgreSQL backend of the ledger SPI (W08.04)."""

    def __init__(self, dsn: str | None = None, *, autocommit_init: bool = True) -> None:
        import os

        self.dsn = dsn or os.environ.get(
            "AOF_LEDGER_PG_DSN", "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control"
        )
        self._init_schema()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(_SCHEMA)
            connection.commit()

    # -- SPI -----------------------------------------------------------------

    def head(self, tenant_id: str) -> tuple[int, str] | None:
        with self._connect() as connection:
            return self._head_on(connection, tenant_id)

    def _head_on(self, connection, tenant_id: str) -> tuple[int, str] | None:
        row = connection.execute(
            "SELECT sequence, head_hash FROM ledger_heads WHERE tenant_id = %s FOR UPDATE",
            (tenant_id,),
        ).fetchone()
        return (row["sequence"], row["head_hash"]) if row else None

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        with self._connect() as connection:
            connection.execute("SET LOCAL lock_timeout = '5s'")
            return self._append_on(connection, entry)

    def _append_on(self, connection, entry: LedgerEntry) -> LedgerEntry:
        """Append on a caller-owned transaction (UnitOfWork support).

        Caller must have opened a transaction; head row is locked FOR UPDATE
        so concurrent appenders serialize on the tenant chain.
        """
        # 1. lock chain head
        head_row = connection.execute(
            "SELECT sequence, head_hash FROM ledger_heads WHERE tenant_id = %s FOR UPDATE",
            (entry.tenant_id,),
        ).fetchone()
        expected_sequence = (head_row["sequence"] + 1) if head_row else 1
        expected_previous = head_row["head_hash"] if head_row else None

        # 2. idempotency
        existing = connection.execute(
            "SELECT * FROM decision_entries WHERE tenant_id = %s AND decision_id = %s",
            (entry.tenant_id, entry.decision_id),
        ).fetchone()
        if existing is not None:
            return self._row_to_entry(existing)

        if entry.sequence != expected_sequence:
            raise LedgerConflictError(
                f"sequence mismatch: expected {expected_sequence}, got {entry.sequence}"
            )
        if entry.previous_hash != expected_previous:
            raise LedgerConflictError(
                f"previous_hash mismatch: expected {expected_previous}, got {entry.previous_hash}"
            )

        # 3. insert
        connection.execute(
            "INSERT INTO decision_entries"
            "(tenant_id, sequence, decision_id, payload_digest, previous_hash, "
            "entry_hash, payload_json, recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                entry.tenant_id,
                entry.sequence,
                entry.decision_id,
                entry.payload_digest,
                entry.previous_hash,
                entry.entry_hash,
                _canonical_json(entry.payload),
                entry.recorded_at,
            ),
        )

        # 4. update head
        connection.execute(
            "INSERT INTO ledger_heads (tenant_id, sequence, head_hash) VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id) DO UPDATE SET sequence = excluded.sequence, "
            "head_hash = excluded.head_hash",
            (entry.tenant_id, entry.sequence, entry.entry_hash),
        )
        return entry

    def get(self, decision_id: str) -> LedgerEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM decision_entries WHERE decision_id = %s",
                (decision_id,),
            ).fetchone()
            return self._row_to_entry(row) if row else None

    def entries(self, *, tenant_id: str, limit: int | None = None) -> list[LedgerEntry]:
        query = (
            "SELECT * FROM decision_entries WHERE tenant_id = %s ORDER BY sequence"
        )
        with self._connect() as connection:
            rows = connection.execute(
                query + (" LIMIT %s" if limit else ""), 
                (tenant_id, limit) if limit else (tenant_id,),
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def all_entries(self, tenant_id: str | None = None) -> list[LedgerEntry]:
        with self._connect() as connection:
            if tenant_id:
                rows = connection.execute(
                    "SELECT * FROM decision_entries WHERE tenant_id = %s ORDER BY sequence",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM decision_entries ORDER BY tenant_id, sequence"
                ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def verify_chain(self, tenant_id: str | None = None) -> dict[str, Any]:
        entries = self.all_entries(tenant_id)
        if tenant_id is not None:
            entries = [e for e in entries if e.tenant_id == tenant_id]
        previous_hash = None
        checked = 0
        for entry in entries:
            checked += 1
            payload = {
                "decision": entry.payload["decision"],
                "previous_hash": entry.previous_hash,
            }
            if entry.previous_hash != previous_hash or entry.entry_hash != _hash(payload):
                return {
                    "valid": False,
                    "entries_checked": checked,
                    "failed_decision_id": entry.decision_id,
                }
            previous_hash = entry.entry_hash
        return {
            "valid": True,
            "entries_checked": checked,
            "head_hash": previous_hash,
        }

    def migrate_from_jsonl(self, path: str | Path) -> dict[str, Any]:
        raise NotImplementedError(
            "JSONL migration for the PostgreSQL backend lands with W02.01 closure; "
            "use SQLiteDecisionLedgerRepository.migrate_from_jsonl + a dump/restore"
        )

    # -- helpers ------------------------------------------------------------

    def _row_to_entry(self, row: Any) -> LedgerEntry:
        payload = json.loads(row["payload_json"])
        return LedgerEntry(
            tenant_id=row["tenant_id"],
            sequence=row["sequence"],
            decision_id=row["decision_id"],
            payload_digest=row["payload_digest"],
            previous_hash=row["previous_hash"],
            entry_hash=row["entry_hash"],
            payload=payload,
            recorded_at=row["recorded_at"],
        )
