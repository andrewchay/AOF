# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W02.06 — Unified cross-store recovery watermark.

- one checkpoint atomically records ledger/outbox/tombstone/revocation/
  action watermarks
- verify_watermarks() reports drift per store (never silently accepted):
  monotone counters going backwards and rewritten ledger chains are both
  caught; forward progress after the checkpoint is not drift
- the coordinator wires the real store readers
"""

from __future__ import annotations



from bridge.persistence.checkpoint import (
    CheckpointCoordinator,
    WatermarkCheckpointStore,
    Watermarks,
    verify_watermarks,
)


def _wm(**kw) -> Watermarks:
    base = dict(
        ledger_heads={"tenant-a": (5, "sha256:head5")},
        outbox_dispatched=100,
        tombstone_count=3,
        revocation_count=7,
        action_run_count=12,
    )
    base.update(kw)
    return Watermarks(**base)


# ---------------------------------------------------------------------------
# Pure verification semantics
# ---------------------------------------------------------------------------


def test_store_roundtrip(tmp_path):
    store = WatermarkCheckpointStore(tmp_path / "ckpt.sqlite")
    cp = store.take(_wm())
    loaded = store.latest()
    assert loaded is not None
    assert loaded.watermarks == cp.watermarks
    assert loaded.checkpoint_id == cp.checkpoint_id


def _checkpoint(**kw) -> object:
    from bridge.persistence.checkpoint import Checkpoint
    from datetime import datetime, timezone
    return Checkpoint(
        checkpoint_id="ckpt:test",
        taken_at=datetime.now(timezone.utc).isoformat(),
        watermarks=_wm(**kw),
    )


def test_consistent_state_passes():
    cp = _checkpoint()
    report = verify_watermarks(cp, _wm())
    assert report.consistent is True
    assert report.drifts == ()


def test_forward_progress_is_not_drift():
    """checkpoint 之后的正常增长（计数变大、链前进）不是漂移。"""
    cp = _checkpoint()
    advanced = _wm(
        ledger_heads={"tenant-a": (9, "sha256:head9")},
        outbox_dispatched=150,
        tombstone_count=4,
        revocation_count=8,
        action_run_count=20,
    )
    report = verify_watermarks(cp, advanced)
    assert report.consistent is True


def test_counter_regression_is_drift():
    cp = _checkpoint()
    # outbox 和 tombstone 倒退 = 恢复时丢数据
    report = verify_watermarks(cp, _wm(outbox_dispatched=40, tombstone_count=1))
    assert report.consistent is False
    stores = {d.store for d in report.drifts}
    assert {"outbox_dispatched", "tombstone_count"} <= stores


def test_ledger_chain_behind_checkpoint_is_drift():
    cp = _checkpoint()
    report = verify_watermarks(cp, _wm(ledger_heads={"tenant-a": (3, "sha256:head3")}))
    assert report.consistent is False
    assert report.drifts[0].store == "ledger[tenant-a]"


def test_ledger_hash_rewrite_same_sequence_is_drift():
    """同序号不同 hash = 链被重写——审计完整性问题，必须上报。"""
    cp = _checkpoint()
    report = verify_watermarks(cp, _wm(ledger_heads={"tenant-a": (5, "sha256:REWRITTEN")}))
    assert report.consistent is False
    assert report.drifts[0].store == "ledger[tenant-a].hash"


def test_missing_tenant_chain_is_drift():
    cp = _checkpoint()
    report = verify_watermarks(cp, _wm(ledger_heads={}))
    assert report.consistent is False
    assert report.drifts[0].store == "ledger[tenant-a]"


# ---------------------------------------------------------------------------
# Coordinator with real store readers
# ---------------------------------------------------------------------------


def test_coordinator_take_and_verify(tmp_path):
    from bridge.access.deletion import DeletionCoordinator, PayloadVault
    from bridge.access.revocations import RevocationRegistry
    from bridge.audit.outbox_dispatch import FileBrokerTransport, Inbox, OutboxDispatcher
    from bridge.audit.logger import FileOutbox
    from bridge.persistence.checkpoint import (
        read_action_run_count,
        read_ledger_heads,
        read_outbox_dispatched,
        read_revocation_count,
        read_tombstone_count,
    )
    from bridge.persistence.decision_ledger_repository import SQLiteDecisionLedgerRepository
    from bridge.semantic_core.action_runs import SqliteActionRunRepository

    db = tmp_path / "control.sqlite3"
    ledger = SQLiteDecisionLedgerRepository(db)
    actions = SqliteActionRunRepository(db)
    outbox = FileOutbox(str(tmp_path / "outbox"))
    inbox = Inbox(tmp_path / "inbox.sqlite")
    dispatcher = OutboxDispatcher(outbox, transport=FileBrokerTransport(tmp_path / "broker"), inbox=inbox)
    vault = PayloadVault(tmp_path / "vault.sqlite", master_key=b"k")
    coordinator = DeletionCoordinator(vault, path=tmp_path / "deletion.sqlite")
    revocations = RevocationRegistry(tmp_path / "revocations.sqlite")

    # 产生一些真实水位
    from tests.test_decision_ledger_repository import _make_entry

    entry = _make_entry(ledger, "tenant-x", "decision:ck-1")
    ledger.append(entry)

    ckpt_store = WatermarkCheckpointStore(tmp_path / "ckpt.sqlite")
    ckpt_coord = CheckpointCoordinator(
        ckpt_store,
        readers={
            "ledger_heads": lambda: read_ledger_heads(ledger),
            "outbox_dispatched": lambda: read_outbox_dispatched(dispatcher),
            "tombstone_count": lambda: read_tombstone_count(coordinator),
            "revocation_count": lambda: read_revocation_count(revocations),
            "action_run_count": lambda: read_action_run_count(actions),
        },
    )

    checkpoint = ckpt_coord.take_now()
    assert checkpoint.watermarks.ledger_heads["tenant-x"][0] == 1
    assert checkpoint.watermarks.revocation_count == 0

    # 恢复场景：计数倒退（模拟丢了一个 revocation）→ 漂移被报告
    revocations.revoke("subject", "bad-actor")  # live moves forward...
    actual = Watermarks(
        ledger_heads=checkpoint.watermarks.ledger_heads,
        outbox_dispatched=checkpoint.watermarks.outbox_dispatched,
        tombstone_count=checkpoint.watermarks.tombstone_count,
        revocation_count=0,  # ...but the restored store LOST the revocation
        action_run_count=checkpoint.watermarks.action_run_count,
    )
    report = ckpt_coord.verify_against_latest(actual)
    # revocation_count 0 < checkpoint 0? No: checkpoint was taken BEFORE the revoke,
    # so 0 == 0 is consistent; the revoke moved live FORWARD which is not drift.
    assert report is not None and report.consistent is True

    # 反向：checkpoint 之后 revoke，但恢复出的 store 丢了它 → checkpoint 之后
    # 重新取一次水位再对比
    checkpoint2 = ckpt_coord.take_now()
    assert checkpoint2.watermarks.revocation_count == 1
    report2 = verify_watermarks(checkpoint2, actual)
    assert report2.consistent is False
    assert report2.drifts[0].store == "revocation_count"
