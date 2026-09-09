# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_browser_oidc_uses_pkce_without_password_or_client_secret():
    source = (ROOT / "web/src/api/oidc.ts").read_text()
    assert "authorization_code" in source
    assert "code_challenge_method: 'S256'" in source
    assert "code_verifier" in source
    assert "client_secret" not in source
    assert "grant_type: 'password'" not in source
    assert "sessionStorage.setItem(OIDC_TOKEN_KEY" in source


def test_keycloak_provisions_a_public_pkce_browser_client():
    source = (ROOT / "deploy/keycloak-init.sh").read_text()
    assert "clientId=aof-web" in source
    assert "publicClient=true" in source
    assert "standardFlowEnabled=true" in source
    assert "pkce.code.challenge.method" in source


def test_main_layout_exposes_the_oidc_redirect_and_callback_flow():
    source = (ROOT / "web/src/layouts/MainLayout.vue").read_text()
    assert "beginLogin" in source
    assert "completeLogin" in source
    assert "企业账号登录" in source
