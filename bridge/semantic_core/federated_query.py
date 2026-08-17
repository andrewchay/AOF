"""Deterministic multi-engine query DAGs over one trusted release snapshot."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .canonical import canonical_data, content_digest
from .query_execution import QueryExecutor, QueryResult
from .query_plans import (
    QueryCapability,
    QueryPlan,
    QueryRequest,
    TrustedQueryError,
    TrustedSnapshotResolver,
)


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrustedQueryError(f"{field} must be a non-empty string")
    return value.strip()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class FederatedQueryStep:
    step_id: str
    capability: QueryCapability
    query: str
    parameters: Mapping[str, Any]
    depends_on: tuple[str, ...]
    step_digest: str

    @classmethod
    def create(
        cls,
        *,
        step_id: str,
        capability: QueryCapability | str,
        query: str,
        parameters: Mapping[str, Any] | None = None,
        depends_on: Iterable[str] = (),
    ) -> "FederatedQueryStep":
        try:
            normalized_capability = (
                capability
                if isinstance(capability, QueryCapability)
                else QueryCapability(capability)
            )
        except ValueError as exc:
            raise TrustedQueryError(
                f"unsupported query capability: {capability}"
            ) from exc
        normalized_id = _non_empty(step_id, "step_id")
        dependencies = tuple(
            sorted({_non_empty(item, "depends_on") for item in depends_on})
        )
        if normalized_id in dependencies:
            raise TrustedQueryError("a federated query step cannot depend on itself")
        payload = {
            "api_version": "aof.federated-query-step/v1",
            "step_id": normalized_id,
            "capability": normalized_capability.value,
            "query": _non_empty(query, "query"),
            "parameters": canonical_data(dict(parameters or {})),
            "depends_on": list(dependencies),
        }
        return cls(
            step_id=normalized_id,
            capability=normalized_capability,
            query=payload["query"],
            parameters=_freeze(payload["parameters"]),
            depends_on=dependencies,
            step_digest=content_digest(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.federated-query-step/v1",
            "step_id": self.step_id,
            "capability": self.capability.value,
            "query": self.query,
            "parameters": canonical_data(self.parameters),
            "depends_on": list(self.depends_on),
            "step_digest": self.step_digest,
        }


@dataclass(frozen=True)
class FederatedQueryRequest:
    channel: str
    purpose: str
    steps: tuple[FederatedQueryStep, ...]
    request_digest: str

    @classmethod
    def create(
        cls,
        *,
        channel: str,
        purpose: str,
        steps: Iterable[FederatedQueryStep],
    ) -> "FederatedQueryRequest":
        values = tuple(steps)
        if not values:
            raise TrustedQueryError("federated query requires at least one step")
        if not all(isinstance(item, FederatedQueryStep) for item in values):
            raise TrustedQueryError("steps must contain FederatedQueryStep values")
        by_id = {item.step_id: item for item in values}
        if len(by_id) != len(values):
            raise TrustedQueryError("federated query step IDs must be unique")
        ordered = tuple(by_id[step_id] for step_id in sorted(by_id))
        payload = {
            "api_version": "aof.federated-query-request/v1",
            "channel": _non_empty(channel, "channel"),
            "purpose": _non_empty(purpose, "purpose"),
            "steps": [item.to_dict() for item in ordered],
        }
        return cls(
            channel=payload["channel"],
            purpose=payload["purpose"],
            steps=ordered,
            request_digest=content_digest(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.federated-query-request/v1",
            "channel": self.channel,
            "purpose": self.purpose,
            "steps": [item.to_dict() for item in self.steps],
            "request_digest": self.request_digest,
        }


@dataclass(frozen=True)
class FederatedPlanStep:
    step_id: str
    depends_on: tuple[str, ...]
    query_plan: QueryPlan

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "depends_on": list(self.depends_on),
            "query_plan": self.query_plan.to_dict(),
        }


@dataclass(frozen=True)
class FederatedQueryPlan:
    tenant_id: str
    channel: str
    request_digest: str
    run_id: str
    run_digest: str
    release_id: str
    release_digest: str
    steps: tuple[FederatedPlanStep, ...]
    plan_digest: str

    def _payload(self) -> dict[str, Any]:
        return {
            "api_version": "aof.federated-query-plan/v1",
            "tenant_id": self.tenant_id,
            "channel": self.channel,
            "request_digest": self.request_digest,
            "run_id": self.run_id,
            "run_digest": self.run_digest,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "steps": [item.to_dict() for item in self.steps],
        }

    def verify(self) -> bool:
        return self.plan_digest == content_digest(self._payload()) and all(
            item.query_plan.verify() for item in self.steps
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "plan_digest": self.plan_digest}


class FederatedQueryPlanner:
    def __init__(self, resolver: TrustedSnapshotResolver) -> None:
        self.resolver = resolver

    def plan(
        self, request: FederatedQueryRequest, *, tenant_id: str
    ) -> FederatedQueryPlan:
        ordered = self._topological_steps(request.steps)
        planned = []
        snapshot: tuple[str, str, str, str] | None = None
        for item in ordered:
            query_plan = self.resolver.plan(
                QueryRequest.create(
                    channel=request.channel,
                    capability=item.capability,
                    query=item.query,
                    purpose=request.purpose,
                    parameters=item.parameters,
                ),
                tenant_id=tenant_id,
            )
            current = (
                query_plan.run_id,
                query_plan.run_digest,
                query_plan.release_id,
                query_plan.release_digest,
            )
            if snapshot is not None and current != snapshot:
                raise TrustedQueryError(
                    "federated query steps resolved to different runtime snapshots"
                )
            snapshot = current
            planned.append(FederatedPlanStep(item.step_id, item.depends_on, query_plan))
        assert snapshot is not None
        payload = {
            "api_version": "aof.federated-query-plan/v1",
            "tenant_id": tenant_id,
            "channel": request.channel,
            "request_digest": request.request_digest,
            "run_id": snapshot[0],
            "run_digest": snapshot[1],
            "release_id": snapshot[2],
            "release_digest": snapshot[3],
            "steps": [item.to_dict() for item in planned],
        }
        return FederatedQueryPlan(
            tenant_id=tenant_id,
            channel=request.channel,
            request_digest=request.request_digest,
            run_id=snapshot[0],
            run_digest=snapshot[1],
            release_id=snapshot[2],
            release_digest=snapshot[3],
            steps=tuple(planned),
            plan_digest=content_digest(payload),
        )

    @staticmethod
    def _topological_steps(
        steps: tuple[FederatedQueryStep, ...],
    ) -> tuple[FederatedQueryStep, ...]:
        by_id = {item.step_id: item for item in steps}
        unknown = sorted(
            {
                dependency
                for item in steps
                for dependency in item.depends_on
                if dependency not in by_id
            }
        )
        if unknown:
            raise TrustedQueryError(
                f"federated query has unknown dependencies: {', '.join(unknown)}"
            )
        remaining = {item.step_id: set(item.depends_on) for item in steps}
        ordered = []
        while remaining:
            ready = sorted(step_id for step_id, deps in remaining.items() if not deps)
            if not ready:
                raise TrustedQueryError("federated query dependency cycle detected")
            for step_id in ready:
                ordered.append(by_id[step_id])
                del remaining[step_id]
            for deps in remaining.values():
                deps.difference_update(ready)
        return tuple(ordered)


@dataclass(frozen=True)
class FederatedQueryResult:
    status: str
    plan_digest: str
    run_digest: str
    release_digest: str
    step_results: Mapping[str, QueryResult]
    evidence: tuple[Mapping[str, Any], ...]
    result_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.federated-query-result/v1",
            "status": self.status,
            "plan_digest": self.plan_digest,
            "run_digest": self.run_digest,
            "release_digest": self.release_digest,
            "step_results": {
                step_id: self.step_results[step_id].to_dict()
                for step_id in sorted(self.step_results)
            },
            "evidence": [canonical_data(item) for item in self.evidence],
            "result_digest": self.result_digest,
        }


class FederatedQueryExecutor:
    def __init__(self, executor: QueryExecutor) -> None:
        self.executor = executor

    def execute(self, plan: FederatedQueryPlan) -> FederatedQueryResult:
        if not plan.verify():
            raise TrustedQueryError("federated query plan digest mismatch")
        results = {
            item.step_id: self.executor.execute(item.query_plan) for item in plan.steps
        }
        evidence_by_id = {
            str(item["evidence_id"]): canonical_data(item)
            for result in results.values()
            for item in result.evidence
        }
        evidence = tuple(
            _freeze(evidence_by_id[evidence_id])
            for evidence_id in sorted(
                evidence_by_id,
                key=lambda value: (
                    str(evidence_by_id[value].get("target", "")),
                    value,
                ),
            )
        )
        payload = {
            "api_version": "aof.federated-query-result/v1",
            "status": "succeeded",
            "plan_digest": plan.plan_digest,
            "run_digest": plan.run_digest,
            "release_digest": plan.release_digest,
            "step_results": {
                step_id: results[step_id].to_dict() for step_id in sorted(results)
            },
            "evidence": [canonical_data(item) for item in evidence],
        }
        return FederatedQueryResult(
            status="succeeded",
            plan_digest=plan.plan_digest,
            run_digest=plan.run_digest,
            release_digest=plan.release_digest,
            step_results=MappingProxyType(results),
            evidence=evidence,
            result_digest=content_digest(payload),
        )
