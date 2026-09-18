# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Persistent subject and tenant revocations are checked on every REST request."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request(headers: dict[str, str]) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw_headers})


@pytest.mark.parametrize("kind,identity", [("subject", "alice"), ("tenant", "acme")])
def test_signed_rest_principal_is_rechecked_after_revocation(
    tmp_path, monkeypatch, kind, identity
):
    from bridge.access.revocations import RevocationRegistry
    from bridge.semantic_core.identity import SignedPrincipalVerifier
    from services.semantic_middle_layer_api.app import _decision_principal

    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "rest-revocation-secret")
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_KEY_ID", "rest-revocation-key")
    monkeypatch.setenv("AOF_REVOCATIONS_FILE", str(tmp_path / "revocations.sqlite"))
    headers = SignedPrincipalVerifier(
        key_id="rest-revocation-key", secret=b"rest-revocation-secret"
    ).sign_headers(subject="alice", tenant_id="acme", roles=["viewer"])
    request = _request(headers)

    assert _decision_principal(request).subject == "alice"
    RevocationRegistry().revoke(kind, identity, reason="security test")
    with pytest.raises(HTTPException) as exc:
        _decision_principal(request)
    assert exc.value.status_code == 401
    assert "revoked" in str(exc.value.detail)
