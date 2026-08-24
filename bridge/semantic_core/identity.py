"""Trusted, signed principals for the Semantic Governance HTTP boundary."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

from .canonical import canonical_json


class PrincipalVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticPrincipal:
    subject: str
    tenant_id: str
    roles: tuple[str, ...]
    issued_at: int
    key_id: str

    def actor_for(self, action: str) -> str:
        allowed = {
            "create": ("admin", "owner", "editor"),
            "edit": ("admin", "owner", "editor"),
            "read": ("admin", "owner", "editor", "reviewer", "validator", "risk-owner", "compiler", "publisher", "viewer"),
            "validate": ("admin", "validator"),
            "waive": ("admin", "risk-owner"),
            "request_changes": ("admin", "reviewer"),
            "approve": ("admin", "reviewer"),
            "compile": ("admin", "compiler"),
            "publish": ("admin", "publisher"),
        }[action]
        role = next((candidate for candidate in allowed if candidate in self.roles), None)
        if role is None:
            raise PrincipalVerificationError(f"principal lacks role for {action}")
        return f"{role}:{self.subject}"


class SignedPrincipalVerifier:
    def __init__(self, *, key_id: str, secret: bytes, max_age_seconds: int = 300) -> None:
        if not key_id or not secret:
            raise ValueError("identity key_id and secret are required")
        self.key_id, self.secret, self.max_age_seconds = key_id, secret, max_age_seconds

    def sign_headers(
        self, *, subject: str, tenant_id: str, roles: Sequence[str], issued_at: int | None = None
    ) -> dict[str, str]:
        timestamp = int(time.time()) if issued_at is None else issued_at
        payload = self._payload(subject, tenant_id, roles, timestamp, self.key_id)
        return {
            "x-aof-principal-subject": subject,
            "x-aof-principal-tenant": tenant_id,
            "x-aof-principal-roles": ",".join(payload["roles"]),
            "x-aof-principal-timestamp": str(timestamp),
            "x-aof-principal-key-id": self.key_id,
            "x-aof-principal-signature": self._signature(payload),
        }

    def verify(self, headers: Mapping[str, str]) -> SemanticPrincipal:
        try:
            subject = headers["x-aof-principal-subject"]
            tenant_id = headers["x-aof-principal-tenant"]
            roles = tuple(filter(None, headers["x-aof-principal-roles"].split(",")))
            issued_at = int(headers["x-aof-principal-timestamp"])
            key_id = headers["x-aof-principal-key-id"]
            signature = headers["x-aof-principal-signature"]
        except (KeyError, ValueError) as exc:
            raise PrincipalVerificationError("signed principal headers are incomplete") from exc
        if key_id != self.key_id or abs(int(time.time()) - issued_at) > self.max_age_seconds:
            raise PrincipalVerificationError("signed principal is expired or uses an unknown key")
        payload = self._payload(subject, tenant_id, roles, issued_at, key_id)
        if not hmac.compare_digest(signature, self._signature(payload)):
            raise PrincipalVerificationError("signed principal signature is invalid")
        return SemanticPrincipal(subject, tenant_id, tuple(payload["roles"]), issued_at, key_id)

    @staticmethod
    def _payload(subject: str, tenant_id: str, roles: Sequence[str], issued_at: int, key_id: str) -> dict:
        if not subject.strip() or not tenant_id.strip() or not roles:
            raise PrincipalVerificationError("principal subject, tenant, and roles are required")
        return {"subject": subject, "tenant_id": tenant_id, "roles": sorted(set(roles)), "issued_at": issued_at, "key_id": key_id}

    def _signature(self, payload: Mapping) -> str:
        return hmac.new(self.secret, canonical_json(payload).encode(), hashlib.sha256).hexdigest()
