# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W09.03 — Tenant rate limiting: burst capacity, refill, isolation,
bounded key space, 429 + Retry-After, backpressure acquire."""

from __future__ import annotations

import threading

import pytest

from bridge.access.rate_limit import RateLimitConfig, TenantRateLimiter


def _limiter(capacity: int = 5, refill: float = 10.0) -> TenantRateLimiter:
    return TenantRateLimiter(RateLimitConfig(capacity=capacity, refill_per_sec=refill))


def test_burst_capacity_then_exhausted():
    limiter = _limiter(capacity=5, refill=0.001)
    results = [limiter.check("tenant-a") for _ in range(5)]
    assert all(results)
    assert limiter.check("tenant-a") is False


def test_refill_restores_tokens_over_time():
    limiter = _limiter(capacity=2, refill=100.0)
    assert limiter.check("t") and limiter.check("t")
    assert limiter.check("t") is False
    import time
    time.sleep(0.05)  # 100/s * 0.05s = 5 tokens
    assert limiter.check("t") is True


def test_tenants_are_isolated():
    limiter = _limiter(capacity=1, refill=0.001)
    assert limiter.check("tenant-a") is True
    assert limiter.check("tenant-a") is False
    # tenant-b unaffected by a's exhaustion
    assert limiter.check("tenant-b") is True


def test_retry_after_positive_on_exhaustion():
    limiter = _limiter(capacity=1, refill=10.0)
    limiter.check("t")
    retry = limiter.retry_after("t")
    assert retry > 0
    assert retry <= 1 / 10.0 + 0.1


def test_key_space_bounded():
    """W09.02 教训：10 万假租户不得撑爆内存键空间。"""
    limiter = _limiter(capacity=1, refill=0.001)
    limiter.config.max_tenants = 100
    for i in range(100_000):
        limiter.check(f"tenant-{i}")
    assert limiter.bucket_count() <= 100


def test_acquire_backpressure_with_timeout():
    limiter = _limiter(capacity=1, refill=50.0)
    assert limiter.check("t") is True
    # waits until a token refills (50/s -> ~0.02s)
    assert limiter.acquire("t", timeout=2.0) is True
    # exhausted again: short timeout fails
    assert limiter.acquire("t", timeout=0.01) is False


def test_thread_safety_under_concurrency():
    limiter = _limiter(capacity=100, refill=0.001)
    granted = []
    lock = threading.Lock()

    def worker():
        for _ in range(50):
            if limiter.check("shared"):
                with lock:
                    granted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # exactly the capacity: no over-grant race
    assert len(granted) == 100


# ---------------------------------------------------------------------------
# Middleware integration (429 + Retry-After)
# ---------------------------------------------------------------------------


@pytest.fixture()
def limited_client(monkeypatch):
    monkeypatch.setenv('AOF_SEMANTIC_IDENTITY_SECRET', 'rl-e2e')
    monkeypatch.setenv('AOF_RATE_LIMIT_CAPACITY', '3')
    monkeypatch.setenv('AOF_RATE_LIMIT_REFILL_PER_SEC', '0.001')

    from fastapi.testclient import TestClient

    from bridge.semantic_core.identity import SignedPrincipalVerifier
    from services.semantic_middle_layer_api.app import app

    # 重置单例以读取新配置
    import services.semantic_middle_layer_api.app as api_module
    monkeypatch.setattr(api_module, '_RATE_LIMITER', None)

    client = TestClient(app)
    verifier = SignedPrincipalVerifier(key_id='identity-key-default', secret=b'rl-e2e')
    headers = verifier.sign_headers(subject='rl-user', tenant_id='tenant-a', roles=['admin'])
    return client, headers


def test_middleware_429_with_retry_after(limited_client):
    client, headers = limited_client
    # probes are exempt from rate limiting (W08.02) - use a normal API route
    codes = [
        client.get('/v1/ontology/workbench/session', headers=headers).status_code
        for _ in range(4)
    ]
    assert 429 in codes, f"expected at least one 429 after burst, got {codes}"
    first_429 = next(
        r for r in (
            client.get('/v1/ontology/workbench/session', headers=headers)
            for _ in range(2)
        ) if r.status_code == 429
    )
    assert 'Retry-After' in first_429.headers
    assert first_429.json()['code'] == 'rate_limited'


def test_other_tenant_unaffected(limited_client):
    from bridge.semantic_core.identity import SignedPrincipalVerifier

    client, headers = limited_client
    verifier = SignedPrincipalVerifier(key_id='identity-key-default', secret=b'rl-e2e')
    other = verifier.sign_headers(subject='other', tenant_id='tenant-b', roles=['admin'])

    # 打满 tenant-a（普通 API 路由）
    for _ in range(4):
        client.get('/v1/ontology/workbench/session', headers=headers)
    # tenant-b 不受影响
    assert client.get('/v1/ontology/workbench/session', headers=other).status_code == 200
