"""Rotation-aware local and external signing-provider abstractions."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .attestations import HmacReleaseAttestor
from .canonical import canonical_json
from .query_runs import QueryRun
from .releases import KnowledgeRelease, ReleaseError


class ReleaseKeyProvider(Protocol):
    def current(self) -> tuple[str, bytes]: ...
    def get(self, key_id: str) -> bytes | None: ...


class DetachedSigningProvider(Protocol):
    """KMS/HSM-safe boundary: callers never request raw key material."""

    @property
    def algorithm(self) -> str: ...
    def current_key_id(self) -> str: ...
    def sign(self, payload: bytes, *, key_id: str) -> str: ...
    def verify(self, payload: bytes, *, key_id: str, signature: str) -> bool: ...


class ExternalSignerClient(Protocol):
    """Minimal adapter implemented by a KMS/HSM SDK integration."""

    def sign(self, key_id: str, payload: bytes) -> str: ...
    def verify(self, key_id: str, payload: bytes, signature: str) -> bool: ...


class ExternalSigningProvider:
    """Delegates detached operations without exposing any key-read method."""

    def __init__(
        self,
        client: ExternalSignerClient,
        *,
        algorithm: str,
        current_key_id: str,
    ) -> None:
        if not algorithm.strip() or not current_key_id.strip():
            raise ValueError("external signing algorithm and current key ID are required")
        self.client = client
        self.algorithm = algorithm.strip()
        self._current_key_id = current_key_id.strip()

    def current_key_id(self) -> str:
        return self._current_key_id

    def rotate(self, key_id: str) -> None:
        if not key_id.strip():
            raise ValueError("external signing key ID is required")
        self._current_key_id = key_id.strip()

    def sign(self, payload: bytes, *, key_id: str) -> str:
        return self.client.sign(key_id, payload)

    def verify(self, payload: bytes, *, key_id: str, signature: str) -> bool:
        return self.client.verify(key_id, payload, signature)


class LocalSigningKeyProvider:
    """In-process reference provider for development and deterministic tests."""

    algorithm = "hmac-sha256"

    def __init__(self, keys: Mapping[str, bytes], *, current_key_id: str) -> None:
        values = dict(keys)
        if not values or any(not key_id.strip() or not secret for key_id, secret in values.items()):
            raise ValueError("signing keys require non-empty IDs and key material")
        if current_key_id not in values:
            raise ValueError(f"current signing key not found: {current_key_id}")
        self._keys = values
        self._current_key_id = current_key_id

    def current_key_id(self) -> str:
        return self._current_key_id

    def rotate(self, key_id: str) -> None:
        if key_id not in self._keys:
            raise ValueError(f"signing key not found: {key_id}")
        self._current_key_id = key_id

    def sign(self, payload: bytes, *, key_id: str) -> str:
        secret = self._keys.get(key_id)
        if secret is None:
            raise ValueError(f"signing key not found: {key_id}")
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, *, key_id: str, signature: str) -> bool:
        secret = self._keys.get(key_id)
        if secret is None:
            return False
        expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)


class ProviderReleaseAttestor:
    """Release attestation over a detached signing provider."""

    def __init__(self, provider: DetachedSigningProvider) -> None:
        self.provider = provider

    def sign(
        self,
        release: KnowledgeRelease,
        *,
        actor: str,
        decision_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        if release.scope.get("tenant_id") != tenant_id:
            raise ReleaseError("release scope tenant_id does not match attestation tenant_id")
        key_id = self.provider.current_key_id()
        payload = {
            "api_version": "aof.release-attestation/v1",
            "algorithm": self.provider.algorithm,
            "key_id": key_id,
            "tenant_id": tenant_id,
            "release_id": release.release_id,
            "release_digest": release.release_digest,
            "actor": actor,
            "decision_id": decision_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        signature = self.provider.sign(
            canonical_json(payload).encode("utf-8"), key_id=key_id
        )
        return {**payload, "signature": signature}

    def verify(
        self, attestation: Mapping[str, Any], *, release: KnowledgeRelease
    ) -> bool:
        signature = attestation.get("signature")
        key_id = attestation.get("key_id")
        if not isinstance(signature, str) or not isinstance(key_id, str):
            return False
        payload = {key: value for key, value in attestation.items() if key != "signature"}
        if payload.get("algorithm") != self.provider.algorithm:
            return False
        if payload.get("release_id") != release.release_id:
            return False
        if payload.get("release_digest") != release.release_digest:
            return False
        if payload.get("tenant_id") != release.scope.get("tenant_id"):
            return False
        return self.provider.verify(
            canonical_json(payload).encode("utf-8"),
            key_id=key_id,
            signature=signature,
        )


class ProviderQueryEvidenceAttestor:
    """QueryRun evidence attestation over the same provider contract."""

    def __init__(self, provider: DetachedSigningProvider) -> None:
        self.provider = provider

    def sign(self, run: QueryRun) -> dict[str, Any]:
        key_id = self.provider.current_key_id()
        payload = {
            "api_version": "aof.query-evidence-attestation/v1",
            "algorithm": self.provider.algorithm,
            "key_id": key_id,
            "tenant_id": run.tenant_id,
            "query_run_id": run.query_run_id,
            "query_run_digest": run.run_digest,
            "evidence_package_digest": run.evidence_package.get("package_digest"),
        }
        return {
            **payload,
            "signature": self.provider.sign(
                canonical_json(payload).encode("utf-8"), key_id=key_id
            ),
        }

    def verify(self, attestation: Mapping[str, Any], *, run: QueryRun) -> bool:
        signature = attestation.get("signature")
        key_id = attestation.get("key_id")
        if not isinstance(signature, str) or not isinstance(key_id, str):
            return False
        payload = {key: value for key, value in attestation.items() if key != "signature"}
        expected = {
            "api_version": "aof.query-evidence-attestation/v1",
            "algorithm": self.provider.algorithm,
            "key_id": key_id,
            "tenant_id": run.tenant_id,
            "query_run_id": run.query_run_id,
            "query_run_digest": run.run_digest,
            "evidence_package_digest": run.evidence_package.get("package_digest"),
        }
        return payload == expected and self.provider.verify(
            canonical_json(payload).encode("utf-8"),
            key_id=key_id,
            signature=signature,
        )


@dataclass
class KeyringProvider:
    keys: Mapping[str, bytes]
    current_key_id: str

    def current(self) -> tuple[str, bytes]:
        key = self.get(self.current_key_id)
        if key is None:
            raise ValueError(f"current release key not found: {self.current_key_id}")
        return self.current_key_id, key

    def get(self, key_id: str) -> bytes | None:
        return self.keys.get(key_id)


class RotatingReleaseAttestor:
    def __init__(self, provider: ReleaseKeyProvider) -> None:
        self.provider = provider

    def sign(self, release: KnowledgeRelease, **claims):
        key_id, secret = self.provider.current()
        return HmacReleaseAttestor(key_id=key_id, secret=secret).sign(release, **claims)

    def verify(self, attestation, *, release: KnowledgeRelease) -> bool:
        key_id = attestation.get("key_id")
        secret = self.provider.get(key_id) if isinstance(key_id, str) else None
        return False if secret is None else HmacReleaseAttestor(
            key_id=key_id, secret=secret
        ).verify(attestation, release=release)
