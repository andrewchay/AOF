"""Key-provider abstraction and rotation-aware release attestations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .attestations import HmacReleaseAttestor
from .releases import KnowledgeRelease


class ReleaseKeyProvider(Protocol):
    def current(self) -> tuple[str, bytes]: ...
    def get(self, key_id: str) -> bytes | None: ...


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
