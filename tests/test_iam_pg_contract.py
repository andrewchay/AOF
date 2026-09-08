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
