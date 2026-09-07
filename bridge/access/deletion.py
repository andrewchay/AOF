# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W06.05 — Deletion, legal hold and immutable-audit coordination.

Deletion contract (plan section 9.2):
- Sensitive payloads live OUTSIDE the immutable audit trail, in an
  encrypted payload vault. The audit chain stores only references and
  digests, so a deletion destroys vault entries + key material and
  records a tombstone — it NEVER rewrites existing chain hashes.
- A legal hold overrides deletion: conflicting requests are recorded as
  ``pending_legal_review`` with the data still in place. Nothing is
  silently deleted and nothing is falsely reported deleted.
- Every completed deletion returns a receipt binding the tombstone id,
  the policy revision that justified it and a vault-destruction proof.
- Restores/replays cannot resurrect deleted payloads: the vault entry is
  gone, and the tombstone blocks re-materialization.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from bridge.persistence.sqlite_support import managed_sqlite_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payload_vault (
    data_key     TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    ciphertext   BLOB NOT NULL,
    payload_digest TEXT NOT NULL,
    key_seed     BLOB NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS deletion_tombstones (
    tombstone_id  TEXT PRIMARY KEY,
    data_key      TEXT NOT NULL,
    tenant_id     TEXT NOT NULL,
    reason        TEXT NOT NULL,
    requested_by  TEXT NOT NULL,
    policy_id     TEXT,
    policy_revision INTEGER,
    status        TEXT NOT NULL,
    vault_proof   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS legal_holds (
    hold_id      TEXT PRIMARY KEY,
    data_key     TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    reason       TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    released_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_holds_active
    ON legal_holds(data_key) WHERE active = 1;
"""


class DeletionError(ValueError):
    pass


class DeletionStatus(str, Enum):
    PENDING_LEGAL_REVIEW = "pending_legal_review"
    DELETED = "deleted"


@dataclass(frozen=True)
class Tombstone:
    tombstone_id: str
    data_key: str
    tenant_id: str
    reason: str
    requested_by: str
    status: str
    policy_id: str | None = None
    policy_revision: int | None = None
    vault_proof: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tombstone_id": self.tombstone_id,
            "data_key": self.data_key,
            "tenant_id": self.tenant_id,
            "reason": self.reason,
            "requested_by": self.requested_by,
            "status": self.status,
            "policy_id": self.policy_id,
            "policy_revision": self.policy_revision,
            "vault_proof": self.vault_proof,
            "created_at": self.created_at,
        }


class PayloadVault:
    """Encrypted, deletion-controlled storage for sensitive payloads.

    Per-entry derived key: the stored ``key_seed`` is combined with the
    master key, so destroying the entry (seed + ciphertext) makes the
    payload unrecoverable even if the master key is known later.
    """

    def __init__(self, path: str | Path | None = None, *, master_key: bytes | None = None) -> None:
        import hashlib
        import os

        from cryptography.fernet import Fernet

        configured = path or os.environ.get("AOF_PAYLOAD_VAULT_FILE")
        self.path = Path(configured or "data/access/payload_vault.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if master_key is None:
            key_file = self.path.with_suffix(".master")
            if key_file.exists():
                master_key = key_file.read_bytes()
            else:
                master_key = Fernet.generate_key()
                key_file.write_bytes(master_key)
            try:
                key_file.chmod(0o600)
            except OSError:  # pragma: no cover - platform dependent
                pass
        self._master_key = master_key
        self._hashlib = hashlib
        self._fernet = Fernet
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _entry_key(self, data_key: str, key_seed: bytes) -> Any:
        from cryptography.fernet import Fernet
        import hashlib

        digest = hashlib.sha256(self._master_key + key_seed + data_key.encode()).digest()
        return Fernet(__import__("base64").urlsafe_b64encode(digest))

    def put(self, *, data_key: str, tenant_id: str, payload: Mapping[str, Any]) -> str:
        """Store an encrypted payload; returns its content digest."""
        import os

        plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        payload_digest = "sha256:" + self._hashlib.sha256(plaintext).hexdigest()
        key_seed = os.urandom(32)
        token = self._entry_key(data_key, key_seed).encrypt(plaintext)
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute(
                "INSERT INTO payload_vault(data_key, tenant_id, ciphertext, payload_digest, key_seed) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(data_key) DO UPDATE SET "
                "ciphertext = excluded.ciphertext, payload_digest = excluded.payload_digest, "
                "key_seed = excluded.key_seed",
                (data_key, tenant_id, token, payload_digest, key_seed),
            )
        return payload_digest

    def get(self, data_key: str) -> dict[str, Any] | None:
        row = self._fetch(data_key)
        if row is None:
            return None
        _ciphertext, _digest, key_seed = row
        plaintext = self._entry_key(data_key, key_seed).decrypt(_ciphertext)
        return json.loads(plaintext)

    def digest(self, data_key: str) -> str | None:
        row = self._fetch(data_key)
        return row[1] if row else None

    def _fetch(self, data_key: str) -> tuple[bytes, str, bytes] | None:
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT ciphertext, payload_digest, key_seed FROM payload_vault WHERE data_key = ?",
                (data_key,),
            ).fetchone()
        return (row[0], row[1], row[2]) if row else None

    def destroy(self, data_key: str) -> dict[str, Any]:
        """Irreversibly remove the ciphertext AND its key material."""
        with managed_sqlite_connection(self._connect) as connection:
            cursor = connection.execute(
                "DELETE FROM payload_vault WHERE data_key = ?", (data_key,)
            )
            existed = cursor.rowcount > 0
        digest_before = self.digest(data_key)
        return {
            "data_key": data_key,
            "destroyed": existed,
            "vault_proof": (
                f"entry-purged:{data_key}:{datetime.now(timezone.utc).isoformat()}"
                if existed
                else f"already-absent:{data_key}"
            ),
            "post_state_digest": digest_before,
        }


class DeletionCoordinator:
    """Tombstone + legal-hold coordination over the payload vault."""

    def __init__(self, vault: PayloadVault, path: str | Path | None = None) -> None:
        import os

        configured = path or os.environ.get("AOF_DELETION_DB")
        self.db_path = Path(configured or "data/access/deletion.sqlite")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.vault = vault
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    # -- legal holds -------------------------------------------------------

    def place_legal_hold(self, *, data_key: str, tenant_id: str, reason: str) -> str:
        hold_id = f"hold:{uuid.uuid4()}"
        with managed_sqlite_connection(self._connect) as connection:
            try:
                connection.execute(
                    "INSERT INTO legal_holds(hold_id, data_key, tenant_id, reason) VALUES (?, ?, ?, ?)",
                    (hold_id, data_key, tenant_id, reason),
                )
            except sqlite3.IntegrityError as exc:
                raise DeletionError(f"legal hold already active for {data_key}") from exc
        return hold_id

    def release_legal_hold(self, data_key: str) -> bool:
        with managed_sqlite_connection(self._connect) as connection:
            cursor = connection.execute(
                "UPDATE legal_holds SET active = 0, released_at = datetime('now') "
                "WHERE data_key = ? AND active = 1",
                (data_key,),
            )
            return cursor.rowcount > 0

    def has_active_hold(self, data_key: str) -> bool:
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT 1 FROM legal_holds WHERE data_key = ? AND active = 1",
                (data_key,),
            ).fetchone()
            return row is not None

    # -- deletion ------------------------------------------------------------

    def request_deletion(
        self,
        *,
        data_key: str,
        tenant_id: str,
        reason: str,
        requested_by: str,
        policy_id: str | None = None,
        policy_revision: int | None = None,
    ) -> Tombstone:
        """Request deletion. With an active legal hold the request is
        recorded as pending_legal_review and NOTHING is deleted."""
        if self.has_active_hold(data_key):
            tombstone = Tombstone(
                tombstone_id=f"tomb:{uuid.uuid4()}",
                data_key=data_key,
                tenant_id=tenant_id,
                reason=reason,
                requested_by=requested_by,
                status=DeletionStatus.PENDING_LEGAL_REVIEW.value,
                policy_id=policy_id,
                policy_revision=policy_revision,
            )
            self._record_tombstone(tombstone)
            return tombstone

        proof = self.vault.destroy(data_key)
        tombstone = Tombstone(
            tombstone_id=f"tomb:{uuid.uuid4()}",
            data_key=data_key,
            tenant_id=tenant_id,
            reason=reason,
            requested_by=requested_by,
            status=DeletionStatus.DELETED.value,
            policy_id=policy_id,
            policy_revision=policy_revision,
            vault_proof=proof["vault_proof"],
        )
        self._record_tombstone(tombstone)
        return tombstone

    def _record_tombstone(self, tombstone: Tombstone) -> None:
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute(
                "INSERT INTO deletion_tombstones"
                "(tombstone_id, data_key, tenant_id, reason, requested_by, policy_id, "
                "policy_revision, status, vault_proof, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tombstone.tombstone_id,
                    tombstone.data_key,
                    tombstone.tenant_id,
                    tombstone.reason,
                    tombstone.requested_by,
                    tombstone.policy_id,
                    tombstone.policy_revision,
                    tombstone.status,
                    tombstone.vault_proof,
                    # W06.06: explicit isoformat timestamp - SQLite's
                    # datetime('now') uses ' ' separator and would never
                    # compare correctly against isoformat cutoffs
                    tombstone.created_at,
                ),
            )

    def get_tombstone(self, tombstone_id: str) -> Tombstone | None:
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT tombstone_id, data_key, tenant_id, reason, requested_by, policy_id, "
                "policy_revision, status, vault_proof, created_at "
                "FROM deletion_tombstones WHERE tombstone_id = ?",
                (tombstone_id,),
            ).fetchone()
        if row is None:
            return None
        return Tombstone(
            tombstone_id=row[0], data_key=row[1], tenant_id=row[2], reason=row[3],
            requested_by=row[4], policy_id=row[5], policy_revision=row[6],
            status=row[7], vault_proof=row[8], created_at=row[9],
        )

    def pending_legal_reviews(self, tenant_id: str | None = None) -> list[Tombstone]:
        query = (
            "SELECT tombstone_id, data_key, tenant_id, reason, requested_by, policy_id, "
            "policy_revision, status, vault_proof, created_at "
            "FROM deletion_tombstones WHERE status = ?"
        )
        params: tuple = (DeletionStatus.PENDING_LEGAL_REVIEW.value,)
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params = params + (tenant_id,)
        with managed_sqlite_connection(self._connect) as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            Tombstone(
                tombstone_id=r[0], data_key=r[1], tenant_id=r[2], reason=r[3],
                requested_by=r[4], policy_id=r[5], policy_revision=r[6],
                status=r[7], vault_proof=r[8], created_at=r[9],
            )
            for r in rows
        ]

    def deletion_receipt(self, tombstone_id: str) -> dict[str, Any]:
        tombstone = self.get_tombstone(tombstone_id)
        if tombstone is None:
            raise DeletionError(f"tombstone not found: {tombstone_id}")
        return {
            "action": "payload_deletion",
            **tombstone.to_dict(),
            "vault_readable_after": self.vault.get(tombstone.data_key) is not None,
        }
