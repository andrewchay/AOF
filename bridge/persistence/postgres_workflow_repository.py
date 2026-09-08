"""W08.04 — PostgreSQL implementation of the workflow-run repository.

Same contract as SqliteWorkflowRunRepository (get, get_by_identity,
list_runs, put_new, update with optimistic concurrency), adapted to
PostgreSQL (psycopg, ON CONFLICT, timestamptz).
"""

from __future__ import annotations

import json
import os

import psycopg

from bridge.semantic_core.canonical import canonical_json
from bridge.semantic_core.workflows import WorkflowRun, WorkflowRunError


_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id     TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    identity   TEXT NOT NULL UNIQUE,
    run_digest TEXT NOT NULL,
    payload    TEXT NOT NULL
);
"""


class PostgresWorkflowRunRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get(
            "AOF_WORKFLOW_PG_DSN", "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control"
        )
        self._init_schema()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=None)

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(_SCHEMA)
            connection.commit()

    def get(self, run_id: str, *, tenant_id: str | None = None) -> WorkflowRun | None:
        query = "SELECT payload FROM workflow_runs WHERE run_id = %s"
        params: list[str] = [run_id]
        if tenant_id is not None:
            query += " AND tenant_id = %s"
            params.append(tenant_id)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return WorkflowRun.from_dict(json.loads(row[0])) if row else None

    def get_by_identity(self, identity: str) -> WorkflowRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM workflow_runs WHERE identity = %s", (identity,)
            ).fetchone()
        return WorkflowRun.from_dict(json.loads(row[0])) if row else None

    def list_runs(self, *, tenant_id: str) -> list[WorkflowRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM workflow_runs WHERE tenant_id = %s ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [WorkflowRun.from_dict(json.loads(r[0])) for r in rows]

    def put_new(self, run: WorkflowRun) -> WorkflowRun:
        payload_json = canonical_json(run.to_dict())
        identity = json.loads(payload_json).get("identity", run.run_id)
        with self._connect() as connection:
            connection.execute("SET LOCAL lock_timeout = '5s'")
            existing = connection.execute(
                "SELECT payload FROM workflow_runs WHERE identity = %s",
                (identity,),
            ).fetchone()
            if existing is not None:
                return WorkflowRun.from_dict(json.loads(existing[0]))
            connection.execute(
                "INSERT INTO workflow_runs (run_id, tenant_id, identity, run_digest, payload) "
                "VALUES (%s, %s, %s, %s, %s)",
                (run.run_id, run.tenant_id, identity,
                 json.loads(payload_json).get("run_digest", ""),
                 payload_json),
            )
            connection.commit()
        return run

    def update(self, run: WorkflowRun, *, expected_digest: str) -> WorkflowRun:

        payload_json = canonical_json(run.to_dict())
        with self._connect() as connection:
            connection.execute("SET LOCAL lock_timeout = '5s'")
            changed = connection.execute(
                "UPDATE workflow_runs SET run_digest = %s, payload = %s "
                "WHERE run_id = %s AND run_digest = %s",
                (json.loads(payload_json).get("run_digest", ""),
                 payload_json, run.run_id, expected_digest),
            ).rowcount
            if changed != 1:
                raise WorkflowRunError("workflow run transition lost an optimistic concurrency race")
            connection.commit()
        return run
