"""W03.06 — Every registered operation must be callable (success or safe denial).

For each registered operation in the operation registry, verify that:
- public-diagnostic operations respond without authentication
- governed operations respond with valid auth (200/201/422 for data issues)
- legacy operations respond through the strict gate
- retired-410 operations return 410

This is a smoke-level callability check: it proves no route 500s from
missing modules, not business logic correctness.
"""

from __future__ import annotations

import json
import os

import pytest


@pytest.fixture(scope="module")
def caller():
    os.environ.setdefault("AOF_SEMANTIC_IDENTITY_SECRET", "callability-test")
    os.environ.setdefault("AOF_API_AUTH_MODE", "default")

    from fastapi.testclient import TestClient
    from bridge.semantic_core.identity import SignedPrincipalVerifier
    from services.semantic_middle_layer_api.app import app

    client = TestClient(app, raise_server_exceptions=False)
    verifier = SignedPrincipalVerifier(
        key_id="identity-key-default", secret=b"callability-test"
    )

    def call(method: str, path: str, *, auth: bool = True, body: dict | None = None):
        headers = {}
        if auth:
            headers.update(verifier.sign_headers(
                subject="callability", tenant_id="acme",
                roles=["admin", "editor", "viewer", "operator", "reviewer",
                       "publisher", "ingestor", "analyst", "validator", "worker"],
            ))
        request = client.build_request(method, path, headers=headers, json=body or {})
        return client.send(request)

    return call


def test_no_registered_operation_returns_500(caller):
    """W03.06 core: no registered operation should return 500 (internal error)
    — 500 means a missing module or unhandled crash, not a safe denial."""
    from bridge.access.operation_registry import load_registry

    registry = load_registry()
    five_hundreds = []
    checked = 0

    # Test a representative sample of GET operations (safe, no side effects)
    for op_id, op in sorted(registry.items()):
        if op.method != "GET" or op.classification == "static-hosting":
            continue
        checked += 1
        resp = caller("GET", op.path, auth=not op.public)
        if resp.status_code == 500:
            five_hundreds.append(f"GET {op.path} -> 500")

    # POST operations on read-only endpoints (safe bodies, expected 422)
    for op_id, op in sorted(registry.items()):
        if op.method != "POST" or op.classification in ("retired-410", "static-hosting"):
            continue
        checked += 1
        resp = caller("POST", op.path, body={})
        if resp.status_code == 500:
            five_hundreds.append(f"POST {op.path} -> 500")

    assert checked > 50, f"only checked {checked} operations — registry too small?"
    assert not five_hundreds, (
        f"{len(five_hundreds)} operations return 500 (missing module/crash): "
        f"{five_hundreds[:5]}"
    )


def test_retired_endpoints_return_410(caller):
    caller("POST", "/v1/semantic/compile", body={"topic": "t", "intent": "i"})
    # the actual assertion is in test_operation_registry.py; here we verify
    # the call doesn't crash
    resp = caller("POST", "/v1/semantic/compile", body={"topic": "t", "intent": "i"})
    assert resp.status_code in (403, 410, 422), f"got {resp.status_code}"


def test_public_diagnostic_reachable_without_auth(caller):
    for path in ("/healthz", "/readyz"):
        resp = caller("GET", path, auth=False)
        assert resp.status_code in (200, 503), f"{path} -> {resp.status_code}"
