"""Release-pinned WorkflowRun executes governed ActionRuns in DAG order."""

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionConnectorRegistry,
    ActionRequest,
    ActionRunService,
    GovernedActionPlanner,
    SqliteActionRunRepository,
    SqliteWorkflowRunRepository,
    WorkflowPlan,
    WorkflowRunService,
)
from tests.test_action_planning import _action_runtime


class RecordingConnector:
    def __init__(self) -> None:
        self.calls = []

    def invoke(self, request):
        self.calls.append(request["idempotency_key"])
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"request_id": f"receipt-{len(self.calls)}"},
        }

    def compensate(self, request):
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"request_id": "compensated"},
        }


def test_two_node_workflow_checkpoints_approval_and_restart(tmp_path) -> None:
    compiled, action, policy = _action_runtime(tmp_path)
    planner = GovernedActionPlanner(compiled)

    def plan(key):
        return planner.plan(
            ActionRequest.create(
                channel="production",
                action_type_id=action.resource_id,
                object_ids=["customer:alice"],
                inputs={"reason": key},
                purpose="fraud-response",
                idempotency_key=key,
                policy_resource_id=policy.resource_id,
            ),
            tenant_id="acme",
            roles=["risk-operator"],
        )

    workflow_plan = WorkflowPlan.create(
        workflow_id="aof://acme/crm/workflow/customer-risk-response",
        workflow_revision="sha256:workflow-v1",
        idempotency_key="workflow-alice-001",
        nodes={"suspend": plan("suspend-001"), "confirm": plan("confirm-001")},
        dependencies={"suspend": [], "confirm": ["suspend"]},
    )
    connector = RecordingConnector()
    connectors = ActionConnectorRegistry()
    connectors.register("crm-core", connector)
    decisions = DecisionProvenanceStore(tmp_path / "runtime-decisions.jsonl")
    actions = ActionRunService(
        SqliteActionRunRepository(tmp_path / "actions.sqlite3"),
        connectors=connectors,
        decision_store=decisions,
    )
    path = tmp_path / "workflows.sqlite3"
    service = WorkflowRunService(
        SqliteWorkflowRunRepository(path),
        actions=actions,
        decision_store=decisions,
    )

    started = service.start(
        workflow_plan,
        actor="operator:alice",
        rationale="Execute governed risk response.",
    )
    duplicate = service.start(
        workflow_plan,
        actor="operator:alice",
        rationale="Execute governed risk response.",
    )
    approved_first = service.approve_node(
        started.run_id,
        node_id="suspend",
        actor="reviewer:bob",
        roles=["risk-reviewer"],
        rationale="Approve suspension.",
    )
    checkpoint = service.advance(approved_first.run_id, actor="worker:workflow")
    approved_second = service.approve_node(
        checkpoint.run_id,
        node_id="confirm",
        actor="reviewer:bob",
        roles=["risk-reviewer"],
        rationale="Approve confirmation.",
    )
    completed = service.advance(approved_second.run_id, actor="worker:workflow")
    restarted = SqliteWorkflowRunRepository(path).get(completed.run_id)

    assert started.status == "awaiting_approval"
    assert duplicate.run_id == started.run_id
    assert checkpoint.status == "awaiting_approval"
    assert checkpoint.nodes["suspend"]["status"] == "succeeded"
    assert checkpoint.nodes["confirm"]["status"] == "awaiting_approval"
    assert completed.status == "succeeded"
    assert connector.calls == ["suspend-001", "confirm-001"]
    assert restarted.run_digest == completed.run_digest
    assert SqliteWorkflowRunRepository(path).verify_all()["valid"] is True
    assert decisions.verify_integrity()["valid"] is True


class SecondNodeFailsConnector(RecordingConnector):
    def __init__(self) -> None:
        super().__init__()
        self.compensations = []

    def invoke(self, request):
        self.calls.append(request["idempotency_key"])
        if request["idempotency_key"] == "confirm-fails":
            return {
                "outcome": "failed",
                "effect_applied": False,
                "receipt": {"request_id": "rejected"},
            }
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"request_id": "suspended"},
        }

    def compensate(self, request):
        self.compensations.append(request["idempotency_key"])
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"request_id": "resumed"},
        }


def test_downstream_failure_compensates_succeeded_nodes_in_reverse_order(
    tmp_path,
) -> None:
    compiled, action, policy = _action_runtime(tmp_path)
    planner = GovernedActionPlanner(compiled)

    def plan(key):
        return planner.plan(
            ActionRequest.create(
                channel="production",
                action_type_id=action.resource_id,
                object_ids=["customer:alice"],
                inputs={"reason": key},
                purpose="fraud-response",
                idempotency_key=key,
                policy_resource_id=policy.resource_id,
            ),
            tenant_id="acme",
            roles=["risk-operator"],
        )

    workflow = WorkflowPlan.create(
        workflow_id="aof://acme/crm/workflow/customer-risk-response",
        workflow_revision="sha256:workflow-v1",
        idempotency_key="workflow-compensation-001",
        nodes={
            "suspend": plan("suspend-succeeds"),
            "confirm": plan("confirm-fails"),
        },
        dependencies={"suspend": [], "confirm": ["suspend"]},
    )
    connector = SecondNodeFailsConnector()
    connectors = ActionConnectorRegistry()
    connectors.register("crm-core", connector)
    decisions = DecisionProvenanceStore(tmp_path / "runtime-decisions.jsonl")
    service = WorkflowRunService(
        SqliteWorkflowRunRepository(tmp_path / "workflows.sqlite3"),
        actions=ActionRunService(
            SqliteActionRunRepository(tmp_path / "actions.sqlite3"),
            connectors=connectors,
            decision_store=decisions,
        ),
        decision_store=decisions,
    )

    run = service.start(
        workflow, actor="operator:alice", rationale="Run compensation scenario."
    )
    run = service.approve_node(
        run.run_id,
        node_id="suspend",
        actor="reviewer:bob",
        roles=["risk-reviewer"],
        rationale="Approve first node.",
    )
    run = service.advance(run.run_id, actor="worker:workflow")
    run = service.approve_node(
        run.run_id,
        node_id="confirm",
        actor="reviewer:bob",
        roles=["risk-reviewer"],
        rationale="Approve second node.",
    )
    completed = service.advance(run.run_id, actor="worker:workflow")

    assert completed.status == "compensated"
    assert completed.nodes["suspend"]["status"] == "compensated"
    assert completed.nodes["confirm"]["status"] == "failed"
    assert connector.compensations == ["suspend-succeeds"]


class UnknownWorkflowConnector(RecordingConnector):
    def invoke(self, request):
        self.calls.append(request["idempotency_key"])
        raise TimeoutError("outcome unknown after dispatch")


def test_unknown_node_outcome_blocks_workflow_without_retry(tmp_path) -> None:
    compiled, action, policy = _action_runtime(tmp_path)
    action_plan = GovernedActionPlanner(compiled).plan(
        ActionRequest.create(
            channel="production",
            action_type_id=action.resource_id,
            object_ids=["customer:alice"],
            inputs={"reason": "unknown-outcome"},
            purpose="fraud-response",
            idempotency_key="unknown-node-001",
            policy_resource_id=policy.resource_id,
        ),
        tenant_id="acme",
        roles=["risk-operator"],
    )
    workflow = WorkflowPlan.create(
        workflow_id="aof://acme/crm/workflow/customer-risk-response",
        workflow_revision="sha256:workflow-v1",
        idempotency_key="workflow-unknown-001",
        nodes={"suspend": action_plan},
        dependencies={"suspend": []},
    )
    connector = UnknownWorkflowConnector()
    connectors = ActionConnectorRegistry()
    connectors.register("crm-core", connector)
    decisions = DecisionProvenanceStore(tmp_path / "runtime-decisions.jsonl")
    path = tmp_path / "workflows.sqlite3"
    service = WorkflowRunService(
        SqliteWorkflowRunRepository(path),
        actions=ActionRunService(
            SqliteActionRunRepository(tmp_path / "actions.sqlite3"),
            connectors=connectors,
            decision_store=decisions,
        ),
        decision_store=decisions,
    )
    run = service.start(
        workflow, actor="operator:alice", rationale="Run unknown outcome scenario."
    )
    run = service.approve_node(
        run.run_id,
        node_id="suspend",
        actor="reviewer:bob",
        roles=["risk-reviewer"],
        rationale="Approve bounded action.",
    )

    blocked = service.advance(run.run_id, actor="worker:workflow")
    replay = service.advance(run.run_id, actor="worker:recovery")

    assert blocked.status == "reconciliation_required"
    assert replay.run_digest == blocked.run_digest
    assert connector.calls == ["unknown-node-001"]
    assert SqliteWorkflowRunRepository(path).get(blocked.run_id).status == (
        "reconciliation_required"
    )
