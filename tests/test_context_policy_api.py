# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W03.04 — Context management: versioned policy API + withdrawal protocol.

- PUT saves an append-only policy revision (admin-only, same-tenant only)
- GET pulls the current revision; a client pinning an older revision gets
  the new one plus is_current=false (cache revalidation contract)
- unknown tenant policies are 404 (no cross-tenant existence leak)
- withdrawal marks a publication withdrawn while preserving the audit
  record; the withdrawn packet no longer counts as usable
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def policy_env(tmp_path, monkeypatch):
    monkeypatch.setenv('AOF_SEMANTIC_IDENTITY_SECRET', 'ctx-policy-e2e')
    monkeypatch.setenv('AOF_DECISION_LEDGER_BACKEND', 'jsonl')
    monkeypatch.setenv('AOF_TEST_ROOT', str(tmp_path))
    monkeypatch.setattr('services.semantic_middle_layer_api.app.AOF_ROOT', tmp_path)

    from fastapi.testclient import TestClient

    from bridge.semantic_core.identity import SignedPrincipalVerifier
    from services.semantic_middle_layer_api.app import app

    client = TestClient(app)
    verifier = SignedPrincipalVerifier(key_id='identity-key-default', secret=b'ctx-policy-e2e')

    def headers(role, tenant='acme'):
        return verifier.sign_headers(
            subject=f'{role}-user', tenant_id=tenant, roles=[role]
        )

    return client, headers


def _valid_policy(tenant_id: str = 'acme') -> dict:
    return {
        'tenant_id': tenant_id,
        'spaces': {
            'fraud-draft': {
                'draft_space_id': 'space:acme:fraud-draft',
                'allowed_purposes': ['fraud-review'],
            },
        },
        'source_routes': [
            {
                'source_prefix': 'db:acme-finance:',
                'disposition': 'share-eligible',
                'draft_space': 'fraud-draft',
                'allowed_purposes': ['fraud-review'],
                'sensitivity_labels': ['finance'],
            },
        ],
    }


def test_policy_save_requires_admin(policy_env):
    client, headers = policy_env
    resp = client.put(
        '/v1/context/tenant-policy',
        json={'tenant_id': 'acme', 'policy': _valid_policy()},
        headers=headers('editor'),
    )
    assert resp.status_code == 403


def test_policy_save_and_revision_bump(policy_env):
    client, headers = policy_env
    admin = headers('admin')

    first = client.put(
        '/v1/context/tenant-policy',
        json={'tenant_id': 'acme', 'policy': _valid_policy()},
        headers=admin,
    )
    assert first.status_code == 200
    assert first.json()['revision'] == 1

    # v2: add another route
    policy_v2 = _valid_policy()
    policy_v2['source_routes'].append({
        'source_prefix': 'db:acme-hr:',
        'disposition': 'share-eligible',
        'draft_space': 'fraud-draft',
        'allowed_purposes': ['fraud-review'],
        'sensitivity_labels': [],
    })
    second = client.put(
        '/v1/context/tenant-policy',
        json={'tenant_id': 'acme', 'policy': policy_v2},
        headers=admin,
    )
    assert second.status_code == 200
    assert second.json()['revision'] == 2


def test_policy_pull_with_cache_revalidation(policy_env):
    client, headers = policy_env
    admin = headers('admin')
    viewer = headers('viewer')

    client.put('/v1/context/tenant-policy', json={'tenant_id': 'acme', 'policy': _valid_policy()}, headers=admin)

    # client pinned to revision 1 (current) -> keeps cache
    fresh = client.get('/v1/context/tenant-policy/acme?revision=1', headers=viewer)
    assert fresh.status_code == 200
    assert fresh.json()['is_current'] is True
    assert fresh.json()['revision'] == 1

    # admin saves v2 -> pinned client detects staleness
    policy_v2 = _valid_policy()
    policy_v2['source_routes'].append({
        'source_prefix': 'db:acme-hr:',
        'disposition': 'private',
        'draft_space': None,
        'allowed_purposes': [],
        'sensitivity_labels': [],
    })
    client.put('/v1/context/tenant-policy', json={'tenant_id': 'acme', 'policy': policy_v2}, headers=admin)

    stale = client.get('/v1/context/tenant-policy/acme?revision=1', headers=viewer)
    assert stale.status_code == 200
    assert stale.json()['is_current'] is False
    assert stale.json()['revision'] == 1

    current = client.get('/v1/context/tenant-policy/acme', headers=viewer)
    assert current.status_code == 200
    assert current.json()['is_current'] is True
    assert current.json()['revision'] == 2
    assert len(current.json()['policy']['source_routes']) == 2


def test_policy_pull_hides_cross_tenant(policy_env):
    client, headers = policy_env
    admin = headers('admin')
    client.put('/v1/context/tenant-policy', json={'tenant_id': 'acme', 'policy': _valid_policy()}, headers=admin)

    outsider = headers('viewer', 'other-tenant')
    resp = client.get('/v1/context/tenant-policy/acme', headers=outsider)
    assert resp.status_code == 404

    # admin cannot set policy for another tenant either
    resp = client.put(
        '/v1/context/tenant-policy',
        json={'tenant_id': 'other-tenant', 'policy': _valid_policy('other-tenant')},
        headers=admin,
    )
    assert resp.status_code == 403


def test_policy_invalid_contract_rejected(policy_env):
    client, headers = policy_env
    admin = headers('admin')

    bad = _valid_policy()
    bad['source_routes'][0]['disposition'] = 'share-eligible'
    bad['source_routes'][0]['draft_space'] = 'nonexistent-space'  # unknown draft space
    resp = client.put('/v1/context/tenant-policy', json={'tenant_id': 'acme', 'policy': bad}, headers=admin)
    assert resp.status_code == 422


def test_withdrawal_preserves_audit_and_stops_use(policy_env):
    """撤回协议端到端：发布 → 撤回 → 审计保留但状态为 withdrawn。"""
    client, headers = policy_env
    admin = headers('admin')

    # 直接在 repository 层造一个已发布 packet（走完整 promotion 在 W03.03 已验证）
    from bridge.context_exchange.gateway import ContextPublication, SqliteContextPacketRepository

    # app 的撤回端点用 AOF_ROOT/data/context/packets.sqlite3（AOF_ROOT 被
    # monkeypatch 到 tmp_path），测试直写同一文件预置一条发布记录
    import pathlib
    repository = SqliteContextPacketRepository(
        pathlib.Path(os.environ['AOF_TEST_ROOT']) / 'data' / 'context' / 'packets.sqlite3'
    )

    repository.put_publication(
        ContextPublication(
            packet_id='packet:w-1', source_space='space:s1', target_space='space:s2',
            visibility='tenant-governed', release_id='ctx@1.0.0',
            release_digest='sha256:abc', publish_decision_id='decision:x',
        ),
        tenant_id='acme',
    )

    resp = client.post(
        '/v1/context/packets/packet:w-1/withdraw',
        json={'visibility': 'tenant-governed', 'reason': 'found consent violation'},
        headers=admin,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'withdrawn'
    assert body['audit_preserved'] is True
    assert body['release_id'] == 'ctx@1.0.0'  # audit record preserved

    # withdrawal is idempotent
    again = client.post(
        '/v1/context/packets/packet:w-1/withdraw',
        json={'visibility': 'tenant-governed', 'reason': 'again'},
        headers=admin,
    )
    assert again.status_code == 200

    # unknown packet -> 404
    missing = client.post(
        '/v1/context/packets/packet:missing/withdraw',
        json={'visibility': 'tenant-governed', 'reason': 'x'},
        headers=admin,
    )
    assert missing.status_code == 404
