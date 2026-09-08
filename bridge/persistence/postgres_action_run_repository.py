"""W08.04 — PostgreSQL implementation of the action-run repository.

Same contract as SqliteActionRunRepository (put_new with idempotency,
update with optimistic concurrency, get), adapted to PostgreSQL:
- idempotency via UNIQUE constraint + ON CONFLICT (PG-native)
- optimistic concurrency via run_digest match (same as SQLite)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bridge.semantic_core.action_runs import ActionRun, ActionRunError
from bridge.semantic_core.canonical import canonical_json


_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_runs (
    run_id               TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL,
    idempotency_identity TEXT NOT NULL UNIQUE,
    run_digest           TEXT NOT NULL,
    payload              TEXT NOT NULL
);
"""


class PostgresActionRunRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get(
            "AOF_ACTION_PG_DSN", "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control"
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

    def get_by_identity(self, identity: str) -> ActionRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM action_runs WHERE idempotency_identity = %s",
                (identity,),
            ).fetchone()
        return self.get(str(row["run_id"])) if row is not None else None  # type: ignore[call-overload]

    def get(self, run_id: str) -> ActionRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_digest, payload FROM action_runs WHERE run_id = %s", (run_id,)
            ).fetchone()
        if row is None:
            return None
        from bridge.semantic_core.action_runs import ActionRun

        return ActionRun.from_dict(json.loads(row["payload"]))  # type: ignore[call-overload]

    def put_new(self, run: ActionRun) -> ActionRun:
        with self._connect() as connection:
            connection.execute("SET LOCAL lock_timeout = '5s'")
            return self._put_new_on(connection, run)

    def _put_new_on(self, connection, run: ActionRun) -> ActionRun:
        row = connection.execute(
            "SELECT payload FROM action_runs WHERE idempotency_identity = %s",
            (run.idempotency_identity,),
        ).fetchone()
        if row is not None:
            current = ActionRun.from_dict(json.loads(row["payload"]))
            if current.plan.get("plan_digest") != run.plan.get("plan_digest"):
                raise ActionRunError("idempotency key is already bound to another plan")
            return current
        connection.execute(
            "INSERT INTO action_runs (run_id, tenant_id, idempotency_identity, run_digest, payload) "
            "VALUES (%s, %s, %s, %s, %s)",
            (run.run_id, run.tenant_id, run.idempotency_identity,
             run.run_digest, canonical_json(run.to_dict())),
        )
        return run

    def update(self, run: ActionRun, *, expected_digest: str) -> ActionRun:
        with self._connect() as connection:
            connection.execute("SET LOCAL lock_timeout = '5s'")
            return self._update_on(connection, run, expected_digest=expected_digest)

    def _update_on(self, connection, run: ActionRun, *, expected_digest: str) -> ActionRun:
        changed = connection.execute(
            "UPDATE action_runs SET run_digest = %s, payload = %s "
            "WHERE run_id = %s AND run_digest = %s",
            (run.run_digest, canonical_json(run.to_dict()), run.run_id, expected_digest),
        ).rowcount
        if changed != 1:
            raise ActionRunError("action run transition lost an optimistic concurrency race")
        return run

    def verify_all(self) -> dict[str, Any]:
        errors: list[str] = []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, tenant_id, idempotency_identity, run_digest, payload FROM action_runs"
            ).fetchall()
        for row in rows:
            try:
                run = ActionRun.from_dict(json.loads(row["payload"]))  # type: ignore[call-overload]
                if (run.run_id != row["run_id"]  # type: ignore[call-overload]
                        or run.run_digest != row["run_digest"]):  # type: ignore[call-overload]
                    raise ActionRunError("indexed ActionRun identity mismatch")
            except Exception as exc:
                errors.append(f"run/{row['run_id']}: {exc}")  # type: ignore[call-overload]
        return {"valid": not errors, "action_run_count": len(rows), "errors": errors}

    def backup_to(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        import subprocess

        subprocess.run(
            ["pg_dump", "--data-only", "--table=action_runs",
             "--dbname=" + self.dsn, "--file=" + str(target)],
            check=True,
        )
        return target
