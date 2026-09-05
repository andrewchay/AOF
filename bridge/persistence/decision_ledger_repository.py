"""W02.01 — Decision ledger Repository SPI + SQLite implementation.

The decision ledger was originally a JSONL file with fcntl locking and a
sidecar checkpoint (see ``bridge.decision_provenance``). That design is
single-host only and cannot express transactional chain-head updates.

This module defines the Repository SPI and a SQLite reference
implementation (``reference-local`` profile). Each tenant owns an
independent hash chain; ``append`` runs inside a ``BEGIN IMMEDIATE``
transaction that locks the chain head, validates the parent record,
inserts the entry, and updates the chain head atomically.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from bridge.persistence.sqlite_support import managed_sqlite_connection


class DecisionLedgerError(ValueError):
    """Raised when a ledger operation cannot be safely completed."""


class LedgerIntegrityError(DecisionLedgerError):
    """Raised when the ledger fails integrity verification."""


class LedgerConflictError(DecisionLedgerError):
    """Raised when an append conflicts with an existing record (idempotency)."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _hash(value: Any) -> str:
    import hashlib

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    """A single ledger entry as stored by the repository."""

    tenant_id: str
    sequence: int
    decision_id: str
    payload_digest: str
    previous_hash: str | None
    entry_hash: str
    payload: dict[str, Any]
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.payload["decision"],
            "previous_hash": self.previous_hash,
            "integrity": {"algorithm": "sha256", "hash": self.entry_hash},
        }


@runtime_checkable
class DecisionLedgerRepository(Protocol):
    """Repository SPI for the decision ledger.

    Implementations must guarantee that ``append`` is atomic: the chain
    head is locked, the parent record is validated, the entry is inserted,
    and the chain head is updated — all in one transaction.
    """

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        """Append a new entry. Must be idempotent on ``decision_id``."""
        ...

    def get(self, decision_id: str) -> LedgerEntry | None:
        """Return the entry for ``decision_id`` or ``None``."""
        ...

    def entries(
        self, tenant_id: str, limit: int = 100, offset: int = 0
    ) -> list[LedgerEntry]:
        """Return entries for a tenant in sequence order."""
        ...

    def verify_chain(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Verify hash-chain integrity. Returns a summary dict."""
        ...

    def head(self, tenant_id: str) -> tuple[int, str] | None:
        """Return (sequence, head_hash) for the tenant or ``None``."""
        ...

    def migrate_from_jsonl(self, path: str | Path) -> dict[str, Any]:
        """Import a JSONL ledger, preserving legacy identity."""
        ...


class SQLiteDecisionLedgerRepository:
    """SQLite reference implementation of :class:`DecisionLedgerRepository`.

    Tables:
        decision_entries(tenant_id, sequence, decision_id, payload_digest,
                         previous_hash, entry_hash, payload_json, recorded_at)
        ledger_heads(tenant_id, sequence, head_hash)

    Concurrency: ``BEGIN IMMEDIATE`` + ``busy_timeout`` so that concurrent
    writers serialise on the chain head row.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        busy_timeout_ms: int = 5000,
    ) -> None:
        configured = os.environ.get("AOF_DECISION_LEDGER_SQLITE_FILE")
        self.path = Path(path or configured or "data/audit/decision_ledger.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._init_schema()

    # ------------------------------------------------------------------
    # connection helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=self._busy_timeout_ms / 1000.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_schema(self) -> None:
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(
                """
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
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    source       TEXT NOT NULL,
                    line_number  INTEGER,
                    raw_payload  TEXT NOT NULL,
                    reason       TEXT NOT NULL,
                    quarantined_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )

    # ------------------------------------------------------------------
    # Repository SPI
    # ------------------------------------------------------------------

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        """Append ``entry`` in a single immediate transaction.

        Steps: lock chain head → validate parent → insert → update head.
        Idempotent: re-appending the same ``decision_id`` returns the
        existing entry unchanged.
        """
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._append_on(connection, entry)

    def _append_on(self, connection: sqlite3.Connection, entry: LedgerEntry) -> LedgerEntry:
        """Append on a caller-owned connection (W02.04 UnitOfWork support).

        The caller owns the transaction: this method performs head-lock read,
        idempotency check, insert and head update but never commits/rolls back.
        """
        # 1. Lock chain head
        head_row = connection.execute(
            "SELECT sequence, head_hash FROM ledger_heads WHERE tenant_id = ?",
            (entry.tenant_id,),
        ).fetchone()
        expected_sequence = (head_row["sequence"] + 1) if head_row else 1
        expected_previous = head_row["head_hash"] if head_row else None

        # 2. Idempotency: same decision_id already present?
        existing = connection.execute(
            "SELECT * FROM decision_entries WHERE tenant_id = ? AND decision_id = ?",
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

        # 3. Insert entry
        connection.execute(
            """
            INSERT INTO decision_entries
                (tenant_id, sequence, decision_id, payload_digest,
                 previous_hash, entry_hash, payload_json, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
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

        # 4. Update chain head
        connection.execute(
            """
            INSERT INTO ledger_heads (tenant_id, sequence, head_hash)
            VALUES (?, ?, ?)
            ON CONFLICT(tenant_id) DO UPDATE SET
                sequence = excluded.sequence,
                head_hash = excluded.head_hash
            """,
            (entry.tenant_id, entry.sequence, entry.entry_hash),
        )
        return entry

    def get(self, decision_id: str) -> LedgerEntry | None:
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT * FROM decision_entries WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            return self._row_to_entry(row) if row else None

    def entries(
        self, tenant_id: str, limit: int = 100, offset: int = 0
    ) -> list[LedgerEntry]:
        with managed_sqlite_connection(self._connect) as connection:
            rows = connection.execute(
                """
                SELECT * FROM decision_entries
                WHERE tenant_id = ?
                ORDER BY sequence
                LIMIT ? OFFSET ?
                """,
                (tenant_id, limit, offset),
            ).fetchall()
            return [self._row_to_entry(row) for row in rows]

    def all_entries(self, tenant_id: str | None = None) -> list[LedgerEntry]:
        """Return all entries (optionally filtered by tenant) in sequence order."""
        with managed_sqlite_connection(self._connect) as connection:
            if tenant_id is None:
                rows = connection.execute(
                    "SELECT * FROM decision_entries ORDER BY tenant_id, sequence"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM decision_entries WHERE tenant_id = ? ORDER BY sequence",
                    (tenant_id,),
                ).fetchall()
            return [self._row_to_entry(row) for row in rows]

    def verify_chain(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Verify hash-chain integrity for one tenant or all tenants."""
        if tenant_id is not None:
            return self._verify_tenant_chain(tenant_id)
        # Verify every tenant
        with managed_sqlite_connection(self._connect) as connection:
            tenants = [
                row["tenant_id"]
                for row in connection.execute(
                    "SELECT DISTINCT tenant_id FROM decision_entries"
                ).fetchall()
            ]
        for tid in tenants:
            result = self._verify_tenant_chain(tid)
            if not result["valid"]:
                return result
        return {
            "valid": True,
            "entries_checked": sum(
                self._verify_tenant_chain(tid)["entries_checked"] for tid in tenants
            ),
            "tenants_checked": len(tenants),
        }

    def _verify_tenant_chain(self, tenant_id: str) -> dict[str, Any]:
        entries = self.all_entries(tenant_id)
        previous_hash = None
        for index, entry in enumerate(entries, start=1):
            payload = {
                "decision": entry.payload["decision"],
                "previous_hash": entry.previous_hash,
            }
            expected_hash = _hash(payload)
            if entry.previous_hash != previous_hash or entry.entry_hash != expected_hash:
                return {
                    "valid": False,
                    "entries_checked": index,
                    "failed_decision_id": entry.decision_id,
                    "tenant_id": tenant_id,
                }
            previous_hash = entry.entry_hash
        head = self.head(tenant_id)
        return {
            "valid": True,
            "entries_checked": len(entries),
            "head_hash": previous_hash,
            "tenant_id": tenant_id,
            "head_sequence": head[0] if head else 0,
        }

    def head(self, tenant_id: str) -> tuple[int, str] | None:
        with managed_sqlite_connection(self._connect) as connection:
            return self._head_on(connection, tenant_id)

    def _head_on(
        self, connection: sqlite3.Connection, tenant_id: str
    ) -> tuple[int, str] | None:
        """Read chain head on a caller-owned connection (W02.04 UoW support)."""
        row = connection.execute(
            "SELECT sequence, head_hash FROM ledger_heads WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        return (row["sequence"], row["head_hash"]) if row else None

    def migrate_from_jsonl(self, path: str | Path) -> dict[str, Any]:
        """Import a JSONL ledger, preserving legacy identity.

        Corrupt, tenant-less, or duplicate records are quarantined.
        Returns a migration manifest.
        """
        path = Path(path)
        if not path.exists():
            return {
                "input_lines": 0,
                "imported": 0,
                "quarantined": 0,
                "duplicates": 0,
                "output_path": str(self.path),
            }

        lines = path.read_text(encoding="utf-8").splitlines()
        imported = 0
        quarantined = 0
        duplicates = 0
        seen_ids: set[str] = set()

        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for line_number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    self._quarantine(
                        connection, str(path), line_number, line, "invalid_json"
                    )
                    quarantined += 1
                    continue

                decision = raw.get("decision")
                if not isinstance(decision, dict):
                    self._quarantine(
                        connection, str(path), line_number, line, "missing_decision"
                    )
                    quarantined += 1
                    continue

                tenant_id = decision.get("tenant_id")
                if not tenant_id:
                    self._quarantine(
                        connection, str(path), line_number, line, "missing_tenant_id"
                    )
                    quarantined += 1
                    continue

                decision_id = decision.get("id")
                if not decision_id:
                    self._quarantine(
                        connection, str(path), line_number, line, "missing_decision_id"
                    )
                    quarantined += 1
                    continue

                if decision_id in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(decision_id)

                # Check if already in DB (idempotent re-migration)
                existing = connection.execute(
                    "SELECT 1 FROM decision_entries WHERE tenant_id = ? AND decision_id = ?",
                    (tenant_id, decision_id),
                ).fetchone()
                if existing:
                    duplicates += 1
                    continue

                previous_hash = raw.get("previous_hash")
                integrity = raw.get("integrity", {})
                entry_hash = integrity.get("hash")
                if not entry_hash:
                    self._quarantine(
                        connection, str(path), line_number, line, "missing_integrity_hash"
                    )
                    quarantined += 1
                    continue

                # Verify hash before import
                payload = {"decision": decision, "previous_hash": previous_hash}
                if _hash(payload) != entry_hash:
                    self._quarantine(
                        connection, str(path), line_number, line, "hash_mismatch"
                    )
                    quarantined += 1
                    continue

                # Determine sequence for this tenant
                head_row = connection.execute(
                    "SELECT sequence, head_hash FROM ledger_heads WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()
                sequence = (head_row["sequence"] + 1) if head_row else 1
                expected_previous = head_row["head_hash"] if head_row else None

                if previous_hash != expected_previous:
                    self._quarantine(
                        connection,
                        str(path),
                        line_number,
                        line,
                        f"chain_mismatch: expected previous={expected_previous}",
                    )
                    quarantined += 1
                    continue

                connection.execute(
                    """
                    INSERT INTO decision_entries
                        (tenant_id, sequence, decision_id, payload_digest,
                         previous_hash, entry_hash, payload_json, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
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
                    """
                    INSERT INTO ledger_heads (tenant_id, sequence, head_hash)
                    VALUES (?, ?, ?)
                    ON CONFLICT(tenant_id) DO UPDATE SET
                        sequence = excluded.sequence,
                        head_hash = excluded.head_hash
                    """,
                    (tenant_id, sequence, entry_hash),
                )
                imported += 1

        return {
            "input_lines": len([l for l in lines if l.strip()]),
            "imported": imported,
            "quarantined": quarantined,
            "duplicates": duplicates,
            "output_path": str(self.path),
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> LedgerEntry:
        return LedgerEntry(
            tenant_id=row["tenant_id"],
            sequence=row["sequence"],
            decision_id=row["decision_id"],
            payload_digest=row["payload_digest"],
            previous_hash=row["previous_hash"],
            entry_hash=row["entry_hash"],
            payload=json.loads(row["payload_json"]),
            recorded_at=row["recorded_at"],
        )

    @staticmethod
    def _quarantine(
        connection: sqlite3.Connection,
        source: str,
        line_number: int,
        raw_payload: str,
        reason: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO decision_ledger_quarantine
                (source, line_number, raw_payload, reason)
            VALUES (?, ?, ?, ?)
            """,
            (source, line_number, raw_payload, reason),
        )
