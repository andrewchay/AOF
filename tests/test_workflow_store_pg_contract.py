"""W08.04 — Workflow-run store: same contract on SQLite AND PostgreSQL.

Uses the actual WorkflowRunService flow from test_workflow_runs.py
to test both backends end to end.
"""

from __future__ import annotations

import socket

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionConnectorRegistry,
    ActionRunService,
    SqliteActionRunRepository,
    SqliteWorkflowRunRepository,
    WorkflowRunService,
)
from tests.test_action_planning import _action_runtime


def _pg_up() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 5433), timeout=1)
        s.close()
        return True
    except OSError:
        return False


@pytest.fixture(params=["sqlite", "postgres"])
def workflow_repo(request, tmp_path):
    if request.param == "sqlite":
        yield SqliteWorkflowRunRepository(tmp_path / "wf.sqlite")
    elif _pg_up():
        from bridge.persistence.postgres_workflow_repository import (
            PostgresWorkflowRunRepository,
        )

        repo = PostgresWorkflowRunRepository()
        with repo._connect() as connection:
            connection.execute("TRUNCATE workflow_runs")
            connection.commit()
        yield repo
    else:
        pytest.skip("PostgreSQL infra stack not running")


def test_workflow_run_end_to_end_both_backends(workflow_repo, tmp_path):
    """A workflow run submitted through WorkflowRunService must persist
    identically on both SQLite and PostgreSQL backends."""
    connector = type("RecordingConnector", (), {
        "calls": [],
        "invoke": lambda self, request: {
            "outcome": "succeeded", "effect_applied": True,
            "receipt": {"request_id": f"r-{len(self.calls)}"},
        },
        "compensate": lambda self, request: {
            "outcome": "succeeded", "effect_applied": True,
            "receipt": {"request_id": "compensated"},
        },
    })()
    connectors = ActionConnectorRegistry()
    connectors.register("crm-core", connector)

    # build the workflow plan (same as test_workflow_runs.py)
    compiled, action, policy = _action_runtime(tmp_path)
    planner = __import__("bridge.semantic_core", fromlist=["GovernedActionPlanner"]).GovernedActionPlanner(compiled)
    from bridge.semantic_core import ActionRequest

    def make_plan(key):
        return planner.plan(
            ActionRequest.create(
                channel="production", action_type_id=action.resource_id,
                object_ids=["customer:alice"], inputs={"reason": key},
                purpose="fraud-response", idempotency_key=key,
                policy_resource_id=policy.resource_id,
            ),
            tenant_id="acme", roles=["risk-operator"],
        )

    from bridge.semantic_core.workflows import WorkflowPlan

    workflow_plan = WorkflowPlan.create(
        workflow_id="test-wf",
        workflow_revision="sha256:workflow-v1",
        idempotency_key="wf-test-key-001",
        nodes={"suspend": make_plan("wf-suspend-001"), "confirm": make_plan("wf-confirm-001")},
        dependencies={"suspend": [], "confirm": ["suspend"]},
    )

    service = WorkflowRunService(
        workflow_repo, actions=ActionRunService(
            SqliteActionRunRepository(tmp_path / "actions.sqlite"),
            connectors=connectors,
            decision_store=DecisionProvenanceStore(tmp_path / "dp.jsonl"),
        ),
        decision_store=DecisionProvenanceStore(tmp_path / "dp.jsonl"),
    )

    started = service.start(workflow_plan, actor="operator:alice", rationale="test")
    duplicate = service.start(workflow_plan, actor="operator:alice", rationale="test")
    assert started.run_id == duplicate.run_id, "idempotent start"

    fetched = workflow_repo.get(started.run_id)
    assert fetched is not None
    assert fetched.run_id == started.run_id
