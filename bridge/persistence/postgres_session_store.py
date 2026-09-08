"""W08.04 — PostgreSQL implementation of the Session ACL repository.

Same contract as SqliteSessionRepository (create/get/set_state/
set_visibility/upsert_acl/delete_acl with atomic acl_version bumps),
adapted to PostgreSQL (psycopg, ON CONFLICT, timestamptz).
"""

from __future__ import annotations

import dataclasses
import json
import os
from typing import Any

import psycopg

from bridge.access.session_acl import (
    Session,
    SessionAclEntry,
    SessionAclError,
    SessionState,
)
from bridge.persistence.sqlite_support import managed_sqlite_connection  # noqa: F401

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agentic_sessions (
    tenant_id   TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    acl_version INTEGER NOT NULL,
    state       TEXT NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, session_id)
);
CREATE TABLE IF NOT EXISTS agentic_session_acl (
    tenant_id    TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, session_id, subject_kind, subject_id)
);
"""


class PostgresSessionRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get(
            "AOF_SESSION_PG_DSN", "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control"
        )
        self._init_schema()

    def _connect(self) -> psycopg.Connection:
        connection = psycopg.connect(self.dsn, row_factory=None)
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(_SCHEMA)
            connection.commit()

    def create(self, session: Session) -> Session:
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO agentic_sessions(tenant_id, session_id, acl_version, state, payload) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (session.tenant_id, session.session_id, session.acl_version,
                     session.state.value, json.dumps(session.to_dict(), sort_keys=True)),
                )
                connection.commit()
            except psycopg.IntegrityError as exc:
                raise SessionAclError(
                    f"session already exists: {session.tenant_id}/{session.session_id}"
                ) from exc
        return session

    def get(self, tenant_id: str, session_id: str) -> Session | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM agentic_sessions WHERE tenant_id = %s AND session_id = %s",
                (tenant_id, session_id),
            ).fetchone()
        return Session.from_dict(json.loads(row[0])) if row else None

    def set_state(self, tenant_id: str, session_id: str, state: SessionState) -> Session:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, acl_version FROM agentic_sessions WHERE tenant_id = %s AND session_id = %s",
                (tenant_id, session_id),
            ).fetchone()
            if not row:
                raise SessionAclError(f"session not found: {tenant_id}/{session_id}")
            session = Session.from_dict(json.loads(row[0]))
            updated = dataclasses.replace(session, state=state, acl_version=session.acl_version + 1)
            connection.execute(
                "UPDATE agentic_sessions SET acl_version = %s, state = %s, payload = %s "
                "WHERE tenant_id = %s AND session_id = %s",
                (updated.acl_version, state.value,
                 json.dumps(updated.to_dict(), sort_keys=True), tenant_id, session_id),
            )
            connection.commit()
        return updated

    def set_visibility(
        self, tenant_id: str, session_id: str,
        visibility: Any, business_object_id: str | None = None,
    ) -> Session:
        import dataclasses as _dc
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, acl_version FROM agentic_sessions WHERE tenant_id = %s AND session_id = %s",
                (tenant_id, session_id),
            ).fetchone()
            if not row:
                raise SessionAclError(f"session not found: {tenant_id}/{session_id}")
            session = Session.from_dict(json.loads(row[0]))
            updated = _dc.replace(
                session, visibility=visibility,
                business_object_id=business_object_id,
                acl_version=session.acl_version + 1,
            )
            connection.execute(
                "UPDATE agentic_sessions SET acl_version = %s, payload = %s "
                "WHERE tenant_id = %s AND session_id = %s",
                (updated.acl_version, json.dumps(updated.to_dict(), sort_keys=True),
                 tenant_id, session_id),
            )
            connection.commit()
        return updated

    def list_acl(self, tenant_id: str, session_id: str) -> list[SessionAclEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM agentic_session_acl WHERE tenant_id = %s AND session_id = %s",
                (tenant_id, session_id),
            ).fetchall()
        return [SessionAclEntry.from_dict(json.loads(r[0])) for r in rows]

    def upsert_acl(self, tenant_id: str, session_id: str, entry: SessionAclEntry) -> Session:
        with self._connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT payload, acl_version FROM agentic_sessions WHERE tenant_id = %s AND session_id = %s",
                (tenant_id, session_id),
            ).fetchone()
            if not row:
                raise SessionAclError(f"session not found: {tenant_id}/{session_id}")
            session = Session.from_dict(json.loads(row[0]))
            connection.execute(
                "INSERT INTO agentic_session_acl"
                "(tenant_id, session_id, subject_kind, subject_id, payload) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, session_id, subject_kind, subject_id) "
                "DO UPDATE SET payload = excluded.payload",
                (tenant_id, session_id, entry.subject_kind, entry.subject_id,
                 json.dumps(entry.to_dict(), sort_keys=True)),
            )
            updated = dataclasses.replace(session, acl_version=session.acl_version + 1)
            connection.execute(
                "UPDATE agentic_sessions SET acl_version = %s, payload = %s "
                "WHERE tenant_id = %s AND session_id = %s",
                (updated.acl_version, json.dumps(updated.to_dict(), sort_keys=True),
                 tenant_id, session_id),
            )
            connection.commit()
        return updated

    def delete_acl(self, tenant_id: str, session_id: str, subject_kind: str, subject_id: str) -> Session:
        with self._connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT payload, acl_version FROM agentic_sessions WHERE tenant_id = %s AND session_id = %s",
                (tenant_id, session_id),
            ).fetchone()
            if not row:
                raise SessionAclError(f"session not found: {tenant_id}/{session_id}")
            session = Session.from_dict(json.loads(row[0]))
            connection.execute(
                "DELETE FROM agentic_session_acl WHERE tenant_id = %s AND session_id = %s "
                "AND subject_kind = %s AND subject_id = %s",
                (tenant_id, session_id, subject_kind, subject_id),
            )
            updated = dataclasses.replace(session, acl_version=session.acl_version + 1)
            connection.execute(
                "UPDATE agentic_sessions SET acl_version = %s, payload = %s "
                "WHERE tenant_id = %s AND session_id = %s",
                (updated.acl_version, json.dumps(updated.to_dict(), sort_keys=True),
                 tenant_id, session_id),
            )
            connection.commit()
        return updated
