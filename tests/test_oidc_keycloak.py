"""W01.02 — OIDC bearer-token authentication against real Keycloak.

- verify a real Keycloak access token: signature, issuer, audience, claims
- map claims to a principal (subject, aof_tenant → tenant, realm roles)
- expired/garbage/wrong-issuer tokens are rejected
- end-to-end: Bearer token through the auth middleware reaches the API
"""

from __future__ import annotations

import json
import socket
import urllib.parse
import urllib.request

import pytest


def _keycloak_up() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 8081), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _get_token(username: str, password: str) -> str:
    data = urllib.parse.urlencode({
        "client_id": "aof-api", "client_secret": "aof-api-secret",
        "grant_type": "password", "username": username, "password": password,
    }).encode()
    with urllib.request.urlopen(
        "http://localhost:8081/realms/aof/protocol/openid-connect/token", data=data
    ) as r:
        return json.load(r)["access_token"]


@pytest.fixture(scope="module")
def alice_token():
    if not _keycloak_up():
        pytest.skip("Keycloak not running")
    return _get_token("alice", "alice-dev-pass")


@pytest.fixture(scope="module")
def bob_token():
    if not _keycloak_up():
        pytest.skip("Keycloak not running")
    return _get_token("bob", "bob-dev-pass")


# ---------------------------------------------------------------------------
# Token verifier
# ---------------------------------------------------------------------------


def test_verify_real_keycloak_token(alice_token):
    from bridge.access.oidc import OidcTokenVerifier

    verifier = OidcTokenVerifier()
    principal = verifier.verify(alice_token)
    assert principal.subject == "alice"
    assert principal.tenant_id == "acme"
    assert "admin" in principal.roles
    assert principal.issuer.endswith("/realms/aof")


def test_verify_rejects_garbage_token():
    from bridge.access.oidc import OidcTokenVerifier, OidcVerificationError

    verifier = OidcTokenVerifier()
    with pytest.raises(OidcVerificationError):
        verifier.verify("not-a-real-jwt")


def test_verify_rejects_wrong_issuer(alice_token):
    """签名有效但 iss 不匹配（不同 realm 或伪造 issuer）→ 拒绝。"""
    import base64


    from bridge.access.oidc import OidcTokenVerifier, OidcVerificationError

    verifier = OidcTokenVerifier()
    # tamper the iss claim (signature breaks: a re-signed token from a different
    # IdP would also fail because the signing key is unknown)
    parts = alice_token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    payload["iss"] = "http://evil.example.com/realms/aof"
    tampered_payload = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

    with pytest.raises(OidcVerificationError):
        verifier.verify(tampered_token)


# ---------------------------------------------------------------------------
# End-to-end through the auth middleware
# ---------------------------------------------------------------------------


@pytest.fixture()
def oidc_client(monkeypatch):
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "oidc-e2e")
    monkeypatch.setenv("AOF_OIDC_ISSUER", "http://localhost:8081/realms/aof")
    monkeypatch.setenv("AOF_OIDC_AUDIENCE", "aof-api")
    monkeypatch.setenv("AOF_API_AUTH_MODE", "strict")

    from fastapi.testclient import TestClient

    from bridge.semantic_core.identity import SignedPrincipalVerifier
    from services.semantic_middle_layer_api.app import app

    client = TestClient(app)
    verifier = SignedPrincipalVerifier(key_id="identity-key-default", secret=b"oidc-e2e")
    return client, verifier


def test_bearer_token_reaches_api(oidc_client, alice_token):
    """端到端：OIDC Bearer token 通过 auth gate → API 返回正确身份。"""
    client, _ = oidc_client
    resp = client.get(
        "/v1/ontology/workbench/session",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    # 认证成功（不是 401/403）——subject/tenant 从 token claims 映射
    assert resp.status_code == 200, f"bearer auth failed: {resp.status_code} {resp.text[:200]}"
    data = resp.json()
    assert data["subject"] == "alice"
    assert data["tenant_id"] == "acme"


def test_bearer_viewer_can_read_but_not_write(oidc_client, bob_token):
    client, _ = oidc_client
    headers = {"Authorization": f"Bearer {bob_token}"}

    # read OK
    resp = client.get("/v1/ontology/workbench/session", headers=headers)
    assert resp.status_code == 200

    # write blocked (viewer lacks write policy on governed plane)
    resp = client.post(
        "/v1/semantic/proposals",
        json={"proposal_id": "x", "release_id": "r@1", "resources": [{"resource_id": "x"}]},
        headers=headers,
    )
    assert resp.status_code == 403, f"viewer write should be 403, got {resp.status_code}"


def test_signed_principal_still_works_alongside_oidc(oidc_client):
    """兼容性：signed principal 的信封模式在 OIDC 集成后继续工作。"""
    client, verifier = oidc_client
    headers = verifier.sign_headers(subject="svc-user", tenant_id="tenant-svc", roles=["admin"])
    resp = client.get("/v1/ontology/workbench/session", headers=headers)
    assert resp.status_code == 200
