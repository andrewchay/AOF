"""W04.01 — IAM/Tenant durable SQLite persistence contract tests.

Acceptance: create → read back from a *new manager instance* on the same
store (cross-process persistence), slug/username uniqueness enforced at
the database level, lifecycle state changes persist.
"""

from __future__ import annotations

import pytest

from bridge.auth.models import Action, Permission, ResourceType, RoleType
from bridge.auth.rbac import RBACManager
from bridge.persistence.sqlite_iam_store import IamStoreError, SqliteIamStore
from bridge.tenant.manager import TenantManager, TenantStatus


def _m(tmp_path):
    return RBACManager(store_path=tmp_path / 'iam.db')


@pytest.mark.asyncio
async def test_user_persists_across_instances(tmp_path):
    m1 = _m(tmp_path)
    user = await m1.create_user('alice', email='a@corp.io', tenant_id='tenant-a')

    m2 = _m(tmp_path)  # fresh instance = fresh process simulation
    fetched = await m2.get_user(user.id, use_cache=False)
    assert fetched is not None
    assert fetched.username == 'alice'
    assert fetched.tenant_id == 'tenant-a'


@pytest.mark.asyncio
async def test_role_assignment_persists_across_instances(tmp_path):
    m1 = _m(tmp_path)
    user = await m1.create_user('bob')
    role = await m1.create_role('editor', RoleType.EDITOR, tenant_id='tenant-a')
    await m1.grant_role(user.id, role.id, ResourceType.DATASET, resource_id='ds-1')

    m2 = _m(tmp_path)
    assignments = await m2.list_user_roles(user.id)
    assert len(assignments) == 1
    assert assignments[0].role_id == role.id
    assert assignments[0].resource_type == ResourceType.DATASET


@pytest.mark.asyncio
async def test_role_persists_with_permissions(tmp_path):
    m1 = _m(tmp_path)
    perm = Permission(resource_type=ResourceType.DATASET, action=Action.READ)
    role = await m1.create_role('viewer', RoleType.VIEWER, permissions=[perm])

    m2 = _m(tmp_path)
    fetched = await m2.get_role(role.id)
    assert fetched is not None
    assert fetched.name == 'viewer'
    assert len(fetched.permissions) == 1
    assert fetched.permissions[0].resource_type == ResourceType.DATASET
    assert fetched.permissions[0].action == Action.READ


@pytest.mark.asyncio
async def test_tenant_lifecycle_persists_across_instances(tmp_path):
    m1 = TenantManager(store_path=tmp_path / 'iam.db')
    tenant = await m1.create_tenant('Acme Corp', slug='acme-corp')
    await m1.suspend_tenant(tenant.id, reason='contract paused')

    m2 = TenantManager(store_path=tmp_path / 'iam.db')
    fetched = await m2.get_tenant(tenant.id)
    assert fetched is not None
    assert fetched.slug == 'acme-corp'
    assert fetched.status == TenantStatus.SUSPENDED


@pytest.mark.asyncio
async def test_tenant_slug_unique_across_instances(tmp_path):
    m1 = TenantManager(store_path=tmp_path / 'iam.db')
    await m1.create_tenant('Acme', slug='acme-unique')

    m2 = TenantManager(store_path=tmp_path / 'iam.db')
    with pytest.raises(Exception) as exc:
        await m2.create_tenant('Acme Again', slug='acme-unique')
    assert 'acme-unique' in str(exc.value) or 'exist' in str(exc.value).lower()


@pytest.mark.asyncio
async def test_username_unique_enforced_at_store_level(tmp_path):
    store = SqliteIamStore(tmp_path / 'iam.db')
    m1 = RBACManager(store_path=tmp_path / 'iam.db')
    user = await m1.create_user('dup-name')

    from bridge.auth.models import User

    with pytest.raises(IamStoreError):
        # different key + same username -> UNIQUE(username) must reject
        store.users['other-id'] = User(id='other-id', username='dup-name')


@pytest.mark.asyncio
async def test_assignment_delete_works_on_facade(tmp_path):
    """回归护栏：_delete_assignment 的原地切片在门面上语义不变。"""
    m1 = _m(tmp_path)
    user = await m1.create_user('carol')
    role = await m1.create_role('ops', RoleType.EDITOR, tenant_id='tenant-a')
    await m1.grant_role(user.id, role.id, ResourceType.DATASET)

    m2 = _m(tmp_path)
    removed = await m2.revoke_role(user.id, role.id, ResourceType.DATASET)
    assert removed is True
    assert await m2.list_user_roles(user.id) == []
