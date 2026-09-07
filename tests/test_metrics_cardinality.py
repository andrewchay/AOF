"""W09.02 — Metric label cardinality must be bounded (diagnosis D11)."""

from __future__ import annotations

import random

import pytest


@pytest.fixture()
def app_client(monkeypatch):
    from fastapi.testclient import TestClient

    from services.semantic_middle_layer_api.app import app

    return app, TestClient(app)


def test_dynamic_paths_collapse_to_route_templates(app_client):
    app, client = app_client
    from services.semantic_middle_layer_api.app import (
        _metric_route_label,
    )

    label = _metric_route_label('/v1/decisions/decision:abc-123')
    assert label == '/v1/decisions/{decision_id}', (
        f'dynamic decision id must collapse to template, got {label!r}'
    )


def test_unknown_paths_share_single_bucket(app_client):
    from services.semantic_middle_layer_api.app import _METRIC_UNMATCHED_LABEL, _metric_route_label

    random.seed(42)
    for _ in range(200):
        probe = f'/v1/no-such-path/{random.randint(0, 10**9)}'
        assert _metric_route_label(probe) == _METRIC_UNMATCHED_LABEL


def test_stats_keys_bounded_under_random_traffic(app_client):
    """诊断复现：按原始路径累计时，随机 ID 会无限增加时序键。"""
    app, client = app_client
    from services.semantic_middle_layer_api.app import OBS_PATH_STATS

    before = len(OBS_PATH_STATS)
    random.seed(7)
    for i in range(300):
        client.get(f'/v1/definitely-not-a-route/{random.randint(0, 10**9)}-{i}')
        client.get(f'/v1/decisions/decision:rand-{random.randint(0, 10**9)}')

    unknown_keys = [k for k in OBS_PATH_STATS if k.startswith('/v1/definitely-not-a-route')]
    decision_keys = [k for k in OBS_PATH_STATS if k.startswith('/v1/decisions/decision:')]
    assert not unknown_keys, 'unknown paths must not create per-path keys'
    assert not decision_keys, 'dynamic decision ids must not create per-path keys'
    assert len(OBS_PATH_STATS) - before <= 5, (
        'only bounded template buckets may be added'
    )


def test_cache_is_bounded(app_client):
    from services.semantic_middle_layer_api.app import _ROUTE_TEMPLATE_CACHE

    random.seed(1)
    for i in range(500):
        from services.semantic_middle_layer_api.app import _metric_route_label

        _metric_route_label(f'/unbounded-probe/{i}')
    assert len(_ROUTE_TEMPLATE_CACHE) <= 10_000
