# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Transactional, approval-gated ActionRun execution and reconciliation."""

from __future__ import annotations
from bridge.persistence.sqlite_support import managed_sqlite_connection

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from bridge.decision_provenance import (
    DecisionProvenanceStore,
    DecisionRecord,
    EvidenceRef,
)

from .action_plans import ActionPlan
from .canonical import canonical_data, canonical_json, content_digest


class ActionRunError(ValueError):
    """Raised when an ActionRun transition would make side effects unsafe."""


class ActionConnector(Protocol):
    def invoke(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def compensate(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    # Optional (W02.05): query the receipt of a previously attempted execution
    # WITHOUT re-invoking it. Used to settle interrupted runs. Connectors that
    # cannot query receipts simply omit this method; the service then moves the
    # run to reconciliation_required instead of blindly retrying.
    # def query_receipt(self, request: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


class ActionConnectorRegistry:
    """Deployment-time connector registry; credentials remain inside providers."""

    def __init__(self) -> None:
        self._connectors: dict[str, ActionConnector] = {}

    def register(self, name: str, connector: ActionConnector) -> None:
        normalized = _text(name, "connector name")
        if normalized in self._connectors:
            raise ActionRunError(f"action connector already registered: {normalized}")
        if not callable(getattr(connector, "invoke", None)) or not callable(
            getattr(connector, "compensate", None)
        ):
            raise ActionRunError("action connector must implement invoke and compensate")
        self._connectors[normalized] = connector

    def get(self, name: str) -> ActionConnector:
        connector = self._connectors.get(name)
        if connector is None:
            raise ActionRunError(f"action connector is not registered: {name}")
        return connector


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionRunError(f"{field} must be a non-empty string")
    return value.strip()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(
    *,
    action: str,
    status: str,
    actor: str,
    rationale: str,
    decision_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "action": _text(action, "event action"),
        "status": _text(status, "event status"),
        "actor": _text(actor, "event actor"),
        "rationale": _text(rationale, "event rationale"),
        "decision_id": _text(decision_id, "event decision_id"),
        "recorded_at": _now(),
        "metadata": canonical_data(metadata or {}),
    }
    return {**payload, "event_digest": content_digest(payload)}


@dataclass(frozen=True)
class ActionRun:
    run_id: str
    tenant_id: str
    idempotency_identity: str
    plan: Mapping[str, Any]
    requester: str
    status: str
    approval: Mapping[str, Any] | None
    result: Mapping[str, Any]
    history: tuple[Mapping[str, Any], ...]
    run_digest: str

    @classmethod
    def create(
        cls,
        plan: ActionPlan,
        *,
        requester: str,
        rationale: str,
        decision_id: str,
    ) -> "ActionRun":
        if not plan.verify():
            raise ActionRunError("action plan digest mismatch")
        identity_payload = {
            "tenant_id": plan.tenant_id,
            "action_type_id": plan.action_type_id,
            "idempotency_scope": plan.idempotency_scope,
            "idempotency_key": plan.idempotency_key,
            "object_ids": list(plan.object_ids) if plan.idempotency_scope == "object" else [],
        }
        identity = content_digest(identity_payload)
        run_id = "action-" + identity.split(":", 1)[1][:32]
        status = "awaiting_approval" if plan.required_approval_roles else "approved"
        event = _event(
            action="submit",
            status=status,
            actor=requester,
            rationale=rationale,
            decision_id=decision_id,
            metadata={"plan_digest": plan.plan_digest},
        )
        return cls._build(
            run_id=run_id,
            tenant_id=plan.tenant_id,
            idempotency_identity=identity,
            plan=plan.to_dict(),
            requester=_text(requester, "requester"),
            status=status,
            approval=None,
            result={},
            history=(event,),
        )

    @classmethod
    def _build(
        cls,
        *,
        run_id: str,
        tenant_id: str,
        idempotency_identity: str,
        plan: Mapping[str, Any],
        requester: str,
        status: str,
        approval: Mapping[str, Any] | None,
        result: Mapping[str, Any],
        history: Sequence[Mapping[str, Any]],
    ) -> "ActionRun":
        payload = {
            "api_version": "aof.action-run/v1",
            "run_id": run_id,
            "tenant_id": tenant_id,
            "idempotency_identity": idempotency_identity,
            "plan": canonical_data(plan),
            "requester": requester,
            "status": status,
            "approval": canonical_data(approval) if approval is not None else None,
            "result": canonical_data(result),
            "history": canonical_data(history),
        }
        return cls(
            run_id=run_id,
            tenant_id=tenant_id,
            idempotency_identity=idempotency_identity,
            plan=_freeze(payload["plan"]),
            requester=requester,
            status=status,
            approval=_freeze(payload["approval"]) if approval is not None else None,
            result=_freeze(payload["result"]),
            history=tuple(_freeze(item) for item in payload["history"]),
            run_digest=content_digest(payload),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionRun":
        plan = value.get("plan")
        if not isinstance(plan, Mapping):
            raise ActionRunError("action run plan must be a semantic object")
        plan_payload = {key: inner for key, inner in plan.items() if key != "plan_digest"}
        if plan.get("plan_digest") != content_digest(plan_payload):
            raise ActionRunError("persisted action plan digest mismatch")
        history = value.get("history", ())
        if not isinstance(history, list | tuple):
            raise ActionRunError("action run history must be a list")
        for item in history:
            payload = {key: inner for key, inner in item.items() if key != "event_digest"}
            if item.get("event_digest") != content_digest(payload):
                raise ActionRunError("action run event digest mismatch")
        run = cls._build(
            run_id=_text(value.get("run_id"), "run_id"),
            tenant_id=_text(value.get("tenant_id"), "tenant_id"),
            idempotency_identity=_text(
                value.get("idempotency_identity"), "idempotency_identity"
            ),
            plan=plan,
            requester=_text(value.get("requester"), "requester"),
            status=_text(value.get("status"), "status"),
            approval=value.get("approval"),
            result=value.get("result", {}),
            history=history,
        )
        if value.get("run_digest") != run.run_digest:
            raise ActionRunError("action run digest mismatch")
        return run

    def transition(
        self,
        *,
        status: str,
        action: str,
        actor: str,
        rationale: str,
        decision_id: str,
        approval: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ActionRun":
        event = _event(
            action=action,
            status=status,
            actor=actor,
            rationale=rationale,
            decision_id=decision_id,
            metadata=metadata,
        )
        return self._build(
            run_id=self.run_id,
            tenant_id=self.tenant_id,
            idempotency_identity=self.idempotency_identity,
            plan=self.plan,
            requester=self.requester,
            status=status,
            approval=approval if approval is not None else self.approval,
            result=result if result is not None else self.result,
            history=(*self.history, event),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.action-run/v1",
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "idempotency_identity": self.idempotency_identity,
            "plan": canonical_data(self.plan),
            "requester": self.requester,
            "status": self.status,
            "approval": canonical_data(self.approval) if self.approval is not None else None,
            "result": canonical_data(self.result),
            "history": canonical_data(self.history),
            "run_digest": self.run_digest,
        }


class SqliteActionRunRepository:
    """Transactional ActionRun state with unique idempotency identities."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS action_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    idempotency_identity TEXT NOT NULL UNIQUE,
                    run_digest TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute("PRAGMA user_version=1")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)
    def _connection(self):
        """Transaction + close context manager (W09.01: no leaked connections)."""
        return managed_sqlite_connection(self._connect)


    def get(self, run_id: str) -> ActionRun | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT run_digest, payload FROM action_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        run = ActionRun.from_dict(json.loads(row[1]))
        if run.run_digest != row[0]:
            raise ActionRunError("indexed action run digest mismatch")
        return run

    def get_by_identity(self, identity: str) -> ActionRun | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT run_id FROM action_runs WHERE idempotency_identity = ?",
                (identity,),
            ).fetchone()
        return self.get(str(row[0])) if row is not None else None

    def put_new(self, run: ActionRun) -> ActionRun:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = self._put_new_on(connection, run)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _put_new_on(self, connection: sqlite3.Connection, run: ActionRun) -> ActionRun:
        """put_new on a caller-owned connection (W02.04 UnitOfWork support)."""
        row = connection.execute(
            "SELECT payload FROM action_runs WHERE idempotency_identity = ?",
            (run.idempotency_identity,),
        ).fetchone()
        if row is not None:
            current = ActionRun.from_dict(json.loads(row[0]))
            if current.plan.get("plan_digest") != run.plan.get("plan_digest"):
                raise ActionRunError("idempotency key is already bound to another plan")
            return current
        connection.execute(
            "INSERT INTO action_runs "
            "(run_id, tenant_id, idempotency_identity, run_digest, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                run.run_id,
                run.tenant_id,
                run.idempotency_identity,
                run.run_digest,
                canonical_json(run.to_dict()),
            ),
        )
        return run

    def update(self, run: ActionRun, *, expected_digest: str) -> ActionRun:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = self._update_on(connection, run, expected_digest=expected_digest)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _update_on(
        self, connection: sqlite3.Connection, run: ActionRun, *, expected_digest: str
    ) -> ActionRun:
        """update on a caller-owned connection (W02.04 UnitOfWork support)."""
        changed = connection.execute(
            "UPDATE action_runs SET run_digest = ?, payload = ? "
            "WHERE run_id = ? AND run_digest = ?",
            (
                run.run_digest,
                canonical_json(run.to_dict()),
                run.run_id,
                expected_digest,
            ),
        ).rowcount
        if changed != 1:
            raise ActionRunError("action run transition lost an optimistic concurrency race")
        return run

    def verify_all(self) -> dict[str, Any]:
        errors = []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT run_id, tenant_id, idempotency_identity, run_digest, payload "
                "FROM action_runs"
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"sqlite integrity check failed: {integrity}")
        for run_id, tenant_id, identity, digest, payload in rows:
            try:
                run = ActionRun.from_dict(json.loads(payload))
                if (
                    run.run_id != run_id
                    or run.tenant_id != tenant_id
                    or run.idempotency_identity != identity
                    or run.run_digest != digest
                ):
                    raise ActionRunError("indexed ActionRun identity does not match payload")
            except Exception as exc:
                errors.append(f"run/{run_id}: {exc}")
        return {"valid": not errors, "action_run_count": len(rows), "errors": errors}

    def schema_version(self) -> int:
        with self._connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

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


class ActionRunService:
    """Deep state machine around approval and at-most-once connector invocation."""

    _TERMINAL = {"succeeded", "failed", "compensated", "reconciliation_required"}

    def __init__(
        self,
        repository: SqliteActionRunRepository,
        *,
        connectors: ActionConnectorRegistry,
        decision_store: DecisionProvenanceStore,
        unit_of_work=None,
    ) -> None:
        self.repository = repository
        self.connectors = connectors
        self.decision_store = decision_store
        # W02.04: when provided, decision+state writes share one transaction
        self.unit_of_work = unit_of_work

    def submit(self, plan: ActionPlan, *, actor: str, rationale: str) -> ActionRun:
        identity = ActionRun.create(
            plan,
            requester=actor,
            rationale=rationale,
            decision_id="decision:pending",
        ).idempotency_identity
        existing = self.repository.get_by_identity(identity)
        if existing is not None:
            if existing.plan.get("plan_digest") != plan.plan_digest:
                raise ActionRunError("idempotency key is already bound to another plan")
            return existing
        if self.unit_of_work is not None:
            # W02.04: decision audit + state insert in ONE transaction
            with self.unit_of_work.atomic() as session:
                decision = self._decision(
                    actor=actor,
                    decision_type="action_submitted",
                    conclusion="awaiting_approval" if plan.required_approval_roles else "approved",
                    rationale=rationale,
                    plan=plan.to_dict(),
                    session=session,
                )
                run = ActionRun.create(
                    plan,
                    requester=actor,
                    rationale=rationale,
                    decision_id=decision,
                )
                return session.put_action_run(run)
        decision = self._decision(
            actor=actor,
            decision_type="action_submitted",
            conclusion="awaiting_approval" if plan.required_approval_roles else "approved",
            rationale=rationale,
            plan=plan.to_dict(),
        )
        run = ActionRun.create(
            plan,
            requester=actor,
            rationale=rationale,
            decision_id=decision,
        )
        return self.repository.put_new(run)

    def approve(
        self,
        run_id: str,
        *,
        actor: str,
        roles: Sequence[str],
        rationale: str,
    ) -> ActionRun:
        run = self._run(run_id)
        if run.status == "approved":
            return run
        if run.status != "awaiting_approval":
            raise ActionRunError(f"action run cannot be approved from status: {run.status}")
        if actor == run.requester:
            raise ActionRunError("action approval violates separation of duties")
        required = set(run.plan.get("required_approval_roles", ()))
        if required and not required.intersection(roles):
            raise ActionRunError("actor does not hold a required approval role")
        if self.unit_of_work is not None:
            # W02.04: approval audit + state transition in ONE transaction
            with self.unit_of_work.atomic() as session:
                decision = self._decision(
                    actor=actor,
                    decision_type="action_approved",
                    conclusion="approved",
                    rationale=rationale,
                    plan=run.plan,
                    session=session,
                )
                approval = {
                    "actor": actor,
                    "roles": sorted(set(roles)),
                    "decision_id": decision,
                    "rationale": rationale,
                }
                updated = run.transition(
                    status="approved",
                    action="approve",
                    actor=actor,
                    rationale=rationale,
                    decision_id=decision,
                    approval=approval,
                )
                return session.update_action_run(updated, expected_digest=run.run_digest)
        decision = self._decision(
            actor=actor,
            decision_type="action_approved",
            conclusion="approved",
            rationale=rationale,
            plan=run.plan,
        )
        approval = {
            "actor": actor,
            "roles": sorted(set(roles)),
            "decision_id": decision,
            "rationale": rationale,
        }
        updated = run.transition(
            status="approved",
            action="approve",
            actor=actor,
            rationale=rationale,
            decision_id=decision,
            approval=approval,
        )
        return self.repository.update(updated, expected_digest=run.run_digest)

    def execute(self, run_id: str, *, actor: str) -> ActionRun:
        run = self._run(run_id)
        if run.status in self._TERMINAL:
            return run
        if run.status == "executing":
            return self._reconcile_interrupted(run, actor=actor)
        if run.status != "approved":
            raise ActionRunError(f"action run cannot execute from status: {run.status}")
        attempt_decision = self._decision(
            actor=actor,
            decision_type="action_execution_started",
            conclusion="executing",
            rationale="Invoke the exact approved ActionPlan once.",
            plan=run.plan,
        )
        executing = run.transition(
            status="executing",
            action="execute",
            actor=actor,
            rationale="Invoke the exact approved ActionPlan once.",
            decision_id=attempt_decision,
        )
        executing = self.repository.update(executing, expected_digest=run.run_digest)
        connector = self.connectors.get(str(run.plan["connector"]))
        request = {
            "execution_id": run.run_id,
            "idempotency_key": run.plan["idempotency_key"],
            "operation": run.plan["operation"],
            "object_ids": canonical_data(run.plan["object_ids"]),
            "inputs": canonical_data(run.plan["inputs"]),
        }
        try:
            raw_result = connector.invoke(request)
            result = self._result(raw_result)
        except Exception as exc:
            result = {
                "outcome": "unknown",
                "effect_applied": None,
                "error": {"type": type(exc).__name__, "message": str(exc)},
                "receipt": {},
            }
        return self._complete_execution(
            run,
            executing,
            actor=actor,
            connector=connector,
            result=result,
        )

    def _reconcile_interrupted(self, run: ActionRun, *, actor: str) -> ActionRun:
        """W02.05: settle an interrupted execution by querying the connector
        receipt BEFORE deciding. Never blindly re-invoke: a duplicate business
        write is worse than a reconciliation task.

        - receipt with a definitive outcome -> settle without re-invoking
        - receipt unknown/missing/query failed/unsupported -> reconciliation_required
        """
        connector = self.connectors.get(str(run.plan["connector"]))
        request = {
            "execution_id": run.run_id,
            "idempotency_key": run.plan["idempotency_key"],
            "operation": run.plan["operation"],
            "object_ids": canonical_data(run.plan["object_ids"]),
            "inputs": canonical_data(run.plan["inputs"]),
        }
        receipt: dict[str, Any] | None = None
        if callable(getattr(connector, "query_receipt", None)):
            try:
                raw = connector.query_receipt(request)
                receipt = self._result(raw) if raw is not None else None
            except Exception:
                receipt = None  # query failure means the outcome stays unknown
        if receipt is not None and receipt["outcome"] in {"succeeded", "failed"}:
            return self._complete_execution(
                run,
                run,
                actor=actor,
                connector=connector,
                result=receipt,
                decision_type="action_reconciled_from_receipt",
                rationale="Settled from connector receipt without re-invocation.",
            )
        decision = self._decision(
            actor=actor,
            decision_type="action_reconciliation_required",
            conclusion="reconciliation_required",
            rationale="Execution outcome is unknown after an interrupted worker.",
            plan=run.plan,
        )
        blocked = run.transition(
            status="reconciliation_required",
            action="reconcile",
            actor=actor,
            rationale="Execution outcome is unknown after an interrupted worker.",
            decision_id=decision,
            result={"outcome": "unknown", "reason": "interrupted_execution"},
        )
        return self.repository.update(blocked, expected_digest=run.run_digest)

    def _complete_execution(
        self,
        run: ActionRun,
        executing: ActionRun,
        *,
        actor: str,
        connector: ActionConnector,
        result: dict[str, Any],
        decision_type: str = "action_execution_completed",
        rationale: str | None = None,
    ) -> ActionRun:
        """Shared settle tail for live and receipt-reconciled executions."""
        status = self._status(result)
        if (
            status == "failed"
            and result.get("effect_applied") is True
            and run.plan.get("compensation_operation")
        ):
            compensation = self._result(
                connector.compensate(
                    {
                        "execution_id": run.run_id,
                        "idempotency_key": run.plan["idempotency_key"],
                        "operation": run.plan["compensation_operation"],
                        "receipt": canonical_data(result.get("receipt", {})),
                    }
                )
            )
            result = {**result, "compensation": compensation}
            status = (
                "compensated"
                if compensation["outcome"] == "succeeded"
                else "reconciliation_required"
            )
        rationale = rationale or f"Connector returned {result['outcome']}."
        if self.unit_of_work is not None:
            with self.unit_of_work.atomic() as session:
                decision = self._decision(
                    actor=actor,
                    decision_type=decision_type,
                    conclusion=status,
                    rationale=rationale,
                    plan=run.plan,
                    session=session,
                )
                completed = executing.transition(
                    status=status,
                    action="complete",
                    actor=actor,
                    rationale=rationale,
                    decision_id=decision,
                    result=result,
                )
                return session.update_action_run(
                    completed, expected_digest=executing.run_digest
                )
        decision = self._decision(
            actor=actor,
            decision_type=decision_type,
            conclusion=status,
            rationale=rationale,
            plan=run.plan,
        )
        completed = executing.transition(
            status=status,
            action="complete",
            actor=actor,
            rationale=rationale,
            decision_id=decision,
            result=result,
        )
        return self.repository.update(completed, expected_digest=executing.run_digest)

    def compensate(self, run_id: str, *, actor: str, rationale: str) -> ActionRun:
        run = self._run(run_id)
        if run.status == "compensated":
            return run
        if run.status != "succeeded":
            raise ActionRunError(
                f"action run cannot be compensated from status: {run.status}"
            )
        operation = run.plan.get("compensation_operation")
        if not operation:
            raise ActionRunError("action run has no compensation operation")
        connector = self.connectors.get(str(run.plan["connector"]))
        try:
            compensation = self._result(
                connector.compensate(
                    {
                        "execution_id": run.run_id,
                        "idempotency_key": run.plan["idempotency_key"],
                        "operation": operation,
                        "receipt": canonical_data(run.result.get("receipt", {})),
                    }
                )
            )
        except Exception as exc:
            compensation = {
                "outcome": "unknown",
                "effect_applied": None,
                "receipt": {},
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        status = (
            "compensated"
            if compensation["outcome"] == "succeeded"
            else "reconciliation_required"
        )
        decision = self._decision(
            actor=actor,
            decision_type="action_compensation_completed",
            conclusion=status,
            rationale=rationale,
            plan=run.plan,
        )
        updated = run.transition(
            status=status,
            action="compensate",
            actor=actor,
            rationale=rationale,
            decision_id=decision,
            result={**canonical_data(run.result), "compensation": compensation},
        )
        return self.repository.update(updated, expected_digest=run.run_digest)

    @staticmethod
    def _result(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ActionRunError("connector result must be a semantic object")
        outcome = value.get("outcome")
        if outcome not in {"succeeded", "failed", "unknown"}:
            raise ActionRunError("connector outcome must be succeeded, failed, or unknown")
        effect = value.get("effect_applied")
        if effect not in {True, False, None}:
            raise ActionRunError("connector effect_applied must be boolean or null")
        return canonical_data(
            {
                "outcome": outcome,
                "effect_applied": effect,
                "receipt": value.get("receipt", {}),
                "error": value.get("error"),
            }
        )

    @staticmethod
    def _status(result: Mapping[str, Any]) -> str:
        if result["outcome"] == "succeeded":
            return "succeeded"
        if result["outcome"] == "failed" and result.get("effect_applied") is False:
            return "failed"
        return "reconciliation_required" if result["outcome"] == "unknown" else "failed"

    def _run(self, run_id: str) -> ActionRun:
        run = self.repository.get(run_id)
        if run is None:
            raise ActionRunError(f"action run not found: {run_id}")
        return run

    def _decision(
        self,
        *,
        actor: str,
        decision_type: str,
        conclusion: str,
        rationale: str,
        plan: Mapping[str, Any],
        session=None,
    ) -> str:
        evidence = [
            {
                "id": str(plan["plan_digest"]),
                "type": "action_plan",
                "content_hash": str(plan["plan_digest"]),
            }
        ]
        output_entities = [
            {
                "id": str(plan["action_type_id"]),
                "type": "action_type",
            }
        ]
        policies = [f"{plan['policy_resource_id']}@{plan['policy_revision']}"]
        tenant_id = str(plan["tenant_id"])
        metadata = {
            "release_id": plan["release_id"],
            "release_digest": plan["release_digest"],
            "compilation_run_id": plan["run_id"],
        }
        if session is not None:
            # W02.04: persist via the UnitOfWork connection (same transaction
            # as the accompanying state change)
            record = DecisionRecord(
                id=f"decision:{uuid.uuid4()}",
                recorded_at=datetime.now(timezone.utc).isoformat(),
                agent_id=actor,
                decision_type=decision_type,
                conclusion=conclusion,
                rationale=rationale,
                tenant_id=tenant_id,
                evidence=[EvidenceRef(**item) for item in evidence],
                output_entities=output_entities,
                policies=sorted(set(policies)),
                metadata=metadata,
            )
            return session.record_decision(record)
        return self.decision_store.record(
            agent_id=actor,
            decision_type=decision_type,
            conclusion=conclusion,
            rationale=rationale,
            evidence=evidence,
            output_entities=output_entities,
            policies=policies,
            tenant_id=tenant_id,
            metadata=metadata,
        )["decision"]["id"]
