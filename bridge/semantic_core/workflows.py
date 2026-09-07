# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Persistent release-pinned WorkflowRun DAG orchestration."""

from __future__ import annotations
from bridge.persistence.sqlite_support import managed_sqlite_connection

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore

from .action_plans import ActionPlan
from .action_runs import ActionRunService
from .canonical import canonical_data, canonical_json, content_digest


class WorkflowRunError(ValueError):
    """Raised when a workflow plan or state transition is invalid."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowRunError(f"{field} must be a non-empty string")
    return value.strip()


def _topological_order(dependencies: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    nodes = set(dependencies)
    unknown = sorted(
        dependency
        for values in dependencies.values()
        for dependency in values
        if dependency not in nodes
    )
    if unknown:
        raise WorkflowRunError(
            f"workflow contains unknown node dependencies: {', '.join(unknown)}"
        )
    remaining = {node: set(values) for node, values in dependencies.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(node for node, values in remaining.items() if not values)
        if not ready:
            raise WorkflowRunError("workflow dependency graph contains a cycle")
        order.extend(ready)
        for node in ready:
            remaining.pop(node)
        for values in remaining.values():
            values.difference_update(ready)
    return tuple(order)


def _event(
    *, action: str, status: str, actor: str, rationale: str, decision_id: str
) -> dict[str, Any]:
    payload = {
        "action": action,
        "status": status,
        "actor": _text(actor, "actor"),
        "rationale": _text(rationale, "rationale"),
        "decision_id": _text(decision_id, "decision_id"),
    }
    return {**payload, "event_digest": content_digest(payload)}


@dataclass(frozen=True)
class WorkflowPlan:
    tenant_id: str
    workflow_id: str
    workflow_revision: str
    idempotency_key: str
    release_id: str
    release_digest: str
    compilation_run_id: str
    compilation_run_digest: str
    channel: str
    nodes: Mapping[str, Mapping[str, Any]]
    dependencies: Mapping[str, tuple[str, ...]]
    execution_order: tuple[str, ...]
    plan_digest: str

    @classmethod
    def create(
        cls,
        *,
        workflow_id: str,
        workflow_revision: str,
        idempotency_key: str,
        nodes: Mapping[str, ActionPlan],
        dependencies: Mapping[str, Sequence[str]],
    ) -> "WorkflowPlan":
        if not nodes or set(nodes) != set(dependencies):
            raise WorkflowRunError(
                "workflow nodes and dependency keys must be the same non-empty set"
            )
        normalized_nodes: dict[str, dict[str, Any]] = {}
        snapshots = set()
        for raw_node_id, plan in nodes.items():
            node_id = _text(raw_node_id, "node_id")
            if not plan.verify():
                raise WorkflowRunError(f"action plan digest mismatch: {node_id}")
            normalized_nodes[node_id] = plan.to_dict()
            snapshots.add(
                (
                    plan.tenant_id,
                    plan.release_id,
                    plan.release_digest,
                    plan.run_id,
                    plan.run_digest,
                    plan.channel,
                )
            )
        if len(snapshots) != 1:
            raise WorkflowRunError(
                "all workflow nodes must use one tenant, release, compilation run, and channel"
            )
        snapshot = next(iter(snapshots))
        normalized_dependencies = {
            _text(node, "node_id"): tuple(
                sorted({_text(item, "node dependency") for item in values})
            )
            for node, values in dependencies.items()
        }
        order = _topological_order(normalized_dependencies)
        payload = {
            "api_version": "aof.workflow-plan/v1",
            "tenant_id": snapshot[0],
            "workflow_id": _text(workflow_id, "workflow_id"),
            "workflow_revision": _text(workflow_revision, "workflow_revision"),
            "idempotency_key": _text(idempotency_key, "idempotency_key"),
            "release_id": snapshot[1],
            "release_digest": snapshot[2],
            "compilation_run_id": snapshot[3],
            "compilation_run_digest": snapshot[4],
            "channel": snapshot[5],
            "nodes": canonical_data(normalized_nodes),
            "dependencies": canonical_data(normalized_dependencies),
            "execution_order": list(order),
        }
        return cls(
            tenant_id=snapshot[0],
            workflow_id=payload["workflow_id"],
            workflow_revision=payload["workflow_revision"],
            idempotency_key=payload["idempotency_key"],
            release_id=snapshot[1],
            release_digest=snapshot[2],
            compilation_run_id=snapshot[3],
            compilation_run_digest=snapshot[4],
            channel=snapshot[5],
            nodes=payload["nodes"],
            dependencies={
                node: tuple(values)
                for node, values in payload["dependencies"].items()
            },
            execution_order=order,
            plan_digest=content_digest(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(
            {
                "api_version": "aof.workflow-plan/v1",
                "tenant_id": self.tenant_id,
                "workflow_id": self.workflow_id,
                "workflow_revision": self.workflow_revision,
                "idempotency_key": self.idempotency_key,
                "release_id": self.release_id,
                "release_digest": self.release_digest,
                "compilation_run_id": self.compilation_run_id,
                "compilation_run_digest": self.compilation_run_digest,
                "channel": self.channel,
                "nodes": self.nodes,
                "dependencies": self.dependencies,
                "execution_order": self.execution_order,
                "plan_digest": self.plan_digest,
            }
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowPlan":
        nodes = {
            node: ActionPlan.from_dict(plan)
            for node, plan in value.get("nodes", {}).items()
        }
        plan = cls.create(
            workflow_id=str(value.get("workflow_id", "")),
            workflow_revision=str(value.get("workflow_revision", "")),
            idempotency_key=str(value.get("idempotency_key", "")),
            nodes=nodes,
            dependencies=value.get("dependencies", {}),
        )
        if value.get("plan_digest") != plan.plan_digest:
            raise WorkflowRunError("workflow plan digest mismatch")
        return plan


@dataclass(frozen=True)
class WorkflowRun:
    run_id: str
    tenant_id: str
    identity: str
    plan: Mapping[str, Any]
    requester: str
    status: str
    nodes: Mapping[str, Mapping[str, Any]]
    history: tuple[Mapping[str, Any], ...]
    run_digest: str

    @classmethod
    def create(
        cls,
        plan: WorkflowPlan,
        *,
        requester: str,
        rationale: str,
        decision_id: str,
    ) -> "WorkflowRun":
        identity = content_digest(
            {
                "tenant_id": plan.tenant_id,
                "workflow_id": plan.workflow_id,
                "idempotency_key": plan.idempotency_key,
            }
        )
        nodes = {
            node: {
                "node_id": node,
                "status": "pending",
                "action_run_id": None,
                "action_run_digest": None,
            }
            for node in plan.execution_order
        }
        return cls._build(
            run_id="workflow-" + identity.split(":", 1)[1][:32],
            tenant_id=plan.tenant_id,
            identity=identity,
            plan=plan.to_dict(),
            requester=requester,
            status="running",
            nodes=nodes,
            history=(
                _event(
                    action="start",
                    status="running",
                    actor=requester,
                    rationale=rationale,
                    decision_id=decision_id,
                ),
            ),
        )

    @classmethod
    def _build(
        cls,
        *,
        run_id: str,
        tenant_id: str,
        identity: str,
        plan: Mapping[str, Any],
        requester: str,
        status: str,
        nodes: Mapping[str, Mapping[str, Any]],
        history: Sequence[Mapping[str, Any]],
    ) -> "WorkflowRun":
        payload = {
            "api_version": "aof.workflow-run/v1",
            "run_id": run_id,
            "tenant_id": tenant_id,
            "identity": identity,
            "plan": canonical_data(plan),
            "requester": requester,
            "status": status,
            "nodes": canonical_data(nodes),
            "history": canonical_data(history),
        }
        return cls(
            run_id=run_id,
            tenant_id=tenant_id,
            identity=identity,
            plan=payload["plan"],
            requester=requester,
            status=status,
            nodes=payload["nodes"],
            history=tuple(payload["history"]),
            run_digest=content_digest(payload),
        )

    def transition(
        self,
        *,
        status: str,
        nodes: Mapping[str, Mapping[str, Any]],
        action: str,
        actor: str,
        rationale: str,
        decision_id: str,
    ) -> "WorkflowRun":
        return self._build(
            run_id=self.run_id,
            tenant_id=self.tenant_id,
            identity=self.identity,
            plan=self.plan,
            requester=self.requester,
            status=status,
            nodes=nodes,
            history=(
                *self.history,
                _event(
                    action=action,
                    status=status,
                    actor=actor,
                    rationale=rationale,
                    decision_id=decision_id,
                ),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(
            {
                "api_version": "aof.workflow-run/v1",
                "run_id": self.run_id,
                "tenant_id": self.tenant_id,
                "identity": self.identity,
                "plan": self.plan,
                "requester": self.requester,
                "status": self.status,
                "nodes": self.nodes,
                "history": self.history,
                "run_digest": self.run_digest,
            }
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowRun":
        plan = WorkflowPlan.from_dict(value.get("plan", {}))
        history = value.get("history", ())
        if not isinstance(history, list | tuple):
            raise WorkflowRunError("workflow run history must be a list")
        for item in history:
            payload = {key: inner for key, inner in item.items() if key != "event_digest"}
            if item.get("event_digest") != content_digest(payload):
                raise WorkflowRunError("workflow run event digest mismatch")
        nodes = value.get("nodes", {})
        if not isinstance(nodes, Mapping) or set(nodes) != set(plan.nodes):
            raise WorkflowRunError("workflow run nodes do not match the frozen plan")
        run = cls._build(
            run_id=_text(value.get("run_id"), "run_id"),
            tenant_id=_text(value.get("tenant_id"), "tenant_id"),
            identity=_text(value.get("identity"), "identity"),
            plan=plan.to_dict(),
            requester=_text(value.get("requester"), "requester"),
            status=_text(value.get("status"), "status"),
            nodes=nodes,
            history=history,
        )
        if value.get("run_digest") != run.run_digest:
            raise WorkflowRunError("workflow run digest mismatch")
        return run


class SqliteWorkflowRunRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    identity TEXT NOT NULL UNIQUE,
                    run_digest TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                PRAGMA user_version=1;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)
    def _connection(self):
        """Transaction + close context manager (W09.01: no leaked connections)."""
        return managed_sqlite_connection(self._connect)


    def get(self, run_id: str, *, tenant_id: str | None = None) -> WorkflowRun | None:
        query = "SELECT payload FROM workflow_runs WHERE run_id=?"
        params: list[Any] = [run_id]
        if tenant_id is not None:
            query += " AND tenant_id=?"
            params.append(tenant_id)
        with self._connection() as connection:
            row = connection.execute(query, params).fetchone()
        return WorkflowRun.from_dict(json.loads(row[0])) if row else None

    def get_by_identity(self, identity: str) -> WorkflowRun | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM workflow_runs WHERE identity=?", (identity,)
            ).fetchone()
        return WorkflowRun.from_dict(json.loads(row[0])) if row else None

    def list_runs(self, *, tenant_id: str) -> list[WorkflowRun]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM workflow_runs WHERE tenant_id=? ORDER BY rowid DESC",
                (tenant_id,),
            ).fetchall()
        return [WorkflowRun.from_dict(json.loads(row[0])) for row in rows]

    def put_new(self, run: WorkflowRun) -> WorkflowRun:
        with self._connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?)",
                    (
                        run.run_id,
                        run.tenant_id,
                        run.identity,
                        run.run_digest,
                        canonical_json(run.to_dict()),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.get_by_identity(run.identity)
                if existing is None or existing.plan.get("plan_digest") != run.plan.get(
                    "plan_digest"
                ):
                    raise WorkflowRunError(
                        "workflow idempotency key is bound to another plan"
                    )
                return existing
        return run

    def update(self, run: WorkflowRun, *, expected_digest: str) -> WorkflowRun:
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE workflow_runs SET run_digest=?, payload=? "
                "WHERE run_id=? AND run_digest=?",
                (
                    run.run_digest,
                    canonical_json(run.to_dict()),
                    run.run_id,
                    expected_digest,
                ),
            ).rowcount
        if not changed:
            raise WorkflowRunError("workflow run changed concurrently")
        return run

    def verify_all(self) -> dict[str, Any]:
        errors = []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT run_id, run_digest, payload FROM workflow_runs"
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"sqlite integrity check failed: {integrity}")
        for run_id, digest, raw in rows:
            try:
                run = WorkflowRun.from_dict(json.loads(raw))
                if run.run_id != run_id or run.run_digest != digest:
                    raise WorkflowRunError("indexed workflow identity mismatch")
            except Exception as exc:
                errors.append(f"run/{run_id}: {exc}")
        return {"valid": not errors, "workflow_run_count": len(rows), "errors": errors}

    def backup_to(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self._connect()
        backup = sqlite3.connect(target)
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()
        return target


class WorkflowRunService:
    _TERMINAL = {"succeeded", "failed", "compensated", "reconciliation_required"}

    def __init__(
        self,
        repository: SqliteWorkflowRunRepository,
        *,
        actions: ActionRunService,
        decision_store: DecisionProvenanceStore,
    ) -> None:
        self.repository = repository
        self.actions = actions
        self.decisions = decision_store

    def start(
        self, plan: WorkflowPlan, *, actor: str, rationale: str
    ) -> WorkflowRun:
        identity = content_digest(
            {
                "tenant_id": plan.tenant_id,
                "workflow_id": plan.workflow_id,
                "idempotency_key": plan.idempotency_key,
            }
        )
        existing = self.repository.get_by_identity(identity)
        if existing is not None:
            if existing.plan.get("plan_digest") != plan.plan_digest:
                raise WorkflowRunError(
                    "workflow idempotency key is bound to another plan"
                )
            return existing
        decision = self._decision(
            actor=actor,
            decision_type="workflow_started",
            conclusion="running",
            rationale=rationale,
            plan=plan.to_dict(),
        )
        run = self.repository.put_new(
            WorkflowRun.create(
                plan,
                requester=actor,
                rationale=rationale,
                decision_id=decision,
            )
        )
        return self._activate(run, actor=actor)

    def approve_node(
        self,
        run_id: str,
        *,
        node_id: str,
        actor: str,
        roles: Sequence[str],
        rationale: str,
    ) -> WorkflowRun:
        run = self._run(run_id)
        node = dict(run.nodes.get(node_id, {}))
        if node.get("status") != "awaiting_approval":
            raise WorkflowRunError(
                f"workflow node cannot be approved from status: {node.get('status')}"
            )
        action = self.actions.approve(
            str(node["action_run_id"]),
            actor=actor,
            roles=roles,
            rationale=rationale,
        )
        nodes = {key: dict(value) for key, value in run.nodes.items()}
        nodes[node_id] = {
            **node,
            "status": action.status,
            "action_run_digest": action.run_digest,
        }
        decision = self._decision(
            actor=actor,
            decision_type="workflow_node_approved",
            conclusion=f"approved {node_id}",
            rationale=rationale,
            plan=run.plan,
            parents=[action.history[-1]["decision_id"]],
        )
        updated = run.transition(
            status="running",
            nodes=nodes,
            action="approve_node",
            actor=actor,
            rationale=rationale,
            decision_id=decision,
        )
        return self.repository.update(updated, expected_digest=run.run_digest)

    def advance(self, run_id: str, *, actor: str) -> WorkflowRun:
        run = self._run(run_id)
        if run.status in self._TERMINAL:
            return run
        run = self._activate(run, actor=actor)
        plan = WorkflowPlan.from_dict(run.plan)
        for node_id in plan.execution_order:
            node = dict(run.nodes[node_id])
            if node["status"] == "awaiting_approval":
                break
            if node["status"] != "approved":
                continue
            action = self.actions.execute(str(node["action_run_id"]), actor=actor)
            nodes = {key: dict(value) for key, value in run.nodes.items()}
            nodes[node_id] = {
                **node,
                "status": action.status,
                "action_run_digest": action.run_digest,
            }
            status = self._status(nodes)
            decision = self._decision(
                actor=actor,
                decision_type="workflow_node_completed",
                conclusion=f"{node_id}: {action.status}",
                rationale="Advance the exact release-pinned workflow checkpoint.",
                plan=run.plan,
                parents=[action.history[-1]["decision_id"]],
            )
            updated = run.transition(
                status=status,
                nodes=nodes,
                action="complete_node",
                actor=actor,
                rationale=f"Node {node_id} completed with {action.status}.",
                decision_id=decision,
            )
            run = self.repository.update(updated, expected_digest=run.run_digest)
            if status == "failed":
                return self._compensate(run, actor=actor)
            if status in self._TERMINAL:
                return run
            run = self._activate(run, actor=actor)
        return run

    def _compensate(self, run: WorkflowRun, *, actor: str) -> WorkflowRun:
        plan = WorkflowPlan.from_dict(run.plan)
        nodes = {key: dict(value) for key, value in run.nodes.items()}
        parents = []
        compensated = False
        reconciliation = False
        for node_id in reversed(plan.execution_order):
            node = nodes[node_id]
            if node["status"] != "succeeded":
                continue
            action = self.actions.compensate(
                str(node["action_run_id"]),
                actor=actor,
                rationale=f"Compensate workflow node {node_id} after downstream failure.",
            )
            nodes[node_id] = {
                **node,
                "status": action.status,
                "action_run_digest": action.run_digest,
            }
            parents.append(action.history[-1]["decision_id"])
            compensated = compensated or action.status == "compensated"
            reconciliation = (
                reconciliation or action.status == "reconciliation_required"
            )
        status = (
            "reconciliation_required"
            if reconciliation
            else "compensated"
            if compensated
            else "failed"
        )
        decision = self._decision(
            actor=actor,
            decision_type="workflow_compensation_completed",
            conclusion=status,
            rationale="Compensate succeeded nodes in reverse topological order.",
            plan=run.plan,
            parents=parents,
        )
        updated = run.transition(
            status=status,
            nodes=nodes,
            action="compensate",
            actor=actor,
            rationale="Compensate succeeded nodes after workflow failure.",
            decision_id=decision,
        )
        return self.repository.update(updated, expected_digest=run.run_digest)

    def _activate(self, run: WorkflowRun, *, actor: str) -> WorkflowRun:
        plan = WorkflowPlan.from_dict(run.plan)
        nodes = {key: dict(value) for key, value in run.nodes.items()}
        changed = False
        action_parents = []
        for node_id in plan.execution_order:
            node = nodes[node_id]
            if node["status"] != "pending" or not all(
                nodes[dependency]["status"] == "succeeded"
                for dependency in plan.dependencies[node_id]
            ):
                continue
            action = self.actions.submit(
                ActionPlan.from_dict(plan.nodes[node_id]),
                actor=run.requester,
                rationale=f"Activate workflow node {node_id}.",
            )
            nodes[node_id] = {
                **node,
                "status": action.status,
                "action_run_id": action.run_id,
                "action_run_digest": action.run_digest,
            }
            action_parents.append(action.history[-1]["decision_id"])
            changed = True
        if not changed:
            return run
        status = self._status(nodes)
        decision = self._decision(
            actor=actor,
            decision_type="workflow_nodes_activated",
            conclusion=status,
            rationale="Activate only nodes whose dependencies are confirmed succeeded.",
            plan=run.plan,
            parents=action_parents,
        )
        updated = run.transition(
            status=status,
            nodes=nodes,
            action="activate_nodes",
            actor=actor,
            rationale="Activate dependency-ready workflow nodes.",
            decision_id=decision,
        )
        return self.repository.update(updated, expected_digest=run.run_digest)

    @staticmethod
    def _status(nodes: Mapping[str, Mapping[str, Any]]) -> str:
        statuses = {str(node["status"]) for node in nodes.values()}
        if "reconciliation_required" in statuses:
            return "reconciliation_required"
        if "failed" in statuses:
            return "failed"
        if statuses == {"succeeded"}:
            return "succeeded"
        if "awaiting_approval" in statuses:
            return "awaiting_approval"
        return "running"

    def _run(self, run_id: str) -> WorkflowRun:
        run = self.repository.get(run_id)
        if run is None:
            raise WorkflowRunError(f"workflow run not found: {run_id}")
        return run

    def _decision(
        self,
        *,
        actor: str,
        decision_type: str,
        conclusion: str,
        rationale: str,
        plan: Mapping[str, Any],
        parents: Sequence[str] = (),
    ) -> str:
        return self.decisions.record(
            agent_id=actor,
            decision_type=decision_type,
            conclusion=conclusion,
            rationale=rationale,
            evidence=[
                {
                    "id": str(plan["plan_digest"]),
                    "type": "workflow_plan",
                    "content_hash": str(plan["plan_digest"]),
                }
            ],
            parent_decision_ids=parents,
            output_entities=[
                {"id": str(plan["workflow_id"]), "type": "workflow"}
            ],
            tenant_id=str(plan["tenant_id"]),
            policies=["policy:workflow-runtime"],
            metadata={
                "release_id": plan["release_id"],
                "release_digest": plan["release_digest"],
                "compilation_run_id": plan["compilation_run_id"],
            },
        )["decision"]["id"]
