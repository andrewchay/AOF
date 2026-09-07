# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W02.05 — Interrupted executions settle from connector receipts, never
blindly re-invoked (plan 6.4/6.6: no duplicate business writes).

A run left in `executing` by a crashed worker is resumed by a NEW service
instance. If the connector supports query_receipt and the receipt has a
definitive outcome, the run settles to that outcome WITHOUT re-invocation.
Otherwise it moves to reconciliation_required.
"""

from __future__ import annotations

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionConnectorRegistry,
    ActionRunService,
    SqliteActionRunRepository,
)
from tests.test_action_runs import CrashingWorkerConnector, _plan


class _ReceiptConnector(CrashingWorkerConnector):
    """Crashes on invoke like the worker-kill scenario, but can report the
    receipt of the dispatched execution afterwards."""

    def __init__(self, receipt) -> None:
        super().__init__()
        self._receipt = receipt
        self.receipt_queries = 0

    def query_receipt(self, request):
        self.receipt_queries += 1
        if isinstance(self._receipt, Exception):
            raise self._receipt
        return self._receipt


def _crash_and_restart(tmp_path, connector):
    registry = ActionConnectorRegistry()
    registry.register("crm-core", connector)
    path = tmp_path / "action-runs.sqlite3"
    decisions = tmp_path / "action-decisions.jsonl"

    live = ActionRunService(
        SqliteActionRunRepository(path),
        connectors=registry,
        decision_store=DecisionProvenanceStore(decisions),
    )
    submitted = live.submit(_plan(tmp_path), actor="operator:alice", rationale="r")
    approved = live.approve(
        submitted.run_id, actor="reviewer:bob", roles=["risk-reviewer"], rationale="ok"
    )
    with pytest.raises(SystemExit):
        live.execute(approved.run_id, actor="executor:crashing-worker")
    assert connector.calls == 1

    restarted = ActionRunService(
        SqliteActionRunRepository(path),
        connectors=registry,
        decision_store=DecisionProvenanceStore(decisions),
    )
    return restarted, approved


def test_receipt_succeeded_settles_without_reinvocation(tmp_path):
    connector = _ReceiptConnector(
        {"outcome": "succeeded", "effect_applied": True, "receipt": {"crm_request_id": "crm-9"}}
    )
    restarted, approved = _crash_and_restart(tmp_path, connector)

    recovered = restarted.execute(approved.run_id, actor="executor:recovery-worker")

    assert recovered.status == "succeeded"
    assert recovered.result["receipt"] == {"crm_request_id": "crm-9"}
    assert connector.calls == 1, "must NOT re-invoke: receipt settled the run"
    assert connector.receipt_queries == 1


def test_receipt_failed_no_effect_settles_failed(tmp_path):
    connector = _ReceiptConnector(
        {"outcome": "failed", "effect_applied": False, "receipt": {}, "error": {"type": "Declined", "message": "rejected"}}
    )
    restarted, approved = _crash_and_restart(tmp_path, connector)

    recovered = restarted.execute(approved.run_id, actor="executor:recovery-worker")

    assert recovered.status == "failed"
    assert connector.calls == 1


def test_receipt_unknown_goes_to_reconciliation(tmp_path):
    connector = _ReceiptConnector(
        {"outcome": "unknown", "effect_applied": None, "receipt": {}}
    )
    restarted, approved = _crash_and_restart(tmp_path, connector)

    recovered = restarted.execute(approved.run_id, actor="executor:recovery-worker")

    assert recovered.status == "reconciliation_required"
    assert connector.calls == 1


def test_receipt_query_failure_goes_to_reconciliation(tmp_path):
    connector = _ReceiptConnector(TimeoutError("receipt endpoint unreachable"))
    restarted, approved = _crash_and_restart(tmp_path, connector)

    recovered = restarted.execute(approved.run_id, actor="executor:recovery-worker")

    assert recovered.status == "reconciliation_required"
    assert connector.calls == 1


def test_connector_without_receipt_query_goes_to_reconciliation(tmp_path):
    """Fail-safe default: no query capability -> never re-invoke blindly."""
    connector = CrashingWorkerConnector()  # no query_receipt method
    restarted, approved = _crash_and_restart(tmp_path, connector)

    recovered = restarted.execute(approved.run_id, actor="executor:recovery-worker")

    assert recovered.status == "reconciliation_required"
    assert connector.calls == 1
