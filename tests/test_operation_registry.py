"""W01.01 — Operation registry default-deny gate tests."""

from __future__ import annotations

import pytest


@pytest.fixture(scope='module')
def fastapi_app():
    from services.semantic_middle_layer_api.app import app

    return app


def test_registry_loads_with_all_operations():
    from bridge.access.operation_registry import load_registry

    registry = load_registry()
    assert len(registry) >= 150, f'expected full registry, got {len(registry)}'


def test_every_rest_route_is_registered(fastapi_app):
    """新增未声明路由不能通过测试（默认拒绝的第一道门）。"""
    from bridge.access.operation_registry import validate_fastapi_app

    validate_fastapi_app(fastapi_app)


def test_registry_classifications_are_valid():
    from bridge.access.operation_registry import load_registry, VALID_CLASSIFICATIONS

    registry = load_registry()
    for op in registry.values():
        assert op.classification in VALID_CLASSIFICATIONS


def test_health_probes_are_public_diagnostic():
    from bridge.access.operation_registry import load_registry

    registry = load_registry()
    for probe in ('get:/healthz', 'get:/readyz'):
        op = registry.get(probe)
        assert op is not None, f'{probe} must be registered'
        assert op.classification == 'public-diagnostic'
        assert op.public is True


def test_retired_semantic_compile_is_marked(fastapi_app):
    from fastapi.testclient import TestClient

    from bridge.access.operation_registry import load_registry, validate_retired_endpoints

    validate_retired_endpoints(fastapi_app)
    registry = load_registry()
    assert registry['post:/v1/semantic/compile'].classification == 'retired-410'

    client = TestClient(fastapi_app)
    resp = client.post(
        '/v1/semantic/compile',
        json={'topic': 't', 'intent': 'i'},  # valid body: pydantic must not mask the 410
    )
    assert resp.status_code == 410


def test_governed_plane_marked_and_protected():
    from bridge.access.operation_registry import load_registry

    registry = load_registry()
    governed_sample = [
        op for op in registry.values()
        if op.classification == 'governed' and op.method == 'POST'
    ]
    assert governed_sample, 'governed operations must be present'
    for op in governed_sample:
        assert op.auth == 'signed-principal', f'{op.operation_id} must require principal'


def test_unregistered_route_is_rejected():
    """负例：注册表中不存在的路由必须导致校验失败。"""
    from fastapi import FastAPI

    from bridge.access.operation_registry import OperationRegistryError, validate_fastapi_app

    app = FastAPI()

    @app.get('/v1/totally-new-undeclared-endpoint')
    def new_endpoint():
        return {}

    with pytest.raises(OperationRegistryError) as exc:
        validate_fastapi_app(app)
    assert '/v1/totally-new-undeclared-endpoint' in str(exc.value)
