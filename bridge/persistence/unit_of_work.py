# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W02.04 — SqliteUnitOfWork: atomic business-state + audit commits.

Plan section 6.4: local state changes and audit/outbox writes must share
one UnitOfWork. Writing the decision ledger to one store and the action
run state to another with two commits can leave a "confirmed decision but
missing state" (or vice versa) after a crash between the writes.

SqliteUnitOfWork opens ONE connection to the shared control-state SQLite
file, starts BEGIN IMMEDIATE, and exposes both repositories' operations on
that connection; commit happens exactly once when the block exits cleanly.
Any exception rolls back BOTH the decision entry and the state transition.

Usage:

    uow = SqliteUnitOfWork(path)
    with uow.atomic() as session:
        decision_id = session.record_decision(decision_record)
        session.put_action_run(run)
    # both writes committed atomically, or neither
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from bridge.persistence.decision_ledger_repository import (
    LedgerConflictError,
    LedgerEntry,
    SQLiteDecisionLedgerRepository,
    _hash,
)

if TYPE_CHECKING:  # avoid import cycle: semantic_core imports decision_provenance
    from bridge.decision_provenance import DecisionRecord
    from bridge.semantic_core.action_runs import ActionRun, SqliteActionRunRepository


class UnitOfWorkError(ValueError):
    """Raised when a UnitOfWork operation cannot complete safely."""


class SqliteUnitOfWorkSession:
    """Operations bound to the single UnitOfWork connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        ledger: SQLiteDecisionLedgerRepository,
        actions: "SqliteActionRunRepository",
    ) -> None:
        self._connection = connection
        self._ledger = ledger
        self._actions = actions

    # -- decision ledger -------------------------------------------------

    def record_decision(self, decision: "DecisionRecord") -> str:
        """Append a decision entry on the UoW connection; return decision id.

        Chain head is read on THIS connection inside the transaction, so the
        sequence/previous_hash are consistent with the eventual commit.
        """
        tenant_id = decision.tenant_id or "__default__"
        head = self._ledger._head_on(self._connection, tenant_id)
        sequence = (head[0] + 1) if head else 1
        previous_hash = head[1] if head else None
        payload = {"decision": decision.to_dict(), "previous_hash": previous_hash}
        entry = LedgerEntry(
            tenant_id=tenant_id,
            sequence=sequence,
            decision_id=decision.id,
            payload_digest=_hash(decision.to_dict()),
            previous_hash=previous_hash,
            entry_hash=_hash(payload),
            payload=payload,
            recorded_at=decision.recorded_at,
        )
        try:
            self._ledger._append_on(self._connection, entry)
        except LedgerConflictError as exc:
            raise UnitOfWorkError(str(exc)) from exc
        return decision.id

    # -- action runs ------------------------------------------------------

    def put_action_run(self, run: "ActionRun") -> "ActionRun":
        return self._actions._put_new_on(self._connection, run)

    def update_action_run(self, run: "ActionRun", *, expected_digest: str) -> "ActionRun":
        return self._actions._update_on(self._connection, run, expected_digest=expected_digest)


class SqliteUnitOfWork:
    """Single-connection UnitOfWork over the shared control-state database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Initialize both schemas on the SAME file (separate tables).
        self._ledger = SQLiteDecisionLedgerRepository(self.path)
        from bridge.semantic_core.action_runs import SqliteActionRunRepository

        self._actions = SqliteActionRunRepository(self.path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def atomic(self) -> Iterator[SqliteUnitOfWorkSession]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield SqliteUnitOfWorkSession(connection, self._ledger, self._actions)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
