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
        connection = psycopg.connect(self.dsn)
        connection.row_factory = dict_row  # type: ignore[assignment]
        return connection

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
            "SELECT * FROM decision_entries WHERE decision_id = %s",
            (entry.decision_id,),
        ).fetchone()
        if existing is not None:
            if existing["tenant_id"] != entry.tenant_id:
                raise LedgerConflictError("decision_id is already bound to another tenant")
            return self._row_to_entry(existing)

        parents = entry.payload.get("decision", {}).get("parent_decision_ids", [])
        for parent_id in parents:
            parent = connection.execute(
                "SELECT 1 FROM decision_entries WHERE tenant_id = %s AND decision_id = %s",
                (entry.tenant_id, parent_id),
            ).fetchone()
            if parent is None:
                raise LedgerConflictError(
                    f"unknown or cross-tenant parent decision: {parent_id}"
                )

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
        """Import a legacy JSONL chain atomically, quarantining bad records."""
        source = Path(path)
        if not source.exists():
            return {
                "input_lines": 0,
                "imported": 0,
                "quarantined": 0,
                "duplicates": 0,
                "output_path": "postgresql:decision_entries",
            }

        lines = source.read_text(encoding="utf-8").splitlines()
        imported = 0
        quarantined = 0
        duplicates = 0
        seen_ids: set[str] = set()

        with self._connect() as connection:
            # A migration is a single controlled writer. This also serializes
            # creation of a tenant's first head row, which cannot yet be locked
            # with SELECT FOR UPDATE.
            connection.execute(
                "LOCK TABLE decision_entries, ledger_heads IN SHARE ROW EXCLUSIVE MODE"
            )
            for line_number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    self._quarantine(
                        connection, str(source), line_number, line, "invalid_json"
                    )
                    quarantined += 1
                    continue
                if not isinstance(raw, dict):
                    self._quarantine(
                        connection, str(source), line_number, line, "invalid_record"
                    )
                    quarantined += 1
                    continue

                decision = raw.get("decision")
                if not isinstance(decision, dict):
                    self._quarantine(
                        connection, str(source), line_number, line, "missing_decision"
                    )
                    quarantined += 1
                    continue
                tenant_id = decision.get("tenant_id")
                decision_id = decision.get("id")
                if not tenant_id:
                    self._quarantine(
                        connection, str(source), line_number, line, "missing_tenant_id"
                    )
                    quarantined += 1
                    continue
                if not decision_id:
                    self._quarantine(
                        connection, str(source), line_number, line, "missing_decision_id"
                    )
                    quarantined += 1
                    continue
                if decision_id in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(decision_id)

                existing = connection.execute(
                    "SELECT 1 FROM decision_entries WHERE tenant_id = %s AND decision_id = %s",
                    (tenant_id, decision_id),
                ).fetchone()
                if existing:
                    duplicates += 1
                    continue

                previous_hash = raw.get("previous_hash")
                integrity = raw.get("integrity")
                entry_hash = integrity.get("hash") if isinstance(integrity, dict) else None
                if not entry_hash:
                    self._quarantine(
                        connection,
                        str(source),
                        line_number,
                        line,
                        "missing_integrity_hash",
                    )
                    quarantined += 1
                    continue
                payload = {"decision": decision, "previous_hash": previous_hash}
                if _hash(payload) != entry_hash:
                    self._quarantine(
                        connection, str(source), line_number, line, "hash_mismatch"
                    )
                    quarantined += 1
                    continue

                head = self._head_on(connection, str(tenant_id))
                sequence = (head[0] + 1) if head else 1
                expected_previous = head[1] if head else None
                if previous_hash != expected_previous:
                    self._quarantine(
                        connection,
                        str(source),
                        line_number,
                        line,
                        f"chain_mismatch: expected previous={expected_previous}",
                    )
                    quarantined += 1
                    continue

                connection.execute(
                    "INSERT INTO decision_entries"
                    "(tenant_id, sequence, decision_id, payload_digest, previous_hash, "
                    "entry_hash, payload_json, recorded_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        tenant_id,
                        sequence,
                        decision_id,
                        _hash(decision),
                        previous_hash,
                        entry_hash,
                        _canonical_json(raw),
                        decision.get("recorded_at", ""),
                    ),
                )
                connection.execute(
                    "INSERT INTO ledger_heads (tenant_id, sequence, head_hash) "
                    "VALUES (%s, %s, %s) ON CONFLICT (tenant_id) DO UPDATE SET "
                    "sequence = excluded.sequence, head_hash = excluded.head_hash",
                    (tenant_id, sequence, entry_hash),
                )
                imported += 1

        return {
            "input_lines": sum(bool(line.strip()) for line in lines),
            "imported": imported,
            "quarantined": quarantined,
            "duplicates": duplicates,
            "output_path": "postgresql:decision_entries",
        }

    @staticmethod
    def _quarantine(
        connection: Any,
        source: str,
        line_number: int,
        raw_payload: str,
        reason: str,
    ) -> None:
        connection.execute(
            "INSERT INTO decision_ledger_quarantine"
            "(source, line_number, raw_payload, reason) VALUES (%s, %s, %s, %s)",
            (source, line_number, raw_payload, reason),
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
