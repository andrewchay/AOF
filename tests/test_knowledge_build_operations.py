# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.

from pathlib import Path

import os

import pytest

from bridge.knowledge_build_operations import (
    KnowledgeBuildOperationError,
    KnowledgeBuildOperationStore,
    _owner_is_alive,
    snapshot_digest,
)


def _docs(title: str = "Alpha") -> list[dict]:
    return [{"relative_path": "a.md", "sha256": "h1", "title": title, "links": []}]


def test_succeeded_operation_is_idempotent_and_input_bound(tmp_path: Path):
    store = KnowledgeBuildOperationStore(tmp_path / "ops.sqlite", owner_id=f"pid:{os.getpid()}:owner-a")
    digest = snapshot_digest("kb-a", _docs())
    operation, claimed = store.begin(
        tenant_id="tenant-a", operation_id="op-1", kb_id="kb-a", digest=digest
    )
    assert claimed is True
    assert operation["state"] == "running"
    result = {"ok": True, "release_id": "kb-a@1"}
    store.complete(tenant_id="tenant-a", operation_id="op-1", result=result)

    repeated, claimed = store.begin(
        tenant_id="tenant-a", operation_id="op-1", kb_id="kb-a", digest=digest
    )
    assert claimed is False
    assert repeated["state"] == "succeeded"
    assert repeated["result"] == result

    with pytest.raises(KnowledgeBuildOperationError, match="different build input"):
        store.begin(
            tenant_id="tenant-a",
            operation_id="op-1",
            kb_id="kb-a",
            digest=snapshot_digest("kb-a", _docs("Changed")),
        )


def test_tenant_isolation_and_unknown_operation(tmp_path: Path):
    store = KnowledgeBuildOperationStore(tmp_path / "ops.sqlite", owner_id=f"pid:{os.getpid()}:owner-a")
    store.begin(
        tenant_id="tenant-a",
        operation_id="op-1",
        kb_id="kb-a",
        digest=snapshot_digest("kb-a", _docs()),
    )
    assert store.get(tenant_id="tenant-b", operation_id="op-1") is None


def test_live_process_running_operation_stays_running(tmp_path: Path):
    path = tmp_path / "ops.sqlite"
    owner_a = KnowledgeBuildOperationStore(path, owner_id=f"pid:{os.getpid()}:owner-a")
    owner_a.begin(
        tenant_id="tenant-a", operation_id="op-live", kb_id="kb-a",
        digest=snapshot_digest("kb-a", _docs()),
    )
    observer = KnowledgeBuildOperationStore(path, owner_id=f"pid:{os.getpid()}:observer")
    status = observer.get(tenant_id="tenant-a", operation_id="op-live")
    assert status is not None
    assert status["state"] == "running"


def test_dead_process_running_operation_becomes_recovery_required(tmp_path: Path):
    path = tmp_path / "ops.sqlite"
    owner_a = KnowledgeBuildOperationStore(path, owner_id="pid:99999999:owner-a")
    owner_a.begin(
        tenant_id="tenant-a",
        operation_id="op-1",
        kb_id="kb-a",
        digest=snapshot_digest("kb-a", _docs()),
    )

    restarted = KnowledgeBuildOperationStore(path, owner_id=f"pid:{os.getpid()}:owner-b")
    recovered = restarted.get(tenant_id="tenant-a", operation_id="op-1")
    assert recovered is not None
    assert recovered["state"] == "recovery_required"

    with pytest.raises(KnowledgeBuildOperationError, match="no longer owned"):
        owner_a.complete(
            tenant_id="tenant-a", operation_id="op-1", result={"ok": True}
        )


def test_owner_liveness_rejects_reused_pid_fingerprint():
    assert _owner_is_alive(f"pid:{os.getpid()}:definitely-wrong:owner") is False


def test_snapshot_digest_cross_language_contract_vector():
    assert snapshot_digest("kb1", [
        {"relative_path": "a.md", "sha256": "h1", "title": "甲", "links": ["乙"]},
        {"relative_path": "b.md", "sha256": "h2", "title": "乙", "links": []},
    ]) == "sha256:ec328d972155097e187fa06be525e7f43ebcb5e5052bdbb99b2dbfb707a628b8"
