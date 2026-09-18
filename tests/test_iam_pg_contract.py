"""W08.04 — IAM store: same contract on SQLite AND PostgreSQL."""

from __future__ import annotations

import socket

import pytest


def _pg_up() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 5433), timeout=1)
        s.close()
        return True
    except OSError:
        return False


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "sqlite":
        from bridge.persistence.sqlite_iam_store import SqliteIamStore
        yield SqliteIamStore(tmp_path / "iam.db")
    elif _pg_up():
        from bridge.persistence.postgres_iam_store import PostgresIamStore
        store = PostgresIamStore()
        with store._connect() as conn:
            conn.execute("TRUNCATE iam_users, iam_roles, iam_assignments, iam_tenants")
            conn.commit()
        yield store
    else:
        pytest.skip("PostgreSQL infra not running")


def _user(uid: str, username: str, tenant: str = "acme"):
    from bridge.auth.models import User
    return User(id=uid, username=username, tenant_id=tenant)


def _role(rid: str, name: str, tenant: str = "acme"):
    from bridge.auth.models import Role, RoleType
    return Role(id=rid, name=name, role_type=RoleType.EDITOR, tenant_id=tenant)


def _tenant(tid: str, slug: str):
    from bridge.tenant.manager import Tenant, TenantConfig, TenantStatus
    return Tenant(
        id=tid, name=f"Tenant {tid}", slug=slug,
        status=TenantStatus.ACTIVE, config=TenantConfig(),
    )


def test_user_roundtrip(store):
    store.users["u1"] = _user("u1", "alice")
    assert store.users["u1"].username == "alice"
    assert store.users.get("u1").tenant_id == "acme"
    assert "u1" in store.users


def test_username_unique(store):
    from bridge.persistence.sqlite_iam_store import IamStoreError
    store.users["u1"] = _user("u1", "alice")
    with pytest.raises(IamStoreError):
        store.users["u2"] = _user("u2", "alice")  # same username, different key


def test_role_roundtrip(store):
    store.roles["r1"] = _role("r1", "editor")
    assert store.roles["r1"].name == "editor"


def test_assignment_append_and_iterate(store):
    from bridge.auth.models import ResourceType, UserRoleAssignment
    store.assignments.append(UserRoleAssignment(
        user_id="u1", role_id="r1", resource_type=ResourceType.DATASET,
    ))
    store.assignments.append(UserRoleAssignment(
        user_id="u2", role_id="r1", resource_type=ResourceType.DATASET,
    ))
    assert len(store.assignments) == 2
    assert [a.user_id for a in store.assignments] == ["u1", "u2"]


def test_assignment_sequence_supports_mutable_sequence_contract(store):
    from bridge.auth.models import ResourceType, UserRoleAssignment

    def assignment(user_id: str):
        return UserRoleAssignment(
            user_id=user_id,
            role_id="r1",
            resource_type=ResourceType.DATASET,
        )

    store.assignments.append(assignment("u1"))
    store.assignments.append(assignment("u2"))
    store.assignments.insert(1, assignment("middle"))
    assert [value.user_id for value in store.assignments] == ["u1", "middle", "u2"]

    store.assignments[-1] = assignment("last")
    assert store.assignments[-1].user_id == "last"

    store.assignments[1:2] = [assignment("slice-a"), assignment("slice-b")]
    assert [value.user_id for value in store.assignments] == [
        "u1",
        "slice-a",
        "slice-b",
        "last",
    ]

    del store.assignments[1]
    assert [value.user_id for value in store.assignments] == ["u1", "slice-b", "last"]


def test_tenant_slug_unique(store):
    from bridge.persistence.sqlite_iam_store import IamStoreError
    store.tenants["t1"] = _tenant("t1", "acme-corp")
    with pytest.raises(IamStoreError):
        store.tenants["t2"] = _tenant("t2", "acme-corp")


def test_delete_and_len(store):
    store.users["u1"] = _user("u1", "alice")
    store.users["u2"] = _user("u2", "bob")
    assert len(store.users) == 2
    del store.users["u1"]
    assert len(store.users) == 1
    assert store.users.get("u1") is None


def test_governed_legacy_migration_contract(store, tmp_path):
    from bridge.access.iam_migration import IamMigrationService
    from bridge.access.revocations import RevocationRegistry

    service = IamMigrationService(
        target_store=store,
        quarantine_path=tmp_path / "migration.sqlite",
        revocations=RevocationRegistry(tmp_path / "revocations.sqlite"),
    )
    report = service.migrate(
        {
            "schema_version": "aof.legacy-iam/v1",
            "tenants": [{"id": "old-t", "name": "Acme", "slug": "acme-migration", "status": "active"}],
            "users": [{"id": "old-u", "username": "migrated-user", "tenant_id": "old-t"}],
            "roles": [{"id": "old-r", "name": "Viewer", "role_type": "viewer", "tenant_id": "old-t"}],
            "assignments": [{"user_id": "old-u", "role_id": "old-r", "resource_type": "dataset"}],
        },
        tenant_ids={"old-t": "new-t"},
        user_ids={"old-u": "new-u"},
        role_ids={"old-r": "new-r"},
    )
    assert report.assignments_migrated == 1
    assert store.users["new-u"].tenant_id == "new-t"
    assert store.roles["new-r"].tenant_id == "new-t"
    assert store.assignments[0].user_id == "new-u"
