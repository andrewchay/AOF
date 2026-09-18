# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from services.semantic_middle_layer_api.auth_middleware import (
    AuthError,
    AuthenticationMiddleware,
    InvalidAPIKeyError,
    LoginRequest,
    RequestSigner,
    TokenManager,
    login,
    refresh_token,
)


def test_legacy_jwt_has_no_built_in_signing_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(AuthError, match="disabled"):
        TokenManager.create_access_token("user", "alice")


def test_api_key_shape_never_authenticates_without_credential_store():
    middleware = AuthenticationMiddleware(lambda scope, receive, send: None)
    with pytest.raises(InvalidAPIKeyError, match="credential store"):
        asyncio.run(middleware._verify_api_key("aof_acme_unverified"))


def test_local_password_and_refresh_routes_are_explicitly_retired():
    with pytest.raises(HTTPException) as login_error:
        asyncio.run(login(LoginRequest(username="alice", password="anything")))
    assert login_error.value.status_code == 410
    assert "OIDC" in str(login_error.value.detail)

    with pytest.raises(HTTPException) as refresh_error:
        asyncio.run(refresh_token("anything"))
    assert refresh_error.value.status_code == 410


def test_request_signature_rejects_old_and_future_timestamps():
    secret = "test-secret"
    now = datetime.now(timezone.utc)
    for value in (now - timedelta(minutes=10), now + timedelta(minutes=10)):
        timestamp = str(int(value.timestamp()))
        signature = RequestSigner.generate_signature("POST", "/v1/test", timestamp, "{}", secret)
        assert not RequestSigner.verify_signature(
            "POST", "/v1/test", timestamp, "{}", signature, secret, max_age_seconds=60
        )
