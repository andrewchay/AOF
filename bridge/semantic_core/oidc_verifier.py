"""W01.02 — OIDC JWT verification against a trusted IdP (Keycloak).

Enterprise interactive login path (plan 5.1): the IdP signs a JWT; AOF
verifies issuer, audience, expiry, signature algorithm whitelist and key
id against the IdP's JWKS (fetched via OIDC discovery). The signature
secret never lives in AOF - unlike the HMAC envelope path (which stays
as the controlled service-to-service compatibility mode).

The verifier maps verified claims onto the same SemanticPrincipal the
governance plane already consumes, so downstream authorization is
unchanged.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

import jwt
from jwt import PyJWKClient

from bridge.semantic_core.identity import PrincipalVerificationError, SemanticPrincipal

_ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")


class OidcVerifier:
    """Verifies Bearer JWTs against an OIDC IdP's JWKS."""

    def __init__(
        self,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        jwks_url: str | None = None,
        tenant_id: str | None = None,
        role_claim: str = "realm_access.roles",
    ) -> None:
        self.issuer = issuer or os.environ.get("AOF_OIDC_ISSUER")
        self.audience = audience or os.environ.get("AOF_OIDC_AUDIENCE")
        self.tenant_id = tenant_id or os.environ.get("AOF_OIDC_TENANT_ID")
        self.role_claim = role_claim
        if not self.issuer:
            raise PrincipalVerificationError("OIDC issuer is required (AOF_OIDC_ISSUER)")
        jwks_url = jwks_url or os.environ.get("AOF_OIDC_JWKS_URL")
        if not jwks_url:
            jwks_url = f"{self.issuer.rstrip('/')}/protocol/openid-connect/certs"
        self._jwks = PyJWKClient(jwks_url)

    # -- verification ---------------------------------------------------------

    def verify_bearer(self, authorization: str) -> SemanticPrincipal:
        """Verify an ``Authorization: Bearer <jwt>`` header value."""
        if not authorization.startswith("Bearer "):
            raise PrincipalVerificationError("authorization header must be Bearer")
        token = authorization[len("Bearer "):].strip()
        return self.verify_token(token)

    def verify_token(self, token: str) -> SemanticPrincipal:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
        except Exception as exc:
            raise PrincipalVerificationError(f"cannot resolve signing key: {exc}") from exc

        options: Any = {"require": ["exp", "iat", "iss", "sub"]}
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(_ALLOWED_ALGORITHMS),
                audience=self.audience,
                issuer=self.issuer,
                options=options,
            )
        except jwt.ExpiredSignatureError as exc:
            raise PrincipalVerificationError("token is expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise PrincipalVerificationError(f"audience mismatch: {exc}") from exc
        except jwt.InvalidIssuerError as exc:
            raise PrincipalVerificationError(f"issuer mismatch: {exc}") from exc
        except jwt.InvalidAlgorithmError as exc:
            raise PrincipalVerificationError(f"algorithm not allowed: {exc}") from exc
        except jwt.PyJWTError as exc:
            raise PrincipalVerificationError(f"token verification failed: {exc}") from exc

        roles = self._roles(claims)
        if not roles:
            raise PrincipalVerificationError("token carries no usable roles")

        tenant_id = self.tenant_id or str(claims.get("tenant_id") or "")
        if not tenant_id:
            raise PrincipalVerificationError("token carries no tenant_id (and no default configured)")

        return SemanticPrincipal(
            subject=str(claims["sub"]),
            tenant_id=tenant_id,
            roles=tuple(roles),
            issued_at=int(claims["iat"]),
            key_id=str(claims.get("kid", "")),
        )

    def _roles(self, claims: Mapping[str, Any]) -> list[str]:
        """Extract roles from the configured claim (Keycloak realm_access.roles
        by default; also accepts a flat 'roles' list)."""
        if self.role_claim == "realm_access.roles":
            realm = claims.get("realm_access") or {}
            roles = realm.get("roles") or []
        else:
            roles = claims.get(self.role_claim) or claims.get("roles") or []
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.split(",") if r.strip()]
        return [str(r) for r in roles if isinstance(r, str) and r.strip()]
