# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""Governed migration of legacy IAM data into the authoritative store."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from bridge.access.revocations import RevocationRegistry
from bridge.auth.models import Permission, ResourceType, Role, RoleType, Action, User
from bridge.auth.models import UserRoleAssignment
from bridge.persistence.sqlite_support import managed_sqlite_connection
from bridge.tenant.manager import Tenant, TenantConfig, TenantStatus

LEGACY_SCHEMA = "aof.legacy-iam/v1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS iam_migration_quarantine (
    record_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    legacy_id TEXT,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    quarantined_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iam_admin_bootstrap (
    invitation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    email TEXT NOT NULL,
    token_digest TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
"""


class IamMigrationError(ValueError):
    """The legacy IAM payload cannot be migrated safely."""


@dataclass(frozen=True)
class AdminInvitation:
    invitation_id: str
    tenant_id: str
    email: str
    token: str
    expires_at: str


@dataclass(frozen=True)
class MigrationReport:
    source_digest: str
    tenants_migrated: int
    users_migrated: int
    roles_migrated: int
    assignments_migrated: int
    quarantined: int
    bootstrap_required: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "aof.iam-migration-report/v1",
            "source_digest": self.source_digest,
            "tenants_migrated": self.tenants_migrated,
            "users_migrated": self.users_migrated,
            "roles_migrated": self.roles_migrated,
            "assignments_migrated": self.assignments_migrated,
            "quarantined": self.quarantined,
            "bootstrap_required": list(self.bootstrap_required),
        }


class IamMigrationService:
    """Import only explicitly mapped identities and quarantine unsafe rows."""

    def __init__(
        self,
        *,
        target_store: Any,
        quarantine_path: str | Path,
        revocations: RevocationRegistry,
    ) -> None:
        self.target = target_store
        self.path = Path(quarantine_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.revocations = revocations
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _canonical(payload: Mapping[str, Any]) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def _quarantine(self, entity_type: str, payload: Mapping[str, Any], reason: str) -> None:
        encoded = self._canonical(payload)
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute(
                "INSERT INTO iam_migration_quarantine VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"quarantine:{uuid.uuid4()}",
                    entity_type,
                    str(payload.get("id") or payload.get("user_id") or "") or None,
                    reason,
                    encoded.decode(),
                    "sha256:" + hashlib.sha256(encoded).hexdigest(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def quarantine_records(self) -> list[dict[str, Any]]:
        with managed_sqlite_connection(self._connect) as connection:
            rows = connection.execute(
                "SELECT entity_type, legacy_id, reason, payload_digest "
                "FROM iam_migration_quarantine ORDER BY quarantined_at, record_id"
            ).fetchall()
        return [
            {"entity_type": row[0], "legacy_id": row[1], "reason": row[2], "payload_digest": row[3]}
            for row in rows
        ]

    def issue_admin_invitation(
        self, *, tenant_id: str, email: str, lifetime_minutes: int = 30
    ) -> AdminInvitation:
        if not tenant_id.strip() or "@" not in email or lifetime_minutes <= 0:
            raise IamMigrationError("valid tenant, email, and invitation lifetime are required")
        if tenant_id not in self.target.tenants:
            raise IamMigrationError("bootstrap tenant does not exist")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        invitation_id = f"bootstrap:{uuid.uuid4()}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=lifetime_minutes)
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute(
                "INSERT INTO iam_admin_bootstrap"
                "(invitation_id, tenant_id, email, token_digest, expires_at) VALUES (?, ?, ?, ?, ?)",
                (invitation_id, tenant_id, email.strip().lower(), digest, expires_at.isoformat()),
            )
        return AdminInvitation(invitation_id, tenant_id, email.strip().lower(), token, expires_at.isoformat())

    def redeem_admin_invitation(self, token: str) -> dict[str, str]:
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT invitation_id, tenant_id, email, expires_at, consumed_at "
                "FROM iam_admin_bootstrap WHERE token_digest = ?",
                (digest,),
            ).fetchone()
            if row is None or row[4] is not None:
                raise IamMigrationError("bootstrap invitation is invalid or already consumed")
            if datetime.fromisoformat(row[3]) <= now:
                raise IamMigrationError("bootstrap invitation has expired")
            connection.execute(
                "UPDATE iam_admin_bootstrap SET consumed_at = ? WHERE invitation_id = ?",
                (now.isoformat(), row[0]),
            )
        return {"invitation_id": row[0], "tenant_id": row[1], "email": row[2]}

    def migrate(
        self,
        payload: Mapping[str, Any],
        *,
        tenant_ids: Mapping[str, str],
        user_ids: Mapping[str, str],
        role_ids: Mapping[str, str],
    ) -> MigrationReport:
        if payload.get("schema_version") != LEGACY_SCHEMA:
            raise IamMigrationError(f"legacy schema must be {LEGACY_SCHEMA}")
        for name, mapping in (("tenant", tenant_ids), ("user", user_ids), ("role", role_ids)):
            targets = list(mapping.values())
            if any(not str(value).strip() for value in targets) or len(set(targets)) != len(targets):
                raise IamMigrationError(f"{name} id mapping targets must be non-empty and unique")
        digest = "sha256:" + hashlib.sha256(self._canonical(payload)).hexdigest()
        counts = {"tenant": 0, "user": 0, "role": 0, "assignment": 0}
        bootstrap_required: list[str] = []
        quarantine_before = len(self.quarantine_records())

        for raw in payload.get("tenants", []):
            mapped = tenant_ids.get(str(raw.get("id")))
            if not mapped:
                self._quarantine("tenant", raw, "tenant id has no approved mapping")
                continue
            old_admin = raw.get("admin_user_id")
            mapped_admin = user_ids.get(str(old_admin)) if old_admin else None
            tenant = Tenant(
                id=mapped,
                name=str(raw["name"]),
                slug=str(raw["slug"]),
                status=TenantStatus(str(raw.get("status", "pending"))),
                config=TenantConfig.from_dict(dict(raw.get("config", {}))),
                admin_user_id=mapped_admin,
                metadata={"legacy_id": str(raw["id"]), "migration_source": digest},
            )
            self.target.tenants[mapped] = tenant
            counts["tenant"] += 1
            if mapped_admin is None:
                bootstrap_required.append(mapped)

        for raw in payload.get("users", []):
            mapped = user_ids.get(str(raw.get("id")))
            mapped_tenant = tenant_ids.get(str(raw.get("tenant_id")))
            if not mapped or not mapped_tenant or mapped_tenant not in self.target.tenants:
                self._quarantine("user", raw, "user or tenant id has no approved mapping")
                continue
            if bool(raw.get("is_superuser")):
                self._quarantine("user", raw, "legacy superuser elevation requires independent approval")
                continue
            self.target.users[mapped] = User(
                id=mapped,
                username=str(raw["username"]),
                email=raw.get("email"),
                tenant_id=mapped_tenant,
                is_active=bool(raw.get("is_active", True)),
                metadata={"legacy_id": str(raw["id"]), "migration_source": digest},
            )
            counts["user"] += 1

        for tenant_id in tenant_ids.values():
            tenant = self.target.tenants.get(tenant_id)
            if tenant is None or tenant.admin_user_id is None:
                continue
            admin = self.target.users.get(tenant.admin_user_id)
            if admin is None or admin.tenant_id != tenant.id:
                tenant.admin_user_id = None
                self.target.tenants[tenant.id] = tenant
                bootstrap_required.append(tenant.id)

        for raw in payload.get("roles", []):
            mapped = role_ids.get(str(raw.get("id")))
            mapped_tenant = tenant_ids.get(str(raw.get("tenant_id")))
            if not mapped or not mapped_tenant or mapped_tenant not in self.target.tenants:
                self._quarantine("role", raw, "role or tenant id has no approved mapping")
                continue
            permissions = [
                Permission(ResourceType(item["resource_type"]), Action(item["action"]), item.get("resource_id"))
                for item in raw.get("permissions", [])
            ]
            self.target.roles[mapped] = Role(
                id=mapped,
                name=str(raw["name"]),
                role_type=RoleType(str(raw["role_type"])),
                tenant_id=mapped_tenant,
                permissions=permissions,
                description="Migrated through governed IAM mapping",
            )
            counts["role"] += 1

        existing = {
            (item.user_id, item.role_id, item.resource_type, item.resource_id)
            for item in self.target.assignments
        }
        for raw in payload.get("assignments", []):
            user_id = user_ids.get(str(raw.get("user_id")))
            role_id = role_ids.get(str(raw.get("role_id")))
            if not user_id or not role_id or user_id not in self.target.users or role_id not in self.target.roles:
                self._quarantine("assignment", raw, "assignment references an unknown or quarantined member")
                continue
            if self.revocations.is_revoked("subject", user_id):
                self._quarantine("assignment", raw, "subject is revoked in the live authority log")
                continue
            user = self.target.users[user_id]
            role = self.target.roles[role_id]
            if user.tenant_id != role.tenant_id:
                self._quarantine("assignment", raw, "cross-tenant role assignment is forbidden")
                continue
            assignment = UserRoleAssignment(
                user_id=user_id,
                role_id=role_id,
                resource_type=ResourceType(str(raw["resource_type"])),
                resource_id=raw.get("resource_id"),
                granted_by="iam-migration",
            )
            key = (assignment.user_id, assignment.role_id, assignment.resource_type, assignment.resource_id)
            if key not in existing:
                self.target.assignments.append(assignment)
                existing.add(key)
                counts["assignment"] += 1

        return MigrationReport(
            source_digest=digest,
            tenants_migrated=counts["tenant"],
            users_migrated=counts["user"],
            roles_migrated=counts["role"],
            assignments_migrated=counts["assignment"],
            quarantined=len(self.quarantine_records()) - quarantine_before,
            bootstrap_required=tuple(sorted(set(bootstrap_required))),
        )
