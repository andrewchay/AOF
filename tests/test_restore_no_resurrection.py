"""W06.06 — Restores never resurrect deleted data.

- tombstones decided AFTER the backup point are re-applied: the restored
  vault must not bring deleted payloads back
- revocations decided AFTER the backup point remain in force
- legal holds survive the restore
- backups are encrypted, expire on schedule, and destroying the key
  file makes them permanently unreadable
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

import pytest

from bridge.access.deletion import DeletionCoordinator, DeletionStatus, PayloadVault
from bridge.access.restore import BackupBundle, RestoreCoordinator, RestoreError
from bridge.access.revocations import RevocationRegistry


@pytest.fixture()
def governance(tmp_path):
    vault = PayloadVault(tmp_path / "vault.sqlite", master_key=b"governance-key")
    coordinator = DeletionCoordinator(vault, path=tmp_path / "deletion.sqlite")
    revocations = RevocationRegistry(tmp_path / "revocations.sqlite")
    return vault, coordinator, revocations


def _backup_dir(tmp_path):
    return tmp_path / "backups"


def test_deleted_payload_does_not_resurrect_after_restore(tmp_path, governance):
    """核心负例：备份点之后发生的删除，恢复后必须依然生效。"""
    vault, coordinator, revocations = governance
    backup_dir = _backup_dir(tmp_path)

    # 1. 备份点：payload 还在
    vault.put(data_key="k-deleted", tenant_id="t1", payload={"secret": "will-be-deleted"})
    bundle = BackupBundle.create(
        backup_path=backup_dir / "gov.bundle",
        files={"vault.sqlite": vault.path, "deletion.sqlite": coordinator.db_path},
        retention_days=90,
    )
    backup_created_at = datetime.now(timezone.utc)

    # 2. 备份之后：payload 被删除（tombstone 产生）
    coordinator.request_deletion(
        data_key="k-deleted", tenant_id="t1", reason="TTL",
        requested_by="admin:root", policy_id="p", policy_revision=1,
    )
    assert vault.get("k-deleted") is None

    # 3. 恢复流程：解包旧备份文件到新目录（模拟恢复出旧密文）
    restored_dir = tmp_path / "restored"
    bundle.extract_to(restored_dir)
    restored_vault = PayloadVault(restored_dir / "vault.sqlite", master_key=b"governance-key")
    restored_deletion = DeletionCoordinator(
        restored_vault, path=restored_dir / "deletion.sqlite"
    )

    # 4. 恢复计划：合并 LIVE 事件日志中备份点之后的删除决策
    rc = RestoreCoordinator(coordinator=coordinator, revocations=revocations)
    plan = rc.plan_restore(backup_created_at=backup_created_at)
    assert plan.tombstones[0]["data_key"] == "k-deleted"

    # 5. 应用补偿：从旧备份复活出的密文在恢复出的存储中被再次销毁
    restored_revocations = RevocationRegistry(restored_dir / "revocations.sqlite")
    receipt = rc.apply_post_restore(
        plan, live_vault=restored_vault, live_revocations=restored_revocations
    )

    assert receipt.tombstones_replayed == 1
    assert any("k-deleted" in p for p in receipt.vault_proofs)
    assert restored_vault.get("k-deleted") is None  # NOT resurrected


def test_revocations_survive_restore(tmp_path, governance):
    vault, coordinator, revocations = governance
    backup_dir = _backup_dir(tmp_path)

    bundle = BackupBundle.create(
        backup_path=backup_dir / "gov.bundle",
        files={"vault.sqlite": vault.path, "deletion.sqlite": coordinator.db_path},
    )
    backup_created_at = datetime.now(timezone.utc)

    # 备份之后撤销了一个 subject
    revocations.revoke("subject", "offboarded-user", reason="offboarding")

    restored_dir = tmp_path / "restored"
    bundle.extract_to(restored_dir)
    rc = RestoreCoordinator(coordinator=coordinator, revocations=revocations)
    plan = rc.plan_restore(backup_created_at=backup_created_at)
    assert plan.revocations[0]["identity"] == "offboarded-user"

    restored_revocations = RevocationRegistry(restored_dir / "revocations.sqlite")
    receipt = rc.apply_post_restore(plan, live_vault=vault, live_revocations=restored_revocations)

    assert receipt.revocations_replayed == 1
    assert restored_revocations.is_revoked("subject", "offboarded-user") is True


def test_legal_holds_survive_restore(tmp_path, governance):
    vault, coordinator, revocations = governance
    backup_dir = _backup_dir(tmp_path)

    vault.put(data_key="k-held", tenant_id="t1", payload={"secret": "under-hold"})
    bundle = BackupBundle.create(
        backup_path=backup_dir / "gov.bundle",
        files={"vault.sqlite": vault.path, "deletion.sqlite": coordinator.db_path},
    )
    backup_created_at = datetime.now(timezone.utc)

    coordinator.place_legal_hold(data_key="k-held", tenant_id="t1", reason="litigation")

    restored_dir = tmp_path / "restored"
    bundle.extract_to(restored_dir)
    rc = RestoreCoordinator(coordinator=coordinator, revocations=revocations)
    plan = rc.plan_restore(backup_created_at=backup_created_at)
    assert "k-held" in plan.active_holds

    receipt = rc.apply_post_restore(
        plan, live_vault=vault, live_revocations=RevocationRegistry(restored_dir / "r.sqlite")
    )
    assert receipt.holds_checked == 1
    assert coordinator.has_active_hold("k-held") is True


# ---------------------------------------------------------------------------
# Backup bundle: encryption, expiry, key destruction
# ---------------------------------------------------------------------------


def test_backup_is_encrypted_and_verifiable(tmp_path, governance):
    vault, coordinator, _revocations = governance
    bundle = BackupBundle.create(
        backup_path=_backup_dir(tmp_path) / "gov.bundle",
        files={"vault.sqlite": vault.path},
        retention_days=30,
    )
    # bundle bytes are ciphertext, not a plain sqlite/tar file
    raw = bundle.path.read_bytes()
    assert not raw.startswith(b"SQLite format 3")
    assert not raw.startswith(b"\x1f\x8b")  # not plain gzip

    metadata = bundle.verify()
    assert metadata["retention_days"] == 30
    assert "vault.sqlite" in metadata["files"]

    # wrong key cannot decrypt
    with pytest.raises(RestoreError, match="decryption failed"):
        bundle.verify(key=b"ZmFrZS1rZXktZmFrZS1rZXktZmFrZS1rZXk=")


def test_backup_expiry(tmp_path, governance):
    vault, coordinator, _revocations = governance
    bundle = BackupBundle.create(
        backup_path=_backup_dir(tmp_path) / "gov.bundle",
        files={"vault.sqlite": vault.path},
        retention_days=7,
    )
    assert bundle.is_expired() is False
    assert bundle.is_expired(now=datetime.now(timezone.utc) + timedelta(days=8)) is True


def test_backup_key_destruction_makes_bundle_unreadable(tmp_path, governance):
    vault, coordinator, _revocations = governance
    bundle = BackupBundle.create(
        backup_path=_backup_dir(tmp_path) / "gov.bundle",
        files={"vault.sqlite": vault.path},
    )
    assert bundle.verify() is not None  # readable while key exists

    assert bundle.destroy_backup_key() is True

    with pytest.raises(RestoreError, match="permanently unreadable"):
        bundle.verify()
    assert bundle.destroy_backup_key() is False  # already gone
