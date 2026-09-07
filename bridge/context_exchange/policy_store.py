# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W03.04 — Versioned tenant context policy store and publication withdrawal.

- TenantContextPolicy values are stored as an append-only revision chain:
  every save bumps the revision, old revisions remain queryable, and a
  client that fetched revision N can detect a newer N+1 on its next pull.
- Withdrawal marks a published context packet as withdrawn: it stops
  being a valid query/training source while the audit record (and its
  release history) is preserved — withdrawal never rewrites history.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge.context_exchange.contracts import ContextExchangeError
from bridge.context_exchange.tenant_policy import TenantContextPolicy
from bridge.persistence.sqlite_support import managed_sqlite_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_tenant_policies (
    tenant_id   TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    policy_json TEXT NOT NULL,
    saved_by    TEXT NOT NULL,
    saved_at    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, revision)
);
CREATE TABLE IF NOT EXISTS context_publication_status (
    tenant_id    TEXT NOT NULL,
    packet_id    TEXT NOT NULL,
    visibility   TEXT NOT NULL,
    status       TEXT NOT NULL,
    changed_by   TEXT NOT NULL,
    changed_at   TEXT NOT NULL,
    reason       TEXT,
    PRIMARY KEY (tenant_id, packet_id, visibility)
);
"""


class PolicyStoreError(ContextExchangeError):
    pass


@dataclass(frozen=True)
class StoredPolicy:
    tenant_id: str
    revision: int
    policy: TenantContextPolicy
    saved_by: str
    saved_at: str


class SqliteTenantPolicyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    # -- versioned policy ---------------------------------------------------

    def save(
        self, policy: TenantContextPolicy, *, saved_by: str
    ) -> StoredPolicy:
        """Append a new revision. Concurrent savers are serialized by the
        immediate transaction; each save gets a strictly higher revision."""
        now = datetime.now(timezone.utc).isoformat()
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT MAX(revision) FROM context_tenant_policies WHERE tenant_id = ?",
                (policy.tenant_id,),
            ).fetchone()
            revision = (row[0] or 0) + 1
            connection.execute(
                "INSERT INTO context_tenant_policies"
                "(tenant_id, revision, policy_json, saved_by, saved_at) VALUES (?, ?, ?, ?, ?)",
                (
                    policy.tenant_id,
                    revision,
                    json.dumps(policy.as_dict() if hasattr(policy, "as_dict") else _policy_to_dict(policy), ensure_ascii=False, sort_keys=True),
                    saved_by,
                    now,
                ),
            )
        return StoredPolicy(
            tenant_id=policy.tenant_id,
            revision=revision,
            policy=policy,
            saved_by=saved_by,
            saved_at=now,
        )

    def current(self, tenant_id: str) -> StoredPolicy | None:
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT revision, policy_json, saved_by, saved_at "
                "FROM context_tenant_policies WHERE tenant_id = ? "
                "ORDER BY revision DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredPolicy(
            tenant_id=tenant_id,
            revision=row[0],
            policy=TenantContextPolicy.from_dict(json.loads(row[1])),
            saved_by=row[2],
            saved_at=row[3],
        )

    def revision(self, tenant_id: str, revision: int) -> StoredPolicy | None:
        """Fetch a specific historical revision (cache revalidation)."""
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT policy_json, saved_by, saved_at "
                "FROM context_tenant_policies WHERE tenant_id = ? AND revision = ?",
                (tenant_id, revision),
            ).fetchone()
        if row is None:
            return None
        return StoredPolicy(
            tenant_id=tenant_id,
            revision=revision,
            policy=TenantContextPolicy.from_dict(json.loads(row[0])),
            saved_by=row[1],
            saved_at=row[2],
        )

    def history(self, tenant_id: str) -> tuple[int, ...]:
        with managed_sqlite_connection(self._connect) as connection:
            rows = connection.execute(
                "SELECT revision FROM context_tenant_policies WHERE tenant_id = ? ORDER BY revision",
                (tenant_id,),
            ).fetchall()
        return tuple(r[0] for r in rows)

    # -- publication withdrawal ------------------------------------------------

    def withdraw_publication(
        self,
        repository,  # SqliteContextPacketRepository
        *,
        packet_id: str,
        tenant_id: str,
        visibility: str,
        changed_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """Mark a published packet withdrawn. The audit publication record is
        preserved; only the usable status changes."""
        publication = repository.get_publication(packet_id, tenant_id=tenant_id, visibility=visibility)
        if publication is None:
            raise PolicyStoreError(f"publication not found: {packet_id} ({visibility})")
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO context_publication_status"
                "(tenant_id, packet_id, visibility, status, changed_by, changed_at, reason) "
                "VALUES (?, ?, ?, 'withdrawn', ?, ?, ?) "
                "ON CONFLICT(tenant_id, packet_id, visibility) DO UPDATE SET "
                "status = 'withdrawn', changed_by = excluded.changed_by, "
                "changed_at = excluded.changed_at, reason = excluded.reason",
                (tenant_id, packet_id, visibility, changed_by, datetime.now(timezone.utc).isoformat(), reason),
            )
        return {
            "packet_id": packet_id,
            "visibility": visibility,
            "status": "withdrawn",
            "withdrawn_by": changed_by,
            "reason": reason,
            "release_id": publication.release_id,
            "release_digest": publication.release_digest,
            "audit_preserved": True,
        }

    def publication_status(self, tenant_id: str, packet_id: str, visibility: str) -> str:
        """'withdrawn' or 'published' (default for existing publications)."""
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT status FROM context_publication_status "
                "WHERE tenant_id = ? AND packet_id = ? AND visibility = ?",
                (tenant_id, packet_id, visibility),
            ).fetchone()
        return row[0] if row else "published"


def _policy_to_dict(policy: TenantContextPolicy) -> dict[str, Any]:
    """Serialize TenantContextPolicy back to its v1 dict contract."""
    return {
        "tenant_id": policy.tenant_id,
        "spaces": {
            name: {"draft_space_id": space.draft_space_id, "allowed_purposes": list(space.allowed_purposes)}
            for name, space in policy.spaces.items()
        },
        "source_routes": [
            {
                "source_prefix": route.source_prefix,
                "disposition": route.disposition.value,
                "draft_space": route.draft_space_id,
                "allowed_purposes": list(route.allowed_purposes),
                "sensitivity_labels": list(route.sensitivity_labels),
            }
            for route in policy.source_routes
        ],
    }
