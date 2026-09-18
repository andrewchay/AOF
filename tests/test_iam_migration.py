# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""W04.06 governed IAM migration and passwordless administrator bootstrap."""

from __future__ import annotations

import sqlite3

import pytest

from bridge.access.iam_migration import IamMigrationError, IamMigrationService
from bridge.access.revocations import RevocationRegistry
from bridge.persistence.sqlite_iam_store import SqliteIamStore


def _payload():
    return {
        "schema_version": "aof.legacy-iam/v1",
        "tenants": [{"id": "legacy-t", "name": "Acme", "slug": "acme-migrated", "status": "active", "admin_user_id": "legacy-admin"}],
        "users": [
            {"id": "legacy-admin", "username": "admin", "email": "admin@example.com", "tenant_id": "legacy-t"},
            {"id": "unknown-user", "username": "unknown", "tenant_id": "missing-tenant"},
        ],
        "roles": [{"id": "legacy-role", "name": "Admin", "role_type": "admin", "tenant_id": "legacy-t", "permissions": [{"resource_type": "system", "action": "admin"}]}],
        "assignments": [
            {"user_id": "legacy-admin", "role_id": "legacy-role", "resource_type": "system"},
            {"user_id": "unknown-user", "role_id": "legacy-role", "resource_type": "system"},
        ],
    }


def _service(tmp_path):
    revocations = RevocationRegistry(tmp_path / "revocations.sqlite")
    service = IamMigrationService(
        target_store=SqliteIamStore(tmp_path / "iam.sqlite"),
        quarantine_path=tmp_path / "migration.sqlite",
        revocations=revocations,
    )
    return service, revocations


def test_explicit_id_mapping_and_unknown_members_are_quarantined(tmp_path):
    service, _ = _service(tmp_path)
    report = service.migrate(
        _payload(),
        tenant_ids={"legacy-t": "tenant-acme"},
        user_ids={"legacy-admin": "subject-admin"},
        role_ids={"legacy-role": "role-admin"},
    )
    assert report.tenants_migrated == report.users_migrated == report.roles_migrated == 1
    assert report.assignments_migrated == 1
    assert report.quarantined == 2
    assert service.target.tenants["tenant-acme"].admin_user_id == "subject-admin"
    assert {row["entity_type"] for row in service.quarantine_records()} == {"user", "assignment"}


def test_live_revocation_wins_over_legacy_assignment_and_survives_retry(tmp_path):
    service, revocations = _service(tmp_path)
    revocations.revoke("subject", "subject-admin", reason="offboarded after backup")
    report = service.migrate(
        _payload(),
        tenant_ids={"legacy-t": "tenant-acme"},
        user_ids={"legacy-admin": "subject-admin"},
        role_ids={"legacy-role": "role-admin"},
    )
    assert report.assignments_migrated == 0
    assert list(service.target.assignments) == []
    assert revocations.is_revoked("subject", "subject-admin") is True


def test_first_admin_invitation_has_no_default_or_persisted_plaintext_password(tmp_path):
    service, _ = _service(tmp_path)
    service.migrate(
        _payload(),
        tenant_ids={"legacy-t": "tenant-acme"},
        user_ids={"legacy-admin": "subject-admin"},
        role_ids={"legacy-role": "role-admin"},
    )
    invitation = service.issue_admin_invitation(tenant_id="tenant-acme", email="Admin@Example.com")
    assert len(invitation.token) >= 32
    with sqlite3.connect(service.path) as connection:
        stored = connection.execute(
            "SELECT email, token_digest FROM iam_admin_bootstrap WHERE invitation_id = ?",
            (invitation.invitation_id,),
        ).fetchone()
    assert stored[0] == "admin@example.com"
    assert invitation.token not in stored[1]
    assert service.redeem_admin_invitation(invitation.token)["tenant_id"] == "tenant-acme"
    with pytest.raises(IamMigrationError, match="already consumed"):
        service.redeem_admin_invitation(invitation.token)


def test_schema_and_legacy_superuser_fail_closed(tmp_path):
    service, _ = _service(tmp_path)
    with pytest.raises(IamMigrationError, match="legacy schema"):
        service.migrate({}, tenant_ids={}, user_ids={}, role_ids={})
    payload = _payload()
    payload["users"][0]["is_superuser"] = True
    report = service.migrate(
        payload,
        tenant_ids={"legacy-t": "tenant-acme"},
        user_ids={"legacy-admin": "subject-admin"},
        role_ids={"legacy-role": "role-admin"},
    )
    assert report.users_migrated == 0
    assert report.assignments_migrated == 0
    assert report.bootstrap_required == ("tenant-acme",)
    assert service.target.tenants["tenant-acme"].admin_user_id is None


def test_mapping_collisions_and_unknown_bootstrap_tenant_fail_closed(tmp_path):
    service, _ = _service(tmp_path)
    with pytest.raises(IamMigrationError, match="unique"):
        service.migrate(
            _payload(),
            tenant_ids={"legacy-t": "tenant-acme"},
            user_ids={"legacy-admin": "same", "unknown-user": "same"},
            role_ids={"legacy-role": "role-admin"},
        )
    with pytest.raises(IamMigrationError, match="does not exist"):
        service.issue_admin_invitation(tenant_id="missing", email="admin@example.com")
