"""W02.03 — Governed release anchor: signs published release digests with
the OpenBao anchor key so that a DB admin recomputing the ledger cannot
forge a matching anchor signature.

Wire into SemanticGovernanceService.publish() via the anchor_publish()
method. The anchor is stored in the published manifest under
``anchor_signature`` — tampering with the manifest after publication
fails anchor verification even if the internal hashes are self-consistent.
"""

from __future__ import annotations

import json

from typing import Any

from bridge.semantic_core.openbao_signer import LedgerAnchor, OpenBaoTransitClient


class GovernedReleaseAnchor:
    """Signs published release manifests with the OpenBao anchor key."""

    def __init__(self, *, openbao_addr: str | None = None, openbao_token: str | None = None) -> None:
        self.client = OpenBaoTransitClient(addr=openbao_addr, token=openbao_token)
        self.anchor = LedgerAnchor(self.client)

    def anchor_publish(
        self,
        *,
        release_id: str,
        release_digest: str,
        tenant_id: str,
        publish_decision_id: str,
    ) -> dict[str, Any]:
        """Anchor a published release: sign the canonical tuple and return
        the anchor block to embed in the manifest."""
        # canonical payload = the identity of the release
        canonical = json.dumps(
            {
                "release_id": release_id,
                "release_digest": release_digest,
                "tenant_id": tenant_id,
                "publish_decision_id": publish_decision_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = self.client.sign(self.client.default_key_id, canonical)
        return {
            "release_id": release_id,
            "release_digest": release_digest,
            "tenant_id": tenant_id,
            "publish_decision_id": publish_decision_id,
            "anchor_signature": signature,
            "anchor_algorithm": self.client.algorithm,
            "anchor_key_id": self.client.default_key_id,
        }

    def verify_publish(
        self,
        *,
        release_id: str,
        release_digest: str,
        tenant_id: str,
        publish_decision_id: str,
        anchor_signature: str,
    ) -> bool:
        """Verify that a published release manifest has not been tampered
        with after publication. Returns True if the anchor signature matches."""
        canonical = json.dumps(
            {
                "release_id": release_id,
                "release_digest": release_digest,
                "tenant_id": tenant_id,
                "publish_decision_id": publish_decision_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.client.verify(self.client.default_key_id, canonical, anchor_signature)

    def anchor_checkpoint(
        self, *, tenant_id: str, sequence: int, head_hash: str | None
    ) -> dict[str, Any]:
        """Delegate to LedgerAnchor for checkpoint anchoring."""
        return self.anchor.anchor_checkpoint(
            tenant_id=tenant_id, sequence=sequence, head_hash=head_hash
        )


