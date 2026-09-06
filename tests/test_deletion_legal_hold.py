"""W06.05 — Deletion, legal hold and immutable-audit coordination.

- sensitive payloads live in an encrypted vault; deletion destroys the
  entry and its key material, restores cannot resurrect it
- deletion NEVER touches the audit chain: verify_integrity is identical
  before and after
- legal hold overrides deletion: pending_legal_review, data intact,
  nothing silently deleted or falsely reported deleted
- deletion receipts bind the tombstone and the policy revision
"""

from __future__ import annotations

import pytest

from bridge.access.data_governance import RetentionPolicy
from bridge.access.deletion import (
    DeletionCoordinator,
    DeletionError,
    DeletionStatus,
    PayloadVault,
)
from bridge.decision_provenance import DecisionProvenanceStore


def _coordinator(tmp_path, monkeypatch, shared_secret: bytes = b"vault-test-key"):
    # jsonl backend is required for the "chain untouched" assertion (line-level
    # ledger inspection); monkeypatch restores the env after each test.
    monkeypatch.setenv("AOF_DECISION_LEDGER_BACKEND", "jsonl")
    vault = PayloadVault(tmp_path / "vault.sqlite", master_key=shared_secret)
    coordinator = DeletionCoordinator(vault, path=tmp_path / "deletion.sqlite")
    return coordinator, vault


# ---------------------------------------------------------------------------
# Vault: encrypted storage, destroy semantics
# ---------------------------------------------------------------------------


def test_vault_roundtrip(tmp_path, monkeypatch):
    coordinator, vault = _coordinator(tmp_path, monkeypatch)
    digest = vault.put(
        data_key="decision:1:question",
        tenant_id="tenant-a",
        payload={"question": "收入偏差的原因是什么？", "amount": 80},
    )
    assert digest.startswith("sha256:")
    assert vault.get("decision:1:question") == {"question": "收入偏差的原因是什么？", "amount": 80}
    assert vault.digest("decision:1:question") == digest


def test_vault_destroy_makes_payload_unrecoverable(tmp_path, monkeypatch):
    coordinator, vault = _coordinator(tmp_path, monkeypatch)
    vault.put(data_key="k-1", tenant_id="t1", payload={"secret": "sensitive-value"})

    proof = vault.destroy("k-1")
    assert proof["destroyed"] is True
    assert "entry-purged" in proof["vault_proof"]
    assert vault.get("k-1") is None

    # restore/replay cannot resurrect: re-put with same key still needs new write
    # and the old ciphertext is gone
    proof2 = vault.destroy("k-1")
    assert proof2["destroyed"] is False
    assert "already-absent" in proof2["vault_proof"]


# ---------------------------------------------------------------------------
# Deletion coordination + immutable audit
# ---------------------------------------------------------------------------


def test_deletion_leaves_audit_chain_untouched(tmp_path, monkeypatch):
    """核心负例：不修改既有链 hash 来假装删除。"""
    import os

    os.environ["AOF_DECISION_LEDGER_BACKEND"] = "jsonl"
    ledger_path = tmp_path / "ledger.jsonl"
    store = DecisionProvenanceStore(ledger_path)
    entry = store.record(
        agent_id="agent",
        decision_type="finance",
        conclusion="revenue is 80",
        rationale="sql evidence",
        tenant_id="tenant-a",
        metadata={"payload_key": "decision:1:question"},
    )
    before = store.verify_integrity()
    assert before["valid"] is True
    head_before = before["head_hash"]

    coordinator, vault = _coordinator(tmp_path, monkeypatch)
    vault.put(
        data_key="decision:1:question",
        tenant_id="tenant-a",
        payload={"question": "收入偏差的原因？"},
    )
    tombstone = coordinator.request_deletion(
        data_key="decision:1:question",
        tenant_id="tenant-a",
        reason="retention expired",
        requested_by="admin:root",
        policy_id="fin-default",
        policy_revision=2,
    )

    assert tombstone.status == DeletionStatus.DELETED.value
    # the audit chain is byte-identical: same head, still valid
    after = store.verify_integrity()
    assert after["valid"] is True
    assert after["head_hash"] == head_before
    assert after["entries_checked"] == before["entries_checked"]


def test_deletion_receipt_binds_tombstone_and_policy(tmp_path, monkeypatch):
    coordinator, vault = _coordinator(tmp_path, monkeypatch)
    vault.put(data_key="k-2", tenant_id="t1", payload={"secret": "x"})

    tombstone = coordinator.request_deletion(
        data_key="k-2", tenant_id="t1", reason="TTL",
        requested_by="admin:root", policy_id="fin-default", policy_revision=3,
    )
    receipt = coordinator.deletion_receipt(tombstone.tombstone_id)

    assert receipt["action"] == "payload_deletion"
    assert receipt["tombstone_id"] == tombstone.tombstone_id
    assert receipt["policy_revision"] == 3
    assert receipt["vault_readable_after"] is False
    assert "vault_proof" in receipt

    with pytest.raises(DeletionError):
        coordinator.deletion_receipt("tomb:nonexistent")


# ---------------------------------------------------------------------------
# Legal hold overrides deletion
# ---------------------------------------------------------------------------


def test_legal_hold_blocks_deletion_as_pending_review(tmp_path, monkeypatch):
    coordinator, vault = _coordinator(tmp_path, monkeypatch)
    vault.put(data_key="k-3", tenant_id="t1", payload={"secret": "held-value"})

    coordinator.place_legal_hold(data_key="k-3", tenant_id="t1", reason="litigation 2026-09")

    tombstone = coordinator.request_deletion(
        data_key="k-3", tenant_id="t1", reason="TTL expired",
        requested_by="admin:root", policy_id="fin-default", policy_revision=1,
    )

    assert tombstone.status == DeletionStatus.PENDING_LEGAL_REVIEW.value
    assert tombstone.vault_proof is None
    # data is STILL there — nothing silently deleted
    assert vault.get("k-3") == {"secret": "held-value"}
    assert coordinator.pending_legal_reviews(tenant_id="t1")[0].data_key == "k-3"


def test_delete_after_hold_release_succeeds(tmp_path, monkeypatch):
    coordinator, vault = _coordinator(tmp_path, monkeypatch)
    vault.put(data_key="k-4", tenant_id="t1", payload={"secret": "value"})

    coordinator.place_legal_hold(data_key="k-4", tenant_id="t1", reason="hold")
    first = coordinator.request_deletion(
        data_key="k-4", tenant_id="t1", reason="TTL", requested_by="admin:root",
    )
    assert first.status == DeletionStatus.PENDING_LEGAL_REVIEW.value

    assert coordinator.release_legal_hold("k-4") is True
    second = coordinator.request_deletion(
        data_key="k-4", tenant_id="t1", reason="TTL", requested_by="admin:root",
        policy_id="fin-default", policy_revision=1,
    )
    assert second.status == DeletionStatus.DELETED.value
    assert vault.get("k-4") is None


def test_duplicate_active_hold_rejected(tmp_path, monkeypatch):
    coordinator, _vault = _coordinator(tmp_path, monkeypatch)
    coordinator.place_legal_hold(data_key="k-5", tenant_id="t1", reason="first")
    with pytest.raises(DeletionError, match="already active"):
        coordinator.place_legal_hold(data_key="k-5", tenant_id="t1", reason="second")
