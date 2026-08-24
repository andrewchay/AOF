"""Shared signed control plane used by REST and MCP compiler boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore

from ..identity import SemanticPrincipal, SignedPrincipalVerifier
from ..models import SemanticResource
from ..releases import KnowledgeRelease
from .base import CompilePlan, CompilerRegistry
from .governance import CompilationWaiver, CompilerPolicy
from .runs import CompilationRunRepository, CompilationRunService


class CompilerControlPlaneError(ValueError):
    """Raised when a signed compiler request violates its semantic contract."""


@dataclass(frozen=True)
class _CompileContext:
    release: KnowledgeRelease
    resources: tuple[SemanticResource, ...]
    policy: CompilerPolicy
    targets: tuple[str, ...]
    waivers: tuple[CompilationWaiver, ...]


class CompilerControlPlane:
    """Zero-trust orchestration facade shared by HTTP and MCP transports."""

    def __init__(
        self,
        root: str | Path,
        *,
        verifier: SignedPrincipalVerifier,
        registry: CompilerRegistry,
        decision_store: DecisionProvenanceStore,
        repository_factory: Callable[[Path], CompilationRunRepository] | None = None,
    ) -> None:
        self.root = Path(root)
        self.verifier = verifier
        self.registry = registry
        self.decision_store = decision_store
        self.repository_factory = repository_factory or CompilationRunRepository

    def plan(self, payload: Mapping[str, Any], *, headers: Mapping[str, str]) -> dict[str, Any]:
        principal, _ = self._authorize(headers, "compile")
        context = self._context(payload, principal, require_targets=True)
        return self._plan(context).to_dict()

    def evaluate(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal, _ = self._authorize(headers, "compile")
        context = self._context(payload, principal, require_targets=True)
        plan = self._plan(context)
        return context.policy.evaluate(plan, waivers=context.waivers).to_dict()

    def execute(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal, actor = self._authorize(headers, "compile")
        context = self._context(payload, principal, require_targets=True)
        plan = self._plan(context)
        expected = self._required(payload, "expected_plan_digest")
        if plan.plan_digest != expected:
            raise CompilerControlPlaneError("expected_plan_digest does not match the current plan")
        run = self._service(principal).execute(
            run_id=self._required(payload, "run_id"),
            plan=plan,
            policy=context.policy,
            release=context.release,
            resources=context.resources,
            actor=actor,
            rationale=self._required(payload, "rationale"),
            waivers=context.waivers,
        )
        return run.to_dict()

    def replay(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal, actor = self._authorize(headers, "compile")
        context = self._context(payload, principal, require_targets=False)
        run = self._service(principal).replay(
            self._required(payload, "source_run_id"),
            run_id=self._required(payload, "run_id"),
            policy=context.policy,
            release=context.release,
            resources=context.resources,
            actor=actor,
            rationale=self._required(payload, "rationale"),
            waivers=context.waivers,
        )
        return run.to_dict()

    def approve_promotion(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal, actor = self._authorize(headers, "approve")
        return self._service(principal).approve_promotion(
            self._required(payload, "run_id"),
            channel=self._required(payload, "channel"),
            actor=actor,
            rationale=self._required(payload, "rationale"),
        )

    def promote(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal, actor = self._authorize(headers, "publish")
        return self._service(principal).promote_with_approval(
            self._required(payload, "run_id"),
            channel=self._required(payload, "channel"),
            actor=actor,
            approval_decision_id=self._required(payload, "approval_decision_id"),
            rationale=self._required(payload, "rationale"),
        )

    def rollback(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal, actor = self._authorize(headers, "publish")
        return self._service(principal).rollback(
            channel=self._required(payload, "channel"),
            to_run_id=self._required(payload, "to_run_id"),
            actor=actor,
            rationale=self._required(payload, "rationale"),
        )

    def get_run(self, run_id: str, *, headers: Mapping[str, str]) -> dict[str, Any]:
        principal, _ = self._authorize(headers, "read")
        run = self._repository(principal).get(run_id)
        if run is None:
            raise CompilerControlPlaneError(f"compilation run not found: {run_id}")
        return run.to_dict()

    def get_channel(self, channel: str, *, headers: Mapping[str, str]) -> dict[str, Any]:
        principal, _ = self._authorize(headers, "read")
        pointer = self._repository(principal).get_channel(channel)
        if pointer is None:
            raise CompilerControlPlaneError(f"compilation channel not found: {channel}")
        return pointer

    def _context(
        self,
        payload: Mapping[str, Any],
        principal: SemanticPrincipal,
        *,
        require_targets: bool,
    ) -> _CompileContext:
        release_value = payload.get("release")
        policy_value = payload.get("policy")
        resources_value = payload.get("resources")
        if not isinstance(release_value, Mapping) or not isinstance(policy_value, Mapping):
            raise CompilerControlPlaneError("release and policy must be semantic objects")
        if not isinstance(resources_value, list | tuple) or not resources_value:
            raise CompilerControlPlaneError("resources must be a non-empty list")
        release = KnowledgeRelease.from_dict(release_value)
        resources = tuple(SemanticResource.from_dict(item) for item in resources_value)
        policy_resource = SemanticResource.from_dict(policy_value)
        self._require_tenant(release, resources, policy_resource, principal.tenant_id)
        targets_value = payload.get("targets", ())
        if not isinstance(targets_value, list | tuple):
            raise CompilerControlPlaneError("targets must be a list")
        targets = tuple(str(item) for item in targets_value)
        if require_targets and not targets:
            raise CompilerControlPlaneError("at least one compiler target is required")
        waivers_value = payload.get("waivers", ())
        if not isinstance(waivers_value, list | tuple):
            raise CompilerControlPlaneError("waivers must be a list")
        return _CompileContext(
            release=release,
            resources=resources,
            policy=CompilerPolicy.from_resource(policy_resource),
            targets=targets,
            waivers=tuple(CompilationWaiver.from_dict(item) for item in waivers_value),
        )

    def _plan(self, context: _CompileContext) -> CompilePlan:
        return self.registry.plan(
            context.release, resources=context.resources, targets=context.targets
        )

    def _repository(self, principal: SemanticPrincipal) -> CompilationRunRepository:
        return self.repository_factory(self.root / principal.tenant_id)

    def _service(self, principal: SemanticPrincipal) -> CompilationRunService:
        return CompilationRunService(
            self._repository(principal),
            registry=self.registry,
            decision_store=self.decision_store,
        )

    def _authorize(
        self, headers: Mapping[str, str], action: str
    ) -> tuple[SemanticPrincipal, str]:
        principal = self.verifier.verify(headers)
        return principal, principal.actor_for(action)

    @staticmethod
    def _required(payload: Mapping[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CompilerControlPlaneError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _require_tenant(
        release: KnowledgeRelease,
        resources: tuple[SemanticResource, ...],
        policy: SemanticResource,
        tenant_id: str,
    ) -> None:
        if release.scope.get("tenant_id") != tenant_id:
            raise CompilerControlPlaneError("release tenant does not match signed principal tenant")
        resource_tenants = {item.resource_id.split("/", 3)[2] for item in resources}
        if resource_tenants != {tenant_id}:
            raise CompilerControlPlaneError("resource tenant does not match signed principal tenant")
        if policy.resource_id.split("/", 3)[2] != tenant_id:
            raise CompilerControlPlaneError("policy tenant does not match signed principal tenant")
