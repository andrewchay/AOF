# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W06.01 — SQLite repository for Session + ACL entries.

Tables:
- agentic_sessions(tenant_id, session_id, payload, acl_version, state,
  UNIQUE(tenant_id, session_id))
- agentic_session_acl(tenant_id, session_id, subject_kind, subject_id,
  payload, UNIQUE(tenant_id, session_id, subject_kind, subject_id))

ACL mutations bump acl_version inside the same transaction, so a reader
authorizing against version N is never surprised by a silent change.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from bridge.access.session_acl import (
    Session,
    SessionAclEntry,
    SessionAclError,
    SessionState,
)
from bridge.persistence.sqlite_support import managed_sqlite_connection

if TYPE_CHECKING:  # pragma: no cover
    from bridge.access.session_acl import SessionVisibility

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


class SqliteSessionRepository:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    # -- sessions ---------------------------------------------------------

    def create(self, session: Session) -> Session:
        with managed_sqlite_connection(self._connect) as connection:
            try:
                connection.execute(
                    "INSERT INTO agentic_sessions(tenant_id, session_id, acl_version, state, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        session.tenant_id,
                        session.session_id,
                        session.acl_version,
                        session.state.value,
                        json.dumps(session.to_dict(), sort_keys=True),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SessionAclError(
                    f"session already exists: {session.tenant_id}/{session.session_id}"
                ) from exc
        return session

    def get(self, tenant_id: str, session_id: str) -> Session | None:
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT payload FROM agentic_sessions WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            ).fetchone()
        return Session.from_dict(json.loads(row[0])) if row else None

    def set_state(self, tenant_id: str, session_id: str, state: SessionState) -> Session:
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT payload FROM agentic_sessions WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            ).fetchone()
            if not row:
                raise SessionAclError(f"session not found: {tenant_id}/{session_id}")
            session = Session.from_dict(json.loads(row[0]))
            updated = dataclasses.replace(
                session, state=state, acl_version=session.acl_version + 1
            )
            connection.execute(
                "UPDATE agentic_sessions SET acl_version = ?, state = ?, payload = ? "
                "WHERE tenant_id = ? AND session_id = ?",
                (
                    updated.acl_version,
                    state.value,
                    json.dumps(updated.to_dict(), sort_keys=True),
                    tenant_id,
                    session_id,
                ),
            )
        return updated

    def set_visibility(
        self,
        tenant_id: str,
        session_id: str,
        visibility: "SessionVisibility",
        business_object_id: str | None = None,
    ) -> Session:
        """Update visibility and bump acl_version atomically."""
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload, acl_version FROM agentic_sessions WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            ).fetchone()
            if not row:
                raise SessionAclError(f"session not found: {tenant_id}/{session_id}")
            session = Session.from_dict(json.loads(row[0]))
            updated = dataclasses.replace(
                session,
                visibility=visibility,
                business_object_id=business_object_id,
                acl_version=session.acl_version + 1,
            )
            connection.execute(
                "UPDATE agentic_sessions SET acl_version = ?, payload = ? WHERE tenant_id = ? AND session_id = ?",
                (
                    updated.acl_version,
                    json.dumps(updated.to_dict(), sort_keys=True),
                    tenant_id,
                    session_id,
                ),
            )
        return updated

    # -- ACL entries --------------------------------------------------------

    def list_acl(self, tenant_id: str, session_id: str) -> list[SessionAclEntry]:
        with managed_sqlite_connection(self._connect) as connection:
            rows = connection.execute(
                "SELECT payload FROM agentic_session_acl WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            ).fetchall()
        return [SessionAclEntry.from_dict(json.loads(r[0])) for r in rows]

    def upsert_acl(
        self, tenant_id: str, session_id: str, entry: SessionAclEntry
    ) -> Session:
        """Insert/replace an ACL entry and bump acl_version atomically."""
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload, acl_version FROM agentic_sessions WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            ).fetchone()
            if not row:
                raise SessionAclError(f"session not found: {tenant_id}/{session_id}")
            session = Session.from_dict(json.loads(row[0]))
            connection.execute(
                "INSERT INTO agentic_session_acl(tenant_id, session_id, subject_kind, subject_id, payload) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, session_id, subject_kind, subject_id) "
                "DO UPDATE SET payload = excluded.payload",
                (
                    tenant_id,
                    session_id,
                    entry.subject_kind,
                    entry.subject_id,
                    json.dumps(entry.to_dict(), sort_keys=True),
                ),
            )
            updated = dataclasses.replace(session, acl_version=session.acl_version + 1)
            connection.execute(
                "UPDATE agentic_sessions SET acl_version = ?, payload = ? WHERE tenant_id = ? AND session_id = ?",
                (
                    updated.acl_version,
                    json.dumps(updated.to_dict(), sort_keys=True),
                    tenant_id,
                    session_id,
                ),
            )
        return updated

    def delete_acl(self, tenant_id: str, session_id: str, subject_kind: str, subject_id: str) -> Session:
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload, acl_version FROM agentic_sessions WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            ).fetchone()
            if not row:
                raise SessionAclError(f"session not found: {tenant_id}/{session_id}")
            session = Session.from_dict(json.loads(row[0]))
            connection.execute(
                "DELETE FROM agentic_session_acl WHERE tenant_id = ? AND session_id = ? AND subject_kind = ? AND subject_id = ?",
                (tenant_id, session_id, subject_kind, subject_id),
            )
            updated = dataclasses.replace(session, acl_version=session.acl_version + 1)
            connection.execute(
                "UPDATE agentic_sessions SET acl_version = ?, payload = ? WHERE tenant_id = ? AND session_id = ?",
                (
                    updated.acl_version,
                    json.dumps(updated.to_dict(), sort_keys=True),
                    tenant_id,
                    session_id,
                ),
            )
        return updated
