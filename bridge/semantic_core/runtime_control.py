# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Shared signed boundary for reasoning, simulation, and workflow execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .identity import SignedPrincipalVerifier
from .incremental_reasoning import (
    ReasoningFactChange,
    SqliteIncrementalReasoningRuntime,
)
from .simulation import SimulationRequest, SqliteBitemporalSimulationService
from .workflows import WorkflowPlan, WorkflowRunService


class EnterpriseRuntimeControlPlane:
    def __init__(
        self,
        *,
        verifier: SignedPrincipalVerifier,
        reasoning: SqliteIncrementalReasoningRuntime,
        workflows: WorkflowRunService | None = None,
        simulations: SqliteBitemporalSimulationService | None = None,
    ) -> None:
        self.verifier = verifier
        self.reasoning = reasoning
        self.workflows = workflows
        self.simulations = simulations

    def apply_reasoning(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        actor = principal.actor_for("reason")
        change = payload.get("change")
        if not isinstance(change, Mapping):
            raise ValueError("change must be a semantic object")
        run = self.reasoning.apply(
            ruleset_id=self._text(payload.get("ruleset_id"), "ruleset_id"),
            program=self._text(payload.get("program"), "program"),
            change=ReasoningFactChange.create(
                change_id=self._text(change.get("change_id"), "change_id"),
                tenant_id=principal.tenant_id,
                effective_at=(
                    str(change["effective_at"])
                    if change.get("effective_at") is not None
                    else None
                ),
                assertions=self._fact_values(change.get("assertions", ())),
                retractions=self._fact_values(change.get("retractions", ())),
                source=change.get("source", {}),
            ),
            actor=actor,
        )
        return run.to_dict()

    def query_reasoning(
        self,
        *,
        ruleset_id: str,
        predicate: str | None,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        facts = self.reasoning.query(
            tenant_id=principal.tenant_id,
            ruleset_id=ruleset_id,
            predicate=predicate,
        )
        return {"facts": facts, "count": len(facts)}

    def list_reasoning_runs(
        self, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        runs = self.reasoning.list_runs(tenant_id=principal.tenant_id)
        return {"runs": [run.to_dict() for run in runs], "count": len(runs)}

    def get_reasoning_run(
        self, run_id: str, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        return self.reasoning.get_run(
            run_id, tenant_id=principal.tenant_id
        ).to_dict()

    def replay_reasoning(
        self, run_id: str, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        principal.actor_for("reason")
        return self.reasoning.replay(run_id, tenant_id=principal.tenant_id)

    def start_workflow(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        service = self._workflows()
        principal = self.verifier.verify(headers)
        actor = principal.actor_for("workflow_start")
        plan = WorkflowPlan.from_dict(payload.get("plan", payload))
        if plan.tenant_id != principal.tenant_id:
            raise ValueError("workflow plan tenant does not match signed principal tenant")
        return service.start(
            plan,
            actor=actor,
            rationale=self._text(payload.get("rationale"), "rationale"),
        ).to_dict()

    def approve_workflow_node(
        self,
        run_id: str,
        node_id: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        service = self._workflows()
        principal = self.verifier.verify(headers)
        actor = principal.actor_for("workflow_approve")
        run = service.repository.get(run_id, tenant_id=principal.tenant_id)
        if run is None:
            raise ValueError(f"workflow run not found: {run_id}")
        return service.approve_node(
            run_id,
            node_id=node_id,
            actor=actor,
            roles=principal.roles,
            rationale=self._text(payload.get("rationale"), "rationale"),
        ).to_dict()

    def advance_workflow(
        self, run_id: str, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        service = self._workflows()
        principal = self.verifier.verify(headers)
        actor = principal.actor_for("workflow_advance")
        run = service.repository.get(run_id, tenant_id=principal.tenant_id)
        if run is None:
            raise ValueError(f"workflow run not found: {run_id}")
        return service.advance(run_id, actor=actor).to_dict()

    def get_workflow_run(
        self, run_id: str, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        service = self._workflows()
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        run = service.repository.get(run_id, tenant_id=principal.tenant_id)
        if run is None:
            raise ValueError(f"workflow run not found: {run_id}")
        return run.to_dict()

    def list_workflow_runs(
        self, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        service = self._workflows()
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        runs = service.repository.list_runs(tenant_id=principal.tenant_id)
        return {"runs": [run.to_dict() for run in runs], "count": len(runs)}

    def simulate(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        service = self._simulations()
        principal = self.verifier.verify(headers)
        actor = principal.actor_for("simulate")
        request = SimulationRequest.from_dict(payload.get("request", payload))
        if request.tenant_id != principal.tenant_id:
            raise ValueError("simulation tenant does not match signed principal tenant")
        return service.simulate(request, actor=actor).to_dict()

    def get_simulation(
        self, run_id: str, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        service = self._simulations()
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        return service.get(run_id, tenant_id=principal.tenant_id).to_dict()

    def list_simulations(
        self, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        service = self._simulations()
        principal = self.verifier.verify(headers)
        principal.actor_for("read")
        runs = service.list_runs(tenant_id=principal.tenant_id)
        return {"runs": [run.to_dict() for run in runs], "count": len(runs)}

    def replay_simulation(
        self, run_id: str, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        service = self._simulations()
        principal = self.verifier.verify(headers)
        principal.actor_for("simulate")
        return service.replay(run_id, tenant_id=principal.tenant_id)

    def _workflows(self) -> WorkflowRunService:
        if self.workflows is None:
            raise ValueError("workflow runtime is not configured")
        return self.workflows

    def _simulations(self) -> SqliteBitemporalSimulationService:
        if self.simulations is None:
            raise ValueError("simulation runtime is not configured")
        return self.simulations

    @staticmethod
    def _fact_values(value: Any) -> list[tuple[str, list[str]]]:
        if not isinstance(value, list | tuple):
            raise ValueError("reasoning facts must be a list")
        result = []
        for item in value:
            if not isinstance(item, Mapping):
                raise ValueError("reasoning fact must be a semantic object")
            terms = item.get("terms")
            if not isinstance(terms, list | tuple):
                raise ValueError("reasoning fact terms must be a list")
            result.append((str(item.get("predicate", "")), [str(term) for term in terms]))
        return result

    @staticmethod
    def _text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()
