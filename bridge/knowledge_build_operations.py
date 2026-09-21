# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""Tenant-scoped durable operation ledger for MCP knowledge builds."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from bridge.persistence.sqlite_support import managed_sqlite_connection

_OPERATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _process_start_token(pid: int) -> str | None:
    """Return a stable process-start fingerprint to reject PID reuse."""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    started = result.stdout.strip()
    return hashlib.sha256(started.encode()).hexdigest()[:16] if started else None


_PROCESS_START_TOKEN = _process_start_token(os.getpid()) or "unknown"
PROCESS_OWNER_ID = f"pid:{os.getpid()}:{_PROCESS_START_TOKEN}:{uuid.uuid4()}"


def _owner_is_alive(owner_id: str) -> bool:
    parts = owner_id.split(":", 3)
    if len(parts) < 3 or parts[0] != "pid":
        return False
    try:
        pid = int(parts[1])
        os.kill(pid, 0)
    except (ValueError, OSError):
        return False
    # Legacy owner IDs used pid:<pid>:<uuid>; keep them readable. New IDs also
    # bind the process start fingerprint, so a recycled PID is not a live owner.
    if len(parts) == 4:
        current = _process_start_token(pid)
        return current is not None and current == parts[2]
    return True


class KnowledgeBuildOperationError(RuntimeError):
    pass


def snapshot_digest(kb_id: str, docs: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        {
            "kb_id": kb_id,
            "docs": sorted(
                (
                    {
                        "relative_path": str(doc["relative_path"]),
                        "sha256": str(doc["sha256"]),
                        "title": str(doc["title"]),
                        "links": sorted(str(link) for link in doc.get("links", [])),
                    }
                    for doc in docs
                ),
                key=lambda item: item["relative_path"],
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class KnowledgeBuildOperationStore:
    def __init__(self, path: Path, *, owner_id: str = PROCESS_OWNER_ID):
        self.path = path
        self.owner_id = owner_id
        path.parent.mkdir(parents=True, exist_ok=True)
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_build_operations (
                    tenant_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    kb_id TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    PRIMARY KEY (tenant_id, operation_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if not _OPERATION_ID.fullmatch(operation_id):
            raise KnowledgeBuildOperationError("operation_id must match [A-Za-z0-9._:-]{1,128}")

    def begin(self, *, tenant_id: str, operation_id: str, kb_id: str, digest: str) -> tuple[dict[str, Any], bool]:
        self._validate_operation_id(operation_id)
        now = time.time()
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM knowledge_build_operations WHERE tenant_id = ? AND operation_id = ?",
                (tenant_id, operation_id),
            ).fetchone()
            if row is not None:
                item = self._row(row)
                if item["kb_id"] != kb_id or item["snapshot_digest"] != digest:
                    raise KnowledgeBuildOperationError("operation_id is already bound to different build input")
                if (
                    item["state"] == "running"
                    and item["owner_id"] != self.owner_id
                    and not _owner_is_alive(item["owner_id"])
                ):
                    connection.execute(
                        "UPDATE knowledge_build_operations SET state = 'recovery_required', updated_at = ? WHERE tenant_id = ? AND operation_id = ?",
                        (now, tenant_id, operation_id),
                    )
                    item["state"] = "recovery_required"
                    item["updated_at"] = now
                return item, False
            connection.execute(
                """INSERT INTO knowledge_build_operations
                (tenant_id, operation_id, kb_id, snapshot_digest, state, owner_id, started_at, updated_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?, ?)""",
                (tenant_id, operation_id, kb_id, digest, self.owner_id, now, now),
            )
        return self.get(tenant_id=tenant_id, operation_id=operation_id) or {}, True

    def complete(self, *, tenant_id: str, operation_id: str, result: dict[str, Any]) -> None:
        self._finish(tenant_id, operation_id, "succeeded", result=result)

    def fail(self, *, tenant_id: str, operation_id: str, error: str, result: dict[str, Any] | None = None) -> None:
        self._finish(tenant_id, operation_id, "failed", result=result, error=error)

    def _finish(self, tenant_id: str, operation_id: str, state: str, *, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        with managed_sqlite_connection(self._connect) as connection:
            cursor = connection.execute(
                """UPDATE knowledge_build_operations
                SET state = ?, updated_at = ?, result_json = ?, error = ?
                WHERE tenant_id = ? AND operation_id = ? AND state = 'running' AND owner_id = ?""",
                (state, time.time(), json.dumps(result, ensure_ascii=False) if result is not None else None, error, tenant_id, operation_id, self.owner_id),
            )
            if cursor.rowcount != 1:
                raise KnowledgeBuildOperationError("operation is no longer owned by this process")

    def get(self, *, tenant_id: str, operation_id: str) -> dict[str, Any] | None:
        self._validate_operation_id(operation_id)
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM knowledge_build_operations WHERE tenant_id = ? AND operation_id = ?",
                (tenant_id, operation_id),
            ).fetchone()
            if row is None:
                return None
            item = self._row(row)
            if (
                item["state"] == "running"
                and item["owner_id"] != self.owner_id
                and not _owner_is_alive(item["owner_id"])
            ):
                now = time.time()
                connection.execute(
                    "UPDATE knowledge_build_operations SET state = 'recovery_required', updated_at = ? WHERE tenant_id = ? AND operation_id = ?",
                    (now, tenant_id, operation_id),
                )
                item["state"] = "recovery_required"
                item["updated_at"] = now
            return item

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return {
            "operation_id": str(row["operation_id"]),
            "kb_id": str(row["kb_id"]),
            "snapshot_digest": str(row["snapshot_digest"]),
            "state": str(row["state"]),
            "owner_id": str(row["owner_id"]),
            "started_at": float(row["started_at"]),
            "updated_at": float(row["updated_at"]),
            "result": result,
            "error": row["error"],
        }
