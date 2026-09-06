"""W06.03/W04.04 — Persistent revocation registry.

A SQLite-backed set of revoked identities (subjects, sessions, tenants).
Queued background tasks and token-issuing paths consult this registry at
*decision time* so that a revocation takes effect even for work that was
authorized before it happened.

Kinds:
- 'subject' : a principal subject id (user / service account)
- 'session' : an agentic session id
- 'tenant'  : a whole tenant (suspend semantics)

The registry survives restarts and is shared across processes via the
SQLite file (WAL, managed connections per W09.01).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bridge.persistence.sqlite_support import managed_sqlite_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS access_revocations (
    kind       TEXT NOT NULL,
    identity   TEXT NOT NULL,
    reason     TEXT,
    revoked_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (kind, identity)
);
"""

_VALID_KINDS = {"subject", "session", "tenant"}


class RevocationError(ValueError):
    pass


class RevocationRegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or __import__("os").environ.get("AOF_REVOCATIONS_FILE")
        self.path = Path(configured or "data/access/revocations.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def revoke(self, kind: str, identity: str, *, reason: str | None = None) -> None:
        if kind not in _VALID_KINDS:
            raise RevocationError(f"unknown revocation kind: {kind}")
        if not identity or not identity.strip():
            raise RevocationError("identity must be a non-empty string")
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute(
                "INSERT INTO access_revocations(kind, identity, reason, revoked_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(kind, identity) DO UPDATE SET "
                "reason = excluded.reason, revoked_at = excluded.revoked_at",
                (
                    kind,
                    identity.strip(),
                    reason,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def unrevoke(self, kind: str, identity: str) -> bool:
        if kind not in _VALID_KINDS:
            raise RevocationError(f"unknown revocation kind: {kind}")
        with managed_sqlite_connection(self._connect) as connection:
            cursor = connection.execute(
                "DELETE FROM access_revocations WHERE kind = ? AND identity = ?",
                (kind, identity.strip()),
            )
            return cursor.rowcount > 0

    def is_revoked(self, kind: str, identity: str) -> bool:
        if kind not in _VALID_KINDS:
            raise RevocationError(f"unknown revocation kind: {kind}")
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT 1 FROM access_revocations WHERE kind = ? AND identity = ?",
                (kind, identity.strip()),
            ).fetchone()
            return row is not None
