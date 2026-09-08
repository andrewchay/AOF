"""W09.03 — Slow-dependency isolation for connector invocations.

- a connector that takes longer than connector_timeout_seconds produces
  an unknown outcome (reconciliation), not a hang
- the action run state transitions to reconciliation_required
- a fast connector is unaffected
"""

from __future__ import annotations

import time


from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionConnectorRegistry,
    ActionRunService,
    SqliteActionRunRepository,
)
from tests.test_action_runs import _plan


class SlowConnector:
    """Takes `delay` seconds to respond."""

    def __init__(self, delay: float):
        self.delay = delay
        self.calls = 0

    def invoke(self, request):
        self.calls += 1
        time.sleep(self.delay)
        return {"outcome": "succeeded", "effect_applied": True, "receipt": {}}

    def compensate(self, request):
        return {"outcome": "succeeded", "effect_applied": True, "receipt": {}}


class FastConnector:
    def __init__(self):
        self.calls = 0

    def invoke(self, request):
        self.calls += 1
        return {"outcome": "succeeded", "effect_applied": True, "receipt": {}}

    def compensate(self, request):
        return {"outcome": "succeeded", "effect_applied": True, "receipt": {}}


def _service(tmp_path, connector, timeout: float = 2.0):
    registry = ActionConnectorRegistry()
    registry.register("crm-core", connector)
    repo = SqliteActionRunRepository(tmp_path / "ar.sqlite")
    return ActionRunService(
        repo, connectors=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "dp.jsonl"),
        connector_timeout_seconds=timeout,
    )


def test_slow_connector_produces_reconciliation(tmp_path):
    """connector 超过 timeout → unknown → reconciliation_required，不挂起。"""
    from bridge.audit.rabbitmq_transport import TransportDownError  # noqa: F401
    connector = SlowConnector(delay=5.0)  # longer than the 2s timeout
    svc = _service(tmp_path, connector)

    plan = _plan(tmp_path)
    submitted = svc.submit(plan, actor="operator:alice", rationale="slow test")
    approved = svc.approve(
        submitted.run_id, actor="reviewer:bob", roles=["risk-reviewer"], rationale="ok"
    )

    started = time.monotonic()
    result = svc.execute(approved.run_id, actor="operator:slow")
    elapsed = time.monotonic() - started

    # the connector timed out → unknown outcome → reconciliation
    assert result.status == "reconciliation_required"
    assert result.result["outcome"] == "unknown"
    assert result.result["error"]["type"] == "ConnectorTimeout"
    # the timeout actually bounded the wait (~2s, not 5s)
    assert elapsed < 4.0, f"took {elapsed:.1f}s — timeout not enforced"
    assert connector.calls == 1, "connector was called exactly once"


def test_fast_connector_unaffected_by_timeout(tmp_path):
    connector = FastConnector()
    svc = _service(tmp_path, connector, timeout=2.0)

    plan = _plan(tmp_path)
    submitted = svc.submit(plan, actor="operator:alice", rationale="fast test")
    approved = svc.approve(
        submitted.run_id, actor="reviewer:bob", roles=["risk-reviewer"], rationale="ok"
    )

    result = svc.execute(approved.run_id, actor="operator:fast")
    assert result.status == "succeeded"
    assert connector.calls == 1
