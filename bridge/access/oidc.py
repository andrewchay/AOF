"""W01.02 — OIDC bearer-token verification against a Keycloak IdP.

Authenticates requests carrying ``Authorization: Bearer <JWT>`` by:

1. verifying the RS256 signature against the IdP's published JWKS keys
   (cached, refreshed on unknown kid)
2. checking iss, exp, and (optionally) aud claims
3. mapping claims to a principal: subject = preferred_username,
   tenant = aof_tenant claim (falls back to the realm name), roles =
   realm_access.roles

This is the production identity path replacing the dev "paste envelope"
compatibility mode. The signed-principal path remains for service-to-service
calls that carry the shared secret — both are accepted by the auth gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient


class OidcVerificationError(ValueError):
    """Raised when the OIDC token is invalid, expired, or from the wrong issuer."""


@dataclass(frozen=True)
class OidcPrincipal:
    subject: str
    tenant_id: str
    roles: tuple[str, ...]
    issuer: str
    key_id: str

    @property
    def role_set(self) -> frozenset[str]:
        return frozenset(self.roles)


class OidcTokenVerifier:
    """Verifies Keycloak access tokens and maps claims to a principal."""

    def __init__(
        self,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        jwks_cache_ttl: float = 300.0,
    ) -> None:
        self.issuer = (
            issuer
            or os.environ.get("AOF_OIDC_ISSUER", "http://localhost:8081/realms/aof")
        ).rstrip("/")
        self.audience = audience or os.environ.get("AOF_OIDC_AUDIENCE", "aof-api")
        self.jwks_url = f"{self.issuer}/protocol/openid-connect/certs"
        self._jwks_client = PyJWKClient(self.jwks_url, cache_keys=True, lifespan=jwks_cache_ttl)
        self._jwks_cache_ttl = jwks_cache_ttl

    def verify(self, token: str) -> OidcPrincipal:
        """Verify a JWT and map claims. Raises OidcVerificationError."""
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        except Exception as exc:
            raise OidcVerificationError(f"cannot find signing key: {exc}") from exc

        try:
            # Keycloak access tokens carry aud="account" and azp=<clientId>;
            # the audience check is relaxed to match either the configured
            # audience or the azp claim (the client that requested the token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                issuer=self.issuer,
                options={
                    "require": ["exp", "iss", "sub"],
                    "verify_aud": False,  # azp checked below
                },
            )
            azp = claims.get("azp", "")
            aud = claims.get("aud", [])
            aud_list = [aud] if isinstance(aud, str) else (aud or [])
            if self.audience not in aud_list and azp != self.audience:
                raise OidcVerificationError(
                    f"token azp={azp!r} aud={aud_list} does not match audience {self.audience!r}"
                )
        except jwt.ExpiredSignatureError as exc:
            raise OidcVerificationError("token has expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise OidcVerificationError(f"wrong audience: {exc}") from exc
        except jwt.InvalidIssuerError as exc:
            raise OidcVerificationError(f"wrong issuer: {exc}") from exc
        except jwt.PyJWTError as exc:
            raise OidcVerificationError(f"token verification failed: {exc}") from exc

        subject = claims.get("preferred_username") or claims.get("sub", "")
        tenant = claims.get("aof_tenant") or self.issuer.rsplit("/realms/", 1)[-1]
        roles = tuple(
            claims.get("realm_access", {}).get("roles", [])
        )
        return OidcPrincipal(
            subject=str(subject),
            tenant_id=str(tenant),
            roles=roles,
            issuer=self.issuer,
            key_id=signing_key.key_id or "",
        )
