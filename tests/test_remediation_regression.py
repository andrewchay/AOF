# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W00.03 — 诊断复现场景固化为正式回归测试（2026-09-05 诊断 D01/D02/D03/D04）。

每个测试对应诊断报告中的一个已复现缺陷场景，修复后必须持续通过：
- D01: 决策 API 匿名读写、伪造身份、跨租户访问
- D02: 决策账本并发写入竞态、篡改后普通读取未失败关闭
- D03: main 缺失内部模块导致公开端点 500
- D04: K8s 探针路径指向不存在的 /health
"""

from __future__ import annotations

import json
import threading

import pytest


# ---------------------------------------------------------------------------
# D01 — 决策 API 必须拒绝匿名请求并强制服务端身份
# ---------------------------------------------------------------------------


@pytest.fixture()
def decision_client(monkeypatch):
    """配置了签名密钥的 TestClient 与对应的签名工具。"""
    monkeypatch.setenv('AOF_SEMANTIC_IDENTITY_SECRET', 'w00-regression-test-secret')
    monkeypatch.setenv('AOF_SEMANTIC_IDENTITY_KEY_ID', 'identity-key-default')

    from fastapi.testclient import TestClient

    from bridge.semantic_core.identity import SignedPrincipalVerifier
    from services.semantic_middle_layer_api.app import app

    client = TestClient(app)
    verifier = SignedPrincipalVerifier(
        key_id='identity-key-default',
        secret=b'w00-regression-test-secret',
    )
    return client, verifier


def _headers(verifier, subject: str = 'regression-user', tenant: str = 'tenant-a'):
    return verifier.sign_headers(subject=subject, tenant_id=tenant, roles=['admin'])


def test_d01_anonymous_decision_write_rejected(decision_client):
    """诊断复现：无 header 的 POST /v1/decisions 曾返回 201 并接受伪造身份。"""
    client, _ = decision_client
    resp = client.post(
        '/v1/decisions',
        json={
            'agent_id': 'publisher:impersonated',
            'decision_type': 'regression',
            'conclusion': 'should not persist',
            'rationale': 'anonymous write attempt',
            'tenant_id': 'tenant-a',
        },
    )
    assert resp.status_code == 401, f'anonymous write must be rejected, got {resp.status_code}'


def test_d01_anonymous_decision_read_rejected(decision_client):
    client, _ = decision_client
    resp = client.get('/v1/decisions/decision:whatever')
    assert resp.status_code == 401


def test_d01_anonymous_precedent_search_rejected(decision_client):
    client, _ = decision_client
    resp = client.post('/v1/decisions/precedents/search', json={'decision_type': 'x'})
    assert resp.status_code == 401


def test_d01_body_identity_has_no_authority(decision_client, tmp_path, monkeypatch):
    """请求体中的 agent_id/tenant_id 不能覆盖可信 principal。"""
    client, verifier = decision_client
    monkeypatch.setenv('AOF_DECISION_PROVENANCE_FILE', str(tmp_path / 'ledger.jsonl'))

    resp = client.post(
        '/v1/decisions',
        json={
            'agent_id': 'publisher:impersonated',
            'decision_type': 'regression',
            'conclusion': 'body identity ignored',
            'rationale': 'server-side override',
            'tenant_id': 'tenant-evil',
        },
        headers=_headers(verifier, subject='real-user', tenant='tenant-a'),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body['decision']['agent_id'] == 'real-user'
    assert body['decision']['tenant_id'] == 'tenant-a'


def test_d01_cross_tenant_read_not_leaked(decision_client, tmp_path, monkeypatch):
    """租户 B 不能读取租户 A 的决策；404 隐藏存在性。"""
    client, verifier = decision_client
    monkeypatch.setenv('AOF_DECISION_PROVENANCE_FILE', str(tmp_path / 'ledger.jsonl'))

    created = client.post(
        '/v1/decisions',
        json={
            'agent_id': 'ignored',
            'decision_type': 'regression',
            'conclusion': 'tenant a only',
            'rationale': 'isolation',
        },
        headers=_headers(verifier, subject='user-a', tenant='tenant-a'),
    )
    assert created.status_code == 201
    decision_id = created.json()['decision']['id']

    other = client.get(
        f'/v1/decisions/{decision_id}',
        headers=_headers(verifier, subject='user-b', tenant='tenant-b'),
    )
    assert other.status_code == 404, 'cross-tenant read must not leak existence'

    same = client.get(
        f'/v1/decisions/{decision_id}',
        headers=_headers(verifier, subject='user-a', tenant='tenant-a'),
    )
    assert same.status_code == 200


def test_d01_precedent_search_forced_to_caller_tenant(decision_client, tmp_path, monkeypatch):
    """先例搜索不能通过 body tenant_id 越租户。"""
    client, verifier = decision_client
    monkeypatch.setenv('AOF_DECISION_PROVENANCE_FILE', str(tmp_path / 'ledger.jsonl'))

    client.post(
        '/v1/decisions',
        json={
            'agent_id': 'ignored',
            'decision_type': 'precedent-regression',
            'conclusion': 'tenant a precedent',
            'rationale': 'r',
        },
        headers=_headers(verifier, subject='user-a', tenant='tenant-a'),
    )

    resp = client.post(
        '/v1/decisions/precedents/search',
        json={'decision_type': 'precedent-regression', 'tenant_id': 'tenant-a'},
        headers=_headers(verifier, subject='user-b', tenant='tenant-b'),
    )
    assert resp.status_code == 200
    results = resp.json()['results']
    assert all(
        item['decision']['decision'].get('tenant_id') == 'tenant-b' for item in results
    ), 'precedent search leaked another tenant'


# ---------------------------------------------------------------------------
# D02 — 账本并发安全与读时完整性失败关闭
# ---------------------------------------------------------------------------


def test_d02_concurrent_writes_keep_chain_valid(tmp_path):
    """诊断复现：两个写入者读到同一链头后追加，导致第二条校验失败。"""
    from bridge.decision_provenance import DecisionProvenanceStore

    path = tmp_path / 'ledger.jsonl'
    errors = []

    def write(i):
        try:
            store = DecisionProvenanceStore(path)
            store.record(
                agent_id=f'agent-{i}',
                decision_type='concurrency',
                conclusion=f'c-{i}',
                rationale=f'r-{i}',
                tenant_id='tenant-x',
            )
        except Exception as exc:  # pragma: no cover - 收集用于断言
            errors.append(repr(exc))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f'concurrent writes raised: {errors}'

    store = DecisionProvenanceStore(path)
    result = store.verify_integrity()
    assert result['valid'], 'chain must stay valid after concurrent writes'
    assert result['entries_checked'] == 8


def test_d02_tampered_ledger_read_fails_closed(tmp_path):
    """诊断复现：篡改 conclusion 后 get() 仍返回内容。现在必须抛出完整性错误。"""
    from bridge.decision_provenance import DecisionProvenanceStore, LedgerIntegrityError

    path = tmp_path / 'ledger.jsonl'
    store = DecisionProvenanceStore(path)
    entry = store.record(
        agent_id='agent',
        decision_type='tamper',
        conclusion='original',
        rationale='r',
        tenant_id='tenant-x',
    )
    decision_id = entry['decision']['id']

    lines = path.read_text(encoding='utf-8').splitlines()
    tampered = json.loads(lines[0])
    tampered['decision']['conclusion'] = 'TAMPERED'
    lines[0] = json.dumps(tampered, ensure_ascii=False, separators=(',', ':')) + '\n'
    path.write_text(''.join(lines), encoding='utf-8')

    with pytest.raises(LedgerIntegrityError):
        DecisionProvenanceStore(path).get(decision_id)


def test_d02_truncated_tail_read_fails_closed(tmp_path):
    """尾部截断（丢失最后一条）会破坏 previous_hash 链，读取必须失败。"""
    from bridge.decision_provenance import DecisionProvenanceStore, LedgerIntegrityError

    path = tmp_path / 'ledger.jsonl'
    store = DecisionProvenanceStore(path)
    for i in range(3):
        store.record(
            agent_id='agent',
            decision_type='truncate',
            conclusion=f'c-{i}',
            rationale='r',
            tenant_id='tenant-x',
        )

    lines = path.read_text(encoding='utf-8').splitlines()
    path.write_text('\n'.join(lines[:2]) + '\n', encoding='utf-8')

    with pytest.raises(LedgerIntegrityError):
        DecisionProvenanceStore(path).get('decision:nonexistent')


# ---------------------------------------------------------------------------
# D03 — main 必须能导入 internal 归并模块，公开端点不因缺模块 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'module_name',
    [
        'bridge.harness_trainer',
        'bridge.training_data',
        'bridge.context_exchange',
        'bridge.graph_doctor',
        'bridge.graph_retrieval',
        'bridge.hybrid_search',
    ],
)
def test_d03_restored_modules_importable(module_name):
    __import__(module_name)


def test_d03_harness_sessions_no_module_error():
    """诊断复现：GET /v1/harness/sessions 曾因 bridge.harness_trainer 缺失返回 500。"""
    from fastapi.testclient import TestClient

    from services.semantic_middle_layer_api.app import app

    client = TestClient(app)
    resp = client.get('/v1/harness/sessions')
    assert resp.status_code == 200, (
        f'harness sessions must not 500 on missing module, got {resp.status_code}'
    )


# ---------------------------------------------------------------------------
# D04 — 健康探针路径必须真实存在
# ---------------------------------------------------------------------------


def test_d04_healthz_exists_and_readyz_separated():
    """诊断复现：K8s 探针请求 /health 得到 404。liveness/readiness 路径必须可用。"""
    from fastapi.testclient import TestClient

    from services.semantic_middle_layer_api.app import app

    client = TestClient(app)
    assert client.get('/healthz').status_code == 200
    # readyz 可以因依赖未配置返回 503，但不能是 404
    assert client.get('/readyz').status_code in (200, 503)
