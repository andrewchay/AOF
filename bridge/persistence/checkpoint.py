# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W02.06 — Unified cross-store recovery watermark.

Five governance stores advance at their own pace:
- ledger        : per-tenant decision chain head (sequence, hash)
- outbox        : dispatched through-count of audit events
- tombstone     : deletion-tombstone count
- revocations   : revocation-registry row count
- actions       : action_runs row count

A single checkpoint row records ALL of these watermarks atomically (one
SQLite transaction). After any restore, verify_watermarks() re-reads the
live stores and compares against the checkpoint taken just before the
crash/backup; any drift is listed as a reconciliation item instead of
being silently accepted. Business references stay consistent because a
drifted store is identified by name with its expected/actual values.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from bridge.persistence.sqlite_support import managed_sqlite_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS store_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    taken_at      TEXT NOT NULL,
    watermarks    TEXT NOT NULL
);
"""


class CheckpointError(ValueError):
    pass


@dataclass(frozen=True)
class Watermarks:
    ledger_heads: dict[str, tuple[int, str]]  # tenant -> (sequence, head_hash)
    outbox_dispatched: int
    tombstone_count: int
    revocation_count: int
    action_run_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_heads": {t: list(v) for t, v in self.ledger_heads.items()},
            "outbox_dispatched": self.outbox_dispatched,
            "tombstone_count": self.tombstone_count,
            "revocation_count": self.revocation_count,
            "action_run_count": self.action_run_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Watermarks":
        return cls(
            ledger_heads={t: (int(v[0]), str(v[1])) for t, v in data["ledger_heads"].items()},
            outbox_dispatched=int(data["outbox_dispatched"]),
            tombstone_count=int(data["tombstone_count"]),
            revocation_count=int(data["revocation_count"]),
            action_run_count=int(data["action_run_count"]),
        )


# ---------------------------------------------------------------------------
# Live watermark readers (each store knows its own progress)
# ---------------------------------------------------------------------------


def read_ledger_heads(ledger_repo) -> dict[str, tuple[int, str]]:
    """(tenant -> (sequence, head_hash)) for every tenant chain."""
    with managed_sqlite_connection(ledger_repo._connect) as connection:
        rows = connection.execute(
            "SELECT tenant_id, sequence, head_hash FROM ledger_heads"
        ).fetchall()
    return {r["tenant_id"]: (r["sequence"], r["head_hash"]) for r in rows}


def read_outbox_dispatched(dispatcher) -> int:
    """Total events marked dispatched (processed files' line count)."""
    total = 0
    for f in dispatcher.outbox.outbox_dir.glob("*.processed"):
        with f.open("r", encoding="utf-8") as handle:
            total += sum(1 for line in handle if line.strip())
    return total


def read_tombstone_count(coordinator) -> int:
    with managed_sqlite_connection(coordinator._connect) as connection:
        return connection.execute("SELECT COUNT(*) FROM deletion_tombstones").fetchone()[0]


def read_revocation_count(registry) -> int:
    with managed_sqlite_connection(registry._connect) as connection:
        return connection.execute("SELECT COUNT(*) FROM access_revocations").fetchone()[0]


def read_action_run_count(action_repo) -> int:
    with managed_sqlite_connection(action_repo._connect) as connection:
        return connection.execute("SELECT COUNT(*) FROM action_runs").fetchone()[0]


# ---------------------------------------------------------------------------
# Checkpoint store
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    taken_at: str
    watermarks: Watermarks

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "taken_at": self.taken_at,
            "watermarks": self.watermarks.to_dict(),
        }


class WatermarkCheckpointStore:
    """Atomic multi-store watermark snapshots."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def take(self, watermarks: Watermarks) -> Checkpoint:
        checkpoint_id = f"ckpt:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        taken_at = datetime.now(timezone.utc).isoformat()
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute(
                "INSERT INTO store_checkpoints(checkpoint_id, taken_at, watermarks) "
                "VALUES (?, ?, ?)",
                (checkpoint_id, taken_at, json.dumps(watermarks.to_dict(), ensure_ascii=False)),
            )
        return Checkpoint(checkpoint_id, taken_at, watermarks)

    def latest(self) -> Checkpoint | None:
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT checkpoint_id, taken_at, watermarks FROM store_checkpoints "
                "ORDER BY taken_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return Checkpoint(row[0], row[1], Watermarks.from_dict(json.loads(row[2])))


# ---------------------------------------------------------------------------
# Drift reconciliation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Drift:
    store: str
    expected: Any
    actual: Any

    def to_dict(self) -> dict[str, Any]:
        return {"store": self.store, "expected": self.expected, "actual": self.actual}


@dataclass(frozen=True)
class ConsistencyReport:
    checkpoint_id: str
    consistent: bool
    drifts: tuple[Drift, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "consistent": self.consistent,
            "drifts": [d.to_dict() for d in self.drifts],
        }


def verify_watermarks(checkpoint: Checkpoint, actual: Watermarks) -> ConsistencyReport:
    """Compare live watermarks against the checkpoint. Drift is REPORTED,
    never silently accepted - callers reconcile by store name."""
    drifts: list[Drift] = []
    cp = checkpoint.watermarks

    for tenant, (seq, head) in cp.ledger_heads.items():
        actual_head = actual.ledger_heads.get(tenant)
        if actual_head is None:
            drifts.append(Drift(f"ledger[{tenant}]", [seq, head], None))
        elif actual_head[0] < seq:
            # a restored chain must never be BEHIND the checkpoint
            drifts.append(Drift(f"ledger[{tenant}]", [seq, head], list(actual_head)))
        elif actual_head[1] != head and actual_head[0] == seq:
            # same sequence but different hash: the chain was rewritten
            drifts.append(Drift(f"ledger[{tenant}].hash", head, actual_head[1]))

    for name, expected, actual_v in (
        ("outbox_dispatched", cp.outbox_dispatched, actual.outbox_dispatched),
        ("tombstone_count", cp.tombstone_count, actual.tombstone_count),
        ("revocation_count", cp.revocation_count, actual.revocation_count),
        ("action_run_count", cp.action_run_count, actual.action_run_count),
    ):
        if actual_v < expected:
            # monotone counters going backwards after a restore = data loss
            drifts.append(Drift(name, expected, actual_v))

    return ConsistencyReport(
        checkpoint_id=checkpoint.checkpoint_id,
        consistent=not drifts,
        drifts=tuple(drifts),
    )


class CheckpointCoordinator:
    """Take and verify cross-store watermarks in one place."""

    def __init__(self, store: WatermarkCheckpointStore, *, readers: dict[str, Callable[[], Any]]) -> None:
        self.store = store
        self.readers = readers

    def take_now(self) -> Checkpoint:
        watermarks = Watermarks(
            ledger_heads=self.readers["ledger_heads"](),
            outbox_dispatched=self.readers["outbox_dispatched"](),
            tombstone_count=self.readers["tombstone_count"](),
            revocation_count=self.readers["revocation_count"](),
            action_run_count=self.readers["action_run_count"](),
        )
        return self.store.take(watermarks)

    def verify_against_latest(self, actual: Watermarks) -> ConsistencyReport | None:
        checkpoint = self.store.latest()
        if checkpoint is None:
            return None
        return verify_watermarks(checkpoint, actual)
