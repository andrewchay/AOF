"""W02.03 — OpenBao transit signing anchor (KMS-safe detached signing).

Implements the ExternalSignerClient protocol (keys.py) against OpenBao's
transit engine: the anchor key NEVER leaves OpenBao — callers submit a
pre-hashed payload and receive a detached signature; verification hits
the same transit endpoint (the server holds the public half).

Used as the W02.03 independent anchor: ledger checkpoint (tenant,
sequence, head_hash) signatures are produced by a key whose material is
outside the database administrator's reach, so a DB admin recomputing a
chain cannot forge a matching anchor signature.

Reference-local transport is plain HTTP with a dev root token
(AOF_OPENBAO_ADDR / AOF_OPENBAO_TOKEN); production fronts OpenBao with
TLS + scoped policies + response wrapping (W08.06).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any



class OpenBaoSignerError(RuntimeError):
    """Raised when the OpenBao transit endpoint is unreachable or rejects
    an operation (auth failure, unknown key, server error)."""


class OpenBaoTransitClient:
    """ExternalSignerClient adapter over the OpenBao transit HTTP API."""

    def __init__(
        self,
        *,
        addr: str | None = None,
        token: str | None = None,
        key_id: str = "aof-anchor",
        timeout: float = 5.0,
    ) -> None:
        self.addr = (addr or os.environ.get("AOF_OPENBAO_ADDR", "http://127.0.0.1:8200")).rstrip("/")
        self.token = token or os.environ.get("AOF_OPENBAO_TOKEN", "aof-dev-root-token")
        self.default_key_id = key_id
        self.timeout = timeout
        self.algorithm = "ed25519-sha2-256"

    # -- ExternalSignerClient ----------------------------------------------

    def sign(self, key_id: str, payload: bytes) -> str:
        response = self._request(
            "POST", f"/v1/transit/sign/{key_id}/sha2-256",
            body={"input": base64.b64encode(payload).decode("ascii")},
        )
        return str(response["data"]["signature"])

    def verify(self, key_id: str, payload: bytes, signature: str) -> bool:
        response = self._request(
            "POST", f"/v1/transit/verify/{key_id}/sha2-256",
            body={
                "input": base64.b64encode(payload).decode("ascii"),
                "signature": signature,
            },
        )
        return bool(response["data"]["valid"])

    # -- transport -----------------------------------------------------------

    def _request(self, method: str, path: str, *, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.addr + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"X-Vault-Token": self.token, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise OpenBaoSignerError(
                f"openbao transit {method} {path} -> HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OpenBaoSignerError(f"openbao unreachable at {self.addr}: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# W02.03 — Independent ledger anchor
# ---------------------------------------------------------------------------


class LedgerAnchor:
    """Signs ledger checkpoints with the OpenBao anchor key.

    Threat model (plan 6.3): a database administrator can recompute a hash
    chain (they own the rows). They CANNOT produce a matching anchor
    signature, because the anchor key lives in OpenBao transit with key
    material inaccessible to DB credentials. Verification compares the
    stored anchor signature against a fresh OpenBao computation: a
    rewritten chain fails the anchor check even if its internal hashes
    are self-consistent.
    """

    def __init__(self, client: OpenBaoTransitClient) -> None:
        self.client = client

    def anchor_checkpoint(
        self, *, tenant_id: str, sequence: int, head_hash: str | None
    ) -> dict[str, Any]:
        payload = self._canonical(tenant_id, sequence, head_hash)
        signature = self.client.sign(self.client.default_key_id, payload)
        return {
            "tenant_id": tenant_id,
            "sequence": sequence,
            "head_hash": head_hash,
            "anchor_signature": signature,
            "anchor_algorithm": self.client.algorithm,
            "anchor_key_id": self.client.default_key_id,
            "anchored_at": _utcnow(),
        }

    def verify_anchor(
        self,
        *,
        tenant_id: str,
        sequence: int,
        head_hash: str | None,
        anchor_signature: str,
    ) -> bool:
        payload = self._canonical(tenant_id, sequence, head_hash)
        return self.client.verify(self.client.default_key_id, payload, anchor_signature)

    @staticmethod
    def _canonical(tenant_id: str, sequence: int, head_hash: str | None) -> bytes:
        canonical = json.dumps(
            {"tenant_id": tenant_id, "sequence": int(sequence), "head_hash": head_hash},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return canonical.encode("utf-8")


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
