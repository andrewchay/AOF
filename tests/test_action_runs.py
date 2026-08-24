"""Transactional ActionRun approval, idempotency, execution, and recovery."""

from __future__ import annotations

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionConnectorRegistry,
    ActionRequest,
    ActionRunError,
    ActionRunService,
    GovernedActionPlanner,
    SqliteActionRunRepository,
)
from tests.test_action_planning import _action_runtime


def _plan(tmp_path):
    repository, action, policy = _action_runtime(tmp_path)
    return GovernedActionPlanner(repository).plan(
        ActionRequest.create(
            channel="production",
            action_type_id=action.resource_id,
            object_ids=["customer:alice"],
            inputs={"reason": "confirmed fraud"},
            purpose="fraud-response",
            idempotency_key="suspend-alice-001",
            policy_resource_id=policy.resource_id,
        ),
        tenant_id="acme",
        roles=["risk-operator"],
    )


class SuccessfulCrmConnector:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request):
        self.calls += 1
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"crm_request_id": "crm-001"},
        }

    def compensate(self, request):
        raise AssertionError("compensation is not expected for a successful action")


def test_action_run_requires_separate_approval_and_executes_exactly_once(tmp_path) -> None:
    connector = SuccessfulCrmConnector()
    registry = ActionConnectorRegistry()
    registry.register("crm-core", connector)
    repository = SqliteActionRunRepository(tmp_path / "action-runs.sqlite3")
    service = ActionRunService(
        repository,
        connectors=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "action-decisions.jsonl"),
    )
    plan = _plan(tmp_path)

    submitted = service.submit(
        plan, actor="operator:alice", rationale="Respond to confirmed fraud."
    )
    duplicate = service.submit(
        plan, actor="operator:alice", rationale="Respond to confirmed fraud."
    )

    assert submitted.status == "awaiting_approval"
    assert duplicate.run_id == submitted.run_id
    with pytest.raises(ActionRunError, match="separation of duties"):
        service.approve(
            submitted.run_id,
            actor="operator:alice",
            roles=["risk-reviewer"],
            rationale="Self approval must fail.",
        )
    with pytest.raises(ActionRunError, match="required approval role"):
        service.approve(
            submitted.run_id,
            actor="reviewer:bob",
            roles=["viewer"],
            rationale="An unprivileged reviewer must fail.",
        )
    approved = service.approve(
        submitted.run_id,
        actor="reviewer:bob",
        roles=["risk-reviewer"],
        rationale="Impact and evidence reviewed.",
    )
    succeeded = service.execute(approved.run_id, actor="executor:worker-1")
    replay = service.execute(approved.run_id, actor="executor:worker-2")

    assert approved.status == "approved"
    assert succeeded.status == "succeeded"
    assert replay.run_digest == succeeded.run_digest
    assert connector.calls == 1
    assert succeeded.result["receipt"]["crm_request_id"] == "crm-001"
    assert repository.verify_all()["valid"] is True


class PartiallyFailedCrmConnector:
    def __init__(self) -> None:
        self.compensations = 0

    def invoke(self, request):
        return {
            "outcome": "failed",
            "effect_applied": True,
            "receipt": {"crm_request_id": "crm-partial-001"},
            "error": {"code": "downstream_confirmation_failed"},
        }

    def compensate(self, request):
        self.compensations += 1
        assert request["operation"] == "resume_customer"
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"crm_request_id": "crm-compensate-001"},
        }


def test_reversible_action_compensates_a_confirmed_partial_failure(tmp_path) -> None:
    connector = PartiallyFailedCrmConnector()
    registry = ActionConnectorRegistry()
    registry.register("crm-core", connector)
    service = ActionRunService(
        SqliteActionRunRepository(tmp_path / "action-runs.sqlite3"),
        connectors=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "action-decisions.jsonl"),
    )
    submitted = service.submit(
        _plan(tmp_path), actor="operator:alice", rationale="Submit reversible action."
    )
    approved = service.approve(
        submitted.run_id,
        actor="reviewer:bob",
        roles=["risk-reviewer"],
        rationale="Approve compensation-bound action.",
    )

    result = service.execute(approved.run_id, actor="executor:worker-1")

    assert result.status == "compensated"
    assert result.result["compensation"]["outcome"] == "succeeded"
    assert connector.compensations == 1


class UnknownOutcomeConnector:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request):
        self.calls += 1
        raise TimeoutError("connection lost after request dispatch")

    def compensate(self, request):
        raise AssertionError("unknown effects cannot be blindly compensated")


def test_unknown_connector_outcome_requires_reconciliation_without_retry(tmp_path) -> None:
    connector = UnknownOutcomeConnector()
    registry = ActionConnectorRegistry()
    registry.register("crm-core", connector)
    service = ActionRunService(
        SqliteActionRunRepository(tmp_path / "action-runs.sqlite3"),
        connectors=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "action-decisions.jsonl"),
    )
    submitted = service.submit(
        _plan(tmp_path), actor="operator:alice", rationale="Submit uncertain action."
    )
    approved = service.approve(
        submitted.run_id,
        actor="reviewer:bob",
        roles=["risk-reviewer"],
        rationale="Approve the bounded action.",
    )

    blocked = service.execute(approved.run_id, actor="executor:worker-1")
    replay = service.execute(approved.run_id, actor="executor:worker-2")

    assert blocked.status == "reconciliation_required"
    assert blocked.result["outcome"] == "unknown"
    assert replay.run_digest == blocked.run_digest
    assert connector.calls == 1


class CrashingWorkerConnector:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request):
        self.calls += 1
        raise SystemExit("worker terminated after dispatch")

    def compensate(self, request):
        raise AssertionError("a restarted worker cannot assume effects")


def test_restart_converts_interrupted_execution_to_reconciliation(tmp_path) -> None:
    connector = CrashingWorkerConnector()
    registry = ActionConnectorRegistry()
    registry.register("crm-core", connector)
    path = tmp_path / "action-runs.sqlite3"
    decisions = tmp_path / "action-decisions.jsonl"
    live_repository = SqliteActionRunRepository(path)
    service = ActionRunService(
        live_repository,
        connectors=registry,
        decision_store=DecisionProvenanceStore(decisions),
    )
    submitted = service.submit(
        _plan(tmp_path), actor="operator:alice", rationale="Submit restart drill."
    )
    approved = service.approve(
        submitted.run_id,
        actor="reviewer:bob",
        roles=["risk-reviewer"],
        rationale="Approve restart drill.",
    )
    with pytest.raises(SystemExit):
        service.execute(approved.run_id, actor="executor:crashing-worker")

    restarted = ActionRunService(
        SqliteActionRunRepository(path),
        connectors=registry,
        decision_store=DecisionProvenanceStore(decisions),
    )
    recovered = restarted.execute(approved.run_id, actor="executor:recovery-worker")

    assert recovered.status == "reconciliation_required"
    assert recovered.result["reason"] == "interrupted_execution"
    assert connector.calls == 1
    backup = live_repository.backup_to(tmp_path / "backup" / "action-runs.sqlite3")
    restored = SqliteActionRunRepository(backup)
    assert restored.schema_version() == 1
    assert restored.get(recovered.run_id).run_digest == recovered.run_digest
    assert restored.verify_all()["valid"] is True
