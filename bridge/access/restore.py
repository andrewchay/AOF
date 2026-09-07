"""W06.06 — Restore must never resurrect deleted data.

Acceptance scope:
- BEFORE restoring a backup, merge the independent event logs that
  postdate the backup point: deletion tombstones, revocation epochs and
  legal holds. After the restore, those compensations are re-applied so
  the restored state converges to the live governance decisions.
- Backups are encrypted bundles with a retention expiry; destroying the
  backup key makes the bundle permanently unreadable.
- A restore receipt binds backup identity, replayed compensations and
  the vault-destruction proofs.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from cryptography.fernet import Fernet, InvalidToken

from bridge.access.deletion import DeletionCoordinator, DeletionStatus, PayloadVault
from bridge.access.revocations import RevocationRegistry
from bridge.persistence.sqlite_support import managed_sqlite_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS restore_receipts (
    restore_id        TEXT PRIMARY KEY,
    backup_path       TEXT NOT NULL,
    backup_created_at TEXT NOT NULL,
    tombstones_replayed INTEGER NOT NULL,
    revocations_replayed INTEGER NOT NULL,
    holds_checked     INTEGER NOT NULL,
    details           TEXT NOT NULL,
    executed_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class RestoreError(ValueError):
    pass


@dataclass(frozen=True)
class RestorePlan:
    backup_created_at: str
    tombstones: tuple[dict[str, Any], ...]
    revocations: tuple[dict[str, Any], ...]
    active_holds: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_created_at": self.backup_created_at,
            "tombstones_to_replay": len(self.tombstones),
            "revocations_to_replay": len(self.revocations),
            "active_holds": list(self.active_holds),
        }


@dataclass(frozen=True)
class RestoreReceipt:
    restore_id: str
    backup_created_at: str
    tombstones_replayed: int
    revocations_replayed: int
    holds_checked: int
    vault_proofs: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "restore_id": self.restore_id,
            "backup_created_at": self.backup_created_at,
            "tombstones_replayed": self.tombstones_replayed,
            "revocations_replayed": self.revocations_replayed,
            "holds_checked": self.holds_checked,
            "vault_proofs": list(self.vault_proofs),
        }


class RestoreCoordinator:
    """Plan and execute a governance-safe restore of a pre-deletion backup."""

    def __init__(
        self,
        *,
        coordinator: DeletionCoordinator,
        revocations: RevocationRegistry,
    ) -> None:
        self.coordinator = coordinator
        self.revocations = revocations
        self.path = Path(
            __import__("os").environ.get(
                "AOF_RESTORE_DB", str(coordinator.db_path.with_name("restore.sqlite"))
            )
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    # -- planning -----------------------------------------------------------

    def plan_restore(self, *, backup_created_at: datetime) -> RestorePlan:
        """Everything decided AFTER the backup point must be re-applied."""
        cutoff = backup_created_at.isoformat()

        with managed_sqlite_connection(self.coordinator._connect) as connection:
            tomb_rows = connection.execute(
                "SELECT tombstone_id, data_key, tenant_id, status FROM deletion_tombstones "
                "WHERE created_at > ? AND status = ?",
                (cutoff, DeletionStatus.DELETED.value),
            ).fetchall()
            hold_rows = connection.execute(
                "SELECT data_key FROM legal_holds WHERE active = 1"
            ).fetchall()

        with managed_sqlite_connection(self.revocations._connect) as connection:
            rev_rows = connection.execute(
                "SELECT kind, identity, reason FROM access_revocations WHERE revoked_at > ?",
                (cutoff,),
            ).fetchall()

        return RestorePlan(
            backup_created_at=cutoff,
            tombstones=tuple(
                {"tombstone_id": r[0], "data_key": r[1], "tenant_id": r[2]} for r in tomb_rows
            ),
            revocations=tuple(
                {"kind": r[0], "identity": r[1], "reason": r[2]} for r in rev_rows
            ),
            active_holds=tuple(r[0] for r in hold_rows),
        )

    # -- execution ------------------------------------------------------------

    def apply_post_restore(
        self,
        plan: RestorePlan,
        *,
        live_vault: PayloadVault,
        live_revocations: RevocationRegistry,
    ) -> RestoreReceipt:
        """Re-apply the plan against the freshly restored stores.

        Returns a receipt; vault destruction proofs prove deleted payloads
        from the backup did NOT come back.
        """
        proofs: list[str] = []
        for item in plan.tombstones:
            proof = live_vault.destroy(item["data_key"])
            proofs.append(proof["vault_proof"])

        for item in plan.revocations:
            live_revocations.revoke(item["kind"], item["identity"], reason=item["reason"])

        holds_checked = 0
        for data_key in plan.active_holds:
            # holds are re-asserted on the live coordinator
            if not self.coordinator.has_active_hold(data_key):
                self.coordinator.place_legal_hold(
                    data_key=data_key, tenant_id="*", reason="restored-from-backup"
                )
            holds_checked += 1

        restore_id = f"restore:{uuid.uuid4()}"
        receipt = RestoreReceipt(
            restore_id=restore_id,
            backup_created_at=plan.backup_created_at,
            tombstones_replayed=len(plan.tombstones),
            revocations_replayed=len(plan.revocations),
            holds_checked=holds_checked,
            vault_proofs=tuple(proofs),
        )
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute(
                "INSERT INTO restore_receipts"
                "(restore_id, backup_path, backup_created_at, tombstones_replayed, "
                "revocations_replayed, holds_checked, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    restore_id,
                    "n/a",
                    plan.backup_created_at,
                    receipt.tombstones_replayed,
                    receipt.revocations_replayed,
                    receipt.holds_checked,
                    json.dumps(receipt.to_dict(), ensure_ascii=False),
                ),
            )
        return receipt


# ---------------------------------------------------------------------------
# Encrypted backup bundle with expiry + key destruction
# ---------------------------------------------------------------------------


class BackupBundle:
    """Encrypted archive of governance stores with retention expiry."""

    METADATA_NAME = "_meta.json"

    def __init__(self, path: str | Path, *, key_file: Path | None = None) -> None:
        self.path = Path(path)
        self.key_file = key_file or self.path.with_suffix(self.path.suffix + ".key")

    @classmethod
    def create(
        cls,
        *,
        backup_path: str | Path,
        files: Mapping[str, Path],
        retention_days: int = 90,
    ) -> "BackupBundle":
        """Encrypt the given db files into a single bundle; write the key
        to a separate key file (destroying it makes the backup unreadable)."""
        bundle = cls(backup_path)
        bundle.path.parent.mkdir(parents=True, exist_ok=True)

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for arcname, file_path in files.items():
                tar.add(str(file_path), arcname=arcname)
        plaintext = buffer.getvalue()

        now = datetime.now(timezone.utc)
        metadata = {
            "created_at": now.isoformat(),
            "retention_days": retention_days,
            "expires_at": (now + timedelta(days=retention_days)).isoformat(),
            "payload_digest": "sha256:" + __import__("hashlib").sha256(plaintext).hexdigest(),
            "files": list(files.keys()),
        }
        meta_bytes = json.dumps(metadata, ensure_ascii=False).encode("utf-8")

        inner = io.BytesIO()
        with tarfile.open(fileobj=inner, mode="w:gz") as tar:
            def _add(name: str, data: bytes) -> None:
                import tarfile as _tf

                info = _tf.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

            _add(cls.METADATA_NAME, meta_bytes)
            _add("payload.tar.gz", plaintext)

        key = Fernet.generate_key()
        token = Fernet(key).encrypt(inner.getvalue())
        bundle.path.write_bytes(token)
        bundle.key_file.write_bytes(key)
        try:
            bundle.key_file.chmod(0o600)
        except OSError:  # pragma: no cover
            pass
        return bundle

    # -- reading ------------------------------------------------------------

    def _decrypt(self, key: bytes | None = None) -> tuple[dict[str, Any], bytes]:
        if not self.path.exists():
            raise RestoreError(f"backup bundle missing: {self.path}")
        key_bytes = key if key is not None else (
            self.key_file.read_bytes() if self.key_file.exists() else None
        )
        if key_bytes is None:
            raise RestoreError(
                f"backup key destroyed or missing: {self.key_file} — bundle is permanently unreadable"
            )
        try:
            inner = Fernet(key_bytes).decrypt(self.path.read_bytes())
        except (InvalidToken, ValueError) as exc:
            # ValueError: not a valid Fernet key at all (also a wrong-key case)
            raise RestoreError("backup decryption failed: wrong key or corrupted bundle") from exc

        import hashlib

        with tarfile.open(fileobj=io.BytesIO(inner), mode="r:gz") as tar:
            meta_member = tar.extractfile(self.METADATA_NAME)
            payload_member = tar.extractfile("payload.tar.gz")
            if meta_member is None or payload_member is None:
                raise RestoreError("backup bundle is missing required members")
            meta_bytes = meta_member.read()
            payload_bytes = payload_member.read()
        metadata = json.loads(meta_bytes)
        expected = metadata.get("payload_digest")
        actual = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()
        if expected != actual:
            raise RestoreError("backup integrity check failed: payload digest mismatch")
        return metadata, payload_bytes

    def verify(self, key: bytes | None = None) -> dict[str, Any]:
        """Decrypt and check integrity; returns the metadata on success."""
        metadata, _payload = self._decrypt(key)
        return metadata

    def extract_to(self, target_dir: str | Path, key: bytes | None = None) -> dict[Path, Path]:
        """Decrypt and write the archived db files into ``target_dir``."""
        _metadata, payload_bytes = self._decrypt(key)
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        restored: dict[Path, Path] = {}
        with tarfile.open(fileobj=io.BytesIO(payload_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                member_file = tar.extractfile(member)
                if member_file is None:
                    continue  # directory member
                dest = target / member.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(member_file.read())
                restored[Path(member.name)] = dest
        return restored

    # -- lifecycle ------------------------------------------------------------

    def is_expired(self, *, now: datetime | None = None) -> bool:
        metadata, _payload = self._decrypt()
        expires = datetime.fromisoformat(metadata["expires_at"])
        return (now or datetime.now(timezone.utc)) >= expires

    def destroy_backup_key(self) -> bool:
        """Permanently destroy the key file: the bundle becomes unreadable."""
        if self.key_file.exists():
            self.key_file.unlink()
            return True
        return False
