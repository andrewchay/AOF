# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Signed transport-neutral boundary for governed enterprise actions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore

from .action_plans import ActionRequest, GovernedActionPlanner
from .action_runs import (
    ActionConnectorRegistry,
    ActionRunError,
    ActionRunService,
    SqliteActionRunRepository,
)
from .compilers import CompilationRunRepository
from .identity import SignedPrincipalVerifier


class ActionControlPlaneError(ValueError):
    """Raised when a signed action request violates the public contract."""


class ActionControlPlane:
    """Share the exact plan/submit/approve/execute semantics across transports."""

    def __init__(
        self,
        compilation_repository: CompilationRunRepository,
        *,
        verifier: SignedPrincipalVerifier,
        action_runs: SqliteActionRunRepository,
        connectors: ActionConnectorRegistry,
        decision_store: DecisionProvenanceStore,
    ) -> None:
        self.compilation_repository = compilation_repository
        self.verifier = verifier
        self.action_runs = action_runs
        self.service = ActionRunService(
            action_runs,
            connectors=connectors,
            decision_store=decision_store,
        )

    def plan(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        return GovernedActionPlanner(self.compilation_repository).plan(
            self._request(payload),
            tenant_id=principal.tenant_id,
            roles=principal.roles,
        ).to_dict()

    def submit(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        plan = GovernedActionPlanner(self.compilation_repository).plan(
            self._request(payload),
            tenant_id=principal.tenant_id,
            roles=principal.roles,
        )
        if self._required(payload, "expected_plan_digest") != plan.plan_digest:
            raise ActionControlPlaneError(
                "expected_plan_digest does not match the current action plan"
            )
        return self.service.submit(
            plan,
            actor=f"principal:{principal.subject}",
            rationale=self._required(payload, "rationale"),
        ).to_dict()

    def approve(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        self._tenant_run(self._required(payload, 'run_id'), principal.tenant_id)
        return self.service.approve(
            self._required(payload, "run_id"),
            actor=f"principal:{principal.subject}",
            roles=principal.roles,
            rationale=self._required(payload, "rationale"),
        ).to_dict()

    def execute(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        if not {"admin", "action-executor"}.intersection(principal.roles):
            raise ActionControlPlaneError("principal lacks action-executor role")
        run = self._tenant_run(self._required(payload, "run_id"), principal.tenant_id)
        return self.service.execute(
            run.run_id, actor=f"principal:{principal.subject}"
        ).to_dict()

    def get_run(
        self, run_id: str, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        return self._tenant_run(run_id, principal.tenant_id).to_dict()

    def _tenant_run(self, run_id: str, tenant_id: str):
        run = self.action_runs.get(run_id)
        if run is None:
            raise ActionRunError(f"action run not found: {run_id}")
        if run.tenant_id != tenant_id:
            raise ActionRunError("action run tenant does not match signed principal")
        return run

    @staticmethod
    def _request(payload: Mapping[str, Any]) -> ActionRequest:
        inputs = payload.get("inputs")
        if not isinstance(inputs, Mapping):
            raise ActionControlPlaneError("inputs must be a semantic object")
        object_ids = payload.get("object_ids")
        if not isinstance(object_ids, list | tuple):
            raise ActionControlPlaneError("object_ids must be a list")
        return ActionRequest.create(
            channel=ActionControlPlane._required(payload, "channel"),
            action_type_id=ActionControlPlane._required(payload, "action_type_id"),
            object_ids=object_ids,
            inputs=inputs,
            purpose=ActionControlPlane._required(payload, "purpose"),
            idempotency_key=ActionControlPlane._required(payload, "idempotency_key"),
            policy_resource_id=ActionControlPlane._required(
                payload, "policy_resource_id"
            ),
        )

    @staticmethod
    def _required(payload: Mapping[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ActionControlPlaneError(f"{field} must be a non-empty string")
        return value.strip()
