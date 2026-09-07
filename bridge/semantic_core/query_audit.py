# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Decision provenance and self-verifying evidence packages for trusted queries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore

from .canonical import canonical_data, content_digest
from .query_plans import QueryPlan, QueryRequest, TrustedSnapshotResolver
from .query_policy import (
    GovernedQueryExecutor,
    GovernedQueryResult,
    QueryPolicyError,
    QueryPolicyWaiver,
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class QueryEvidencePackage:
    """Portable evidence binding a query result to policy and its causal audit trail."""

    request_digest: str
    plan_digest: str
    policy_report_digest: str
    governed_result_digest: str
    execution_decision_id: str
    artifact_evidence: tuple[Mapping[str, Any], ...]
    field_evidence: tuple[Mapping[str, Any], ...]
    audit_trail: Mapping[str, Any]
    package_digest: str

    def _payload(self) -> dict[str, Any]:
        return {
            "api_version": "aof.query-evidence-package/v1",
            "request_digest": self.request_digest,
            "plan_digest": self.plan_digest,
            "policy_report_digest": self.policy_report_digest,
            "governed_result_digest": self.governed_result_digest,
            "execution_decision_id": self.execution_decision_id,
            "artifact_evidence": [canonical_data(item) for item in self.artifact_evidence],
            "field_evidence": [canonical_data(item) for item in self.field_evidence],
            "audit_trail": canonical_data(self.audit_trail),
        }

    def verify(self) -> bool:
        integrity = self.audit_trail.get("integrity", {})
        return (
            self.package_digest == content_digest(self._payload())
            and self.audit_trail.get("@id") == self.execution_decision_id
            and isinstance(integrity, Mapping)
            and integrity.get("valid") is True
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "package_digest": self.package_digest}


@dataclass(frozen=True)
class AuditedQueryResult:
    governed_result: GovernedQueryResult
    plan_decision_id: str
    policy_decision_id: str
    execution_decision_id: str
    waiver_decision_ids: tuple[str, ...]
    evidence_package: QueryEvidencePackage

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.audited-query-result/v1",
            "governed_result": self.governed_result.to_dict(),
            "decisions": {
                "plan": self.plan_decision_id,
                "policy": self.policy_decision_id,
                "waivers": list(self.waiver_decision_ids),
                "execution": self.execution_decision_id,
            },
            "evidence_package": self.evidence_package.to_dict(),
        }


class AuditedQueryService:
    """Plan, authorize, execute, and audit a query as one governed operation."""

    def __init__(
        self,
        *,
        resolver: TrustedSnapshotResolver,
        governed_executor: GovernedQueryExecutor,
        decision_store: DecisionProvenanceStore,
    ) -> None:
        self.resolver = resolver
        self.governed_executor = governed_executor
        self.decision_store = decision_store

    def execute(
        self,
        request: QueryRequest,
        *,
        tenant_id: str,
        actor: str,
        roles: Iterable[str],
        rationale: str,
        waivers: Iterable[QueryPolicyWaiver] = (),
        session_id: str | None = None,
    ) -> AuditedQueryResult:
        plan = self.resolver.plan(request, tenant_id=tenant_id)
        plan_decision = self._record_plan(plan, actor, rationale, session_id)
        waiver_values = tuple(waivers)
        role_values = tuple(roles)
        report = self.governed_executor.policy.evaluate(
            plan, roles=role_values, waivers=waiver_values
        )
        waiver_decisions = self._record_applied_waivers(
            plan,
            report.to_dict(),
            waiver_values,
            actor,
            plan_decision,
            session_id,
        )
        policy_decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_query_policy",
            conclusion="allowed" if report.conforms else "denied",
            rationale=rationale,
            evidence=[
                {
                    "id": report.report_digest,
                    "type": "query_policy_report",
                    "content_hash": report.report_digest,
                    "metadata": {
                        "policy_resource_id": report.policy_resource_id,
                        "policy_revision": report.policy_revision,
                    },
                }
            ],
            parent_decision_ids=[plan_decision, *waiver_decisions],
            output_entities=[
                {
                    "id": report.report_digest,
                    "type": "query_policy_report",
                    "conforms": report.conforms,
                }
            ],
            tags=[plan.capability.value, plan.purpose, "query-policy"],
            policies=[report.policy_revision],
            tenant_id=tenant_id,
            session_id=session_id,
            metadata={
                "plan_digest": plan.plan_digest,
                "policy_report_digest": report.report_digest,
                "finding_codes": [item.code for item in report.findings],
            },
        )["decision"]["id"]
        if not report.conforms:
            codes = ", ".join(item.code for item in report.findings if not item.resolved)
            raise QueryPolicyError(f"query policy rejected the plan: {codes}")

        governed = self.governed_executor.execute(
            plan, roles=role_values, waivers=waiver_values
        )
        execution_decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_query_execute",
            conclusion="succeeded",
            rationale=rationale,
            evidence=[
                {
                    "id": item["evidence_id"],
                    "type": item["type"],
                    "content_hash": item["content_hash"],
                    "metadata": canonical_data(item),
                }
                for item in governed.result.evidence
            ],
            parent_decision_ids=[policy_decision],
            output_entities=[
                {
                    "id": governed.governed_result_digest,
                    "type": "governed_query_result",
                    "content_hash": governed.governed_result_digest,
                }
            ],
            tags=[plan.capability.value, plan.purpose, plan.channel],
            policies=[report.policy_revision, plan.compiler_policy_revision],
            tenant_id=tenant_id,
            session_id=session_id,
            metadata={
                "request_digest": plan.request_digest,
                "plan_digest": plan.plan_digest,
                "run_id": plan.run_id,
                "run_digest": plan.run_digest,
                "release_id": plan.release_id,
                "release_digest": plan.release_digest,
                "policy_report_digest": report.report_digest,
                "result_digest": governed.result.result_digest,
                "governed_result_digest": governed.governed_result_digest,
            },
        )["decision"]["id"]
        audit_trail = self.decision_store.audit_trail(execution_decision)
        package_payload = {
            "api_version": "aof.query-evidence-package/v1",
            "request_digest": plan.request_digest,
            "plan_digest": plan.plan_digest,
            "policy_report_digest": report.report_digest,
            "governed_result_digest": governed.governed_result_digest,
            "execution_decision_id": execution_decision,
            "artifact_evidence": [canonical_data(item) for item in governed.result.evidence],
            "field_evidence": [canonical_data(item) for item in report.field_evidence],
            "audit_trail": canonical_data(audit_trail),
        }
        evidence_package = QueryEvidencePackage(
            request_digest=plan.request_digest,
            plan_digest=plan.plan_digest,
            policy_report_digest=report.report_digest,
            governed_result_digest=governed.governed_result_digest,
            execution_decision_id=execution_decision,
            artifact_evidence=tuple(_freeze(item) for item in governed.result.evidence),
            field_evidence=tuple(_freeze(item) for item in report.field_evidence),
            audit_trail=_freeze(audit_trail),
            package_digest=content_digest(package_payload),
        )
        return AuditedQueryResult(
            governed_result=governed,
            plan_decision_id=plan_decision,
            policy_decision_id=policy_decision,
            execution_decision_id=execution_decision,
            waiver_decision_ids=tuple(waiver_decisions),
            evidence_package=evidence_package,
        )

    def _record_plan(
        self,
        plan: QueryPlan,
        actor: str,
        rationale: str,
        session_id: str | None,
    ) -> str:
        run = self.resolver.repository.get(plan.run_id)
        run_decision_id = getattr(run, "decision_id", None)
        parents = (
            [run_decision_id]
            if isinstance(run_decision_id, str)
            and self.decision_store.get(run_decision_id) is not None
            else []
        )
        evidence = [
            {
                "id": plan.request_digest,
                "type": "query_request",
                "content_hash": plan.request_digest,
            },
            *[
                {
                    "id": f"artifact:{item.target}:{item.content_hash}",
                    "type": "compiled_artifact",
                    "uri": item.uri,
                    "content_hash": item.content_hash,
                    "metadata": {"target": item.target, "compiler": item.compiler},
                }
                for item in plan.artifacts
            ],
        ]
        return self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_query_plan",
            conclusion="snapshot-pinned",
            rationale=rationale,
            evidence=evidence,
            parent_decision_ids=parents,
            output_entities=[
                {"id": plan.plan_digest, "type": "query_plan", "content_hash": plan.plan_digest}
            ],
            tags=[plan.capability.value, plan.purpose, plan.channel],
            policies=[plan.compiler_policy_revision],
            tenant_id=plan.tenant_id,
            session_id=session_id,
            metadata={
                "request_digest": plan.request_digest,
                "plan_digest": plan.plan_digest,
                "channel_pointer_digest": plan.channel_pointer_digest,
                "run_id": plan.run_id,
                "run_digest": plan.run_digest,
                "release_id": plan.release_id,
                "release_digest": plan.release_digest,
            },
        )["decision"]["id"]

    def _record_applied_waivers(
        self,
        plan: QueryPlan,
        report: Mapping[str, Any],
        waivers: tuple[QueryPolicyWaiver, ...],
        actor: str,
        plan_decision_id: str,
        session_id: str | None,
    ) -> list[str]:
        applied = {
            item["waiver_id"]
            for item in report["findings"]
            if item.get("resolved") and item.get("waiver_id")
        }
        decisions = []
        for waiver in sorted(waivers, key=lambda item: item.waiver_id):
            if waiver.waiver_id not in applied:
                continue
            decision = self.decision_store.record(
                agent_id=waiver.actor,
                decision_type="semantic_query_policy_waiver",
                conclusion="approved",
                rationale=waiver.rationale,
                evidence=[
                    {
                        "id": waiver.finding_id,
                        "type": "query_policy_finding",
                        "content_hash": waiver.finding_id,
                    }
                ],
                parent_decision_ids=[plan_decision_id],
                output_entities=[
                    {
                        "id": waiver.waiver_id,
                        "type": "query_policy_waiver",
                        "authority": waiver.authority,
                    }
                ],
                tags=[plan.capability.value, "query-policy-waiver"],
                policies=[waiver.policy_revision],
                tenant_id=plan.tenant_id,
                session_id=session_id,
                metadata={
                    "waiver_id": waiver.waiver_id,
                    "finding_id": waiver.finding_id,
                    "authority": waiver.authority,
                    "requested_by": actor,
                },
            )
            decisions.append(decision["decision"]["id"])
        return decisions
