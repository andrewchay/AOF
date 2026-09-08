"""W08.04 — PostgreSQL UnitOfWork: atomic business-state + audit commits.

Enterprise-profile version of SqliteUnitOfWork: opens ONE PostgreSQL
connection to the shared control-state database, starts a transaction
(implicit in psycopg3), and exposes both repositories' operations on that
connection; commit happens exactly once when the block exits cleanly.

Usage:

    uow = PostgresUnitOfWork(dsn)
    with uow.atomic() as session:
        decision_id = session.record_decision(decision_record)
        session.put_action_run(run)
    # both writes committed atomically, or neither
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row

from bridge.persistence.decision_ledger_repository import (
    LedgerConflictError,
    LedgerEntry,
    _hash,
)
from bridge.persistence.postgres_ledger_repository import (
    PostgresDecisionLedgerRepository,
)
from bridge.semantic_core.canonical import canonical_json

if TYPE_CHECKING:
    from bridge.decision_provenance import DecisionRecord
    from bridge.semantic_core.action_runs import ActionRun


class UnitOfWorkError(ValueError):
    pass


class PostgresUnitOfWorkSession:
    """Operations bound to the single PostgreSQL UnitOfWork connection."""

    def __init__(self, connection, ledger, actions) -> None:
        self._connection = connection
        self._ledger = ledger
        self._actions = actions

    def record_decision(self, decision: "DecisionRecord") -> str:
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

    def put_action_run(self, run: "ActionRun") -> "ActionRun":
        return self._actions._put_new_on(self._connection, run)

    def update_action_run(self, run: "ActionRun", *, expected_digest: str) -> "ActionRun":
        return self._actions._update_on(self._connection, run, expected_digest=expected_digest)


class PostgresUnitOfWork:
    """Single-connection UnitOfWork over the shared PostgreSQL control-state DB."""

    def __init__(self, dsn: str | None = None) -> None:
        import os

        self.dsn = dsn or os.environ.get(
            "AOF_CONTROL_PG_DSN", "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control"
        )
        self._ledger = PostgresDecisionLedgerRepository(self.dsn)

        # Create the action-runs table in the same PG database if needed
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS action_runs (
                    run_id               TEXT PRIMARY KEY,
                    tenant_id            TEXT NOT NULL,
                    idempotency_identity TEXT NOT NULL UNIQUE,
                    run_digest           TEXT NOT NULL,
                    payload              TEXT NOT NULL
                )
            """)
            conn.commit()
        self._actions = _PgActionRunAdapter(self.dsn)

    @contextlib.contextmanager
    def atomic(self) -> Iterator[PostgresUnitOfWorkSession]:
        connection = psycopg.connect(self.dsn, row_factory=dict_row)
        try:
            connection.execute("SET LOCAL lock_timeout = '10s'")
            yield PostgresUnitOfWorkSession(connection, self._ledger, self._actions)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class _PgActionRunAdapter:
    """Adapter wrapping the PG action-runs table for the UnitOfWork session."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _put_new_on(self, connection, run) -> "ActionRun":
        row = connection.execute(
            "SELECT payload FROM action_runs WHERE idempotency_identity = %s",
            (run.idempotency_identity,),
        ).fetchone()
        if row is not None:
            from bridge.semantic_core.action_runs import ActionRun

            current = ActionRun.from_dict(json.loads(row["payload"]))
            if current.plan.get("plan_digest") != run.plan.get("plan_digest"):
                from bridge.semantic_core.action_runs import ActionRunError

                raise ActionRunError("idempotency key is already bound to another plan")
            return current
        connection.execute(
            "INSERT INTO action_runs (run_id, tenant_id, idempotency_identity, run_digest, payload) "
            "VALUES (%s, %s, %s, %s, %s)",
            (run.run_id, run.tenant_id, run.idempotency_identity,
             run.run_digest, canonical_json(run.to_dict())),
        )
        return run

    def _update_on(self, connection, run, *, expected_digest: str) -> "ActionRun":
        changed = connection.execute(
            "UPDATE action_runs SET run_digest = %s, payload = %s "
            "WHERE run_id = %s AND run_digest = %s",
            (run.run_digest, canonical_json(run.to_dict()),
             run.run_id, expected_digest),
        ).rowcount
        if changed != 1:
            from bridge.semantic_core.action_runs import ActionRunError

            raise ActionRunError("action run transition lost an optimistic concurrency race")
        return run
