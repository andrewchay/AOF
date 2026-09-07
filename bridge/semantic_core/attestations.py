# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Detached cryptographic attestations for immutable Knowledge Releases."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Mapping

from .canonical import canonical_json
from .releases import KnowledgeRelease, ReleaseError


class HmacReleaseAttestor:
    """Key-identified HMAC signer; key material is never written to an attestation."""

    def __init__(self, *, key_id: str, secret: bytes) -> None:
        if not key_id.strip() or not secret:
            raise ValueError("key_id and secret are required")
        self.key_id = key_id
        self._secret = secret

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
        payload = {
            "api_version": "aof.release-attestation/v1",
            "algorithm": "hmac-sha256",
            "key_id": self.key_id,
            "tenant_id": tenant_id,
            "release_id": release.release_id,
            "release_digest": release.release_digest,
            "actor": actor,
            "decision_id": decision_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        return {**payload, "signature": self._signature(payload)}

    def verify(self, attestation: Mapping[str, Any], *, release: KnowledgeRelease) -> bool:
        signature = attestation.get("signature")
        if not isinstance(signature, str):
            return False
        payload = {key: value for key, value in attestation.items() if key != "signature"}
        if payload.get("algorithm") != "hmac-sha256" or payload.get("key_id") != self.key_id:
            return False
        if payload.get("release_id") != release.release_id:
            return False
        if payload.get("release_digest") != release.release_digest:
            return False
        if payload.get("tenant_id") != release.scope.get("tenant_id"):
            return False
        return hmac.compare_digest(signature, self._signature(payload))

    def _signature(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(
            self._secret,
            canonical_json(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
