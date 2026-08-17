"""Revision-addressed policy gates for trusted semantic queries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from .canonical import canonical_data, content_digest
from .models import ResourceKind, SemanticResource
from .query_execution import QueryExecutionScope, QueryExecutor, QueryResult
from .query_plans import QueryCapability, QueryPlan


class QueryPolicyError(ValueError):
    """Raised when query policy content or enforcement is invalid."""


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QueryPolicyError(f"{field} must be a non-empty string")
    return value.strip()


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise QueryPolicyError(f"{field} must be a list of non-empty strings")
    return tuple(sorted({_non_empty(item, field) for item in value}))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class QueryPolicyWaiver:
    finding_id: str
    policy_revision: str
    actor: str
    rationale: str
    authority: str
    waiver_id: str

    @classmethod
    def create(
        cls,
        *,
        finding_id: str,
        policy_revision: str,
        actor: str,
        rationale: str,
        authority: str,
    ) -> "QueryPolicyWaiver":
        payload = {
            "api_version": "aof.query-policy-waiver/v1",
            "finding_id": _non_empty(finding_id, "finding_id"),
            "policy_revision": _non_empty(policy_revision, "policy_revision"),
            "actor": _non_empty(actor, "actor"),
            "rationale": _non_empty(rationale, "rationale"),
            "authority": _non_empty(authority, "authority"),
        }
        return cls(
            finding_id=payload["finding_id"],
            policy_revision=payload["policy_revision"],
            actor=payload["actor"],
            rationale=payload["rationale"],
            authority=payload["authority"],
            waiver_id=content_digest(payload),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "api_version": "aof.query-policy-waiver/v1",
            "finding_id": self.finding_id,
            "policy_revision": self.policy_revision,
            "actor": self.actor,
            "rationale": self.rationale,
            "authority": self.authority,
            "waiver_id": self.waiver_id,
        }


@dataclass(frozen=True)
class QueryPolicyFinding:
    finding_id: str
    code: str
    message: str
    subject: str
    waiver_allowed: bool
    resolved: bool = False
    waiver_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "code": self.code,
            "message": self.message,
            "subject": self.subject,
            "waiver_allowed": self.waiver_allowed,
            "resolved": self.resolved,
            "waiver_id": self.waiver_id,
        }


@dataclass(frozen=True)
class QueryPolicyReport:
    policy_resource_id: str
    policy_revision: str
    plan_digest: str
    roles: tuple[str, ...]
    conforms: bool
    findings: tuple[QueryPolicyFinding, ...]
    field_evidence: tuple[Mapping[str, Any], ...]
    resource_scope: tuple[str, ...] | None
    field_scope: tuple[str, ...] | None
    report_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.query-policy-report/v1",
            "policy_resource_id": self.policy_resource_id,
            "policy_revision": self.policy_revision,
            "plan_digest": self.plan_digest,
            "roles": list(self.roles),
            "conforms": self.conforms,
            "findings": [item.to_dict() for item in self.findings],
            "field_evidence": [canonical_data(item) for item in self.field_evidence],
            "resource_scope": (
                list(self.resource_scope) if self.resource_scope is not None else None
            ),
            "field_scope": list(self.field_scope) if self.field_scope is not None else None,
            "report_digest": self.report_digest,
        }


@dataclass(frozen=True)
class QueryPolicy:
    resource_id: str
    revision_id: str
    role_capabilities: Mapping[str, tuple[QueryCapability, ...]]
    capability_rules: Mapping[QueryCapability, Mapping[str, Any]]
    waiver_allowed_codes: frozenset[str]

    @classmethod
    def from_resource(cls, resource: SemanticResource) -> "QueryPolicy":
        if resource.kind is not ResourceKind.POLICY:
            raise QueryPolicyError("query policy must be a Policy semantic resource")
        spec = resource.spec
        if spec.get("policy_type") != "query":
            raise QueryPolicyError("query policy spec.policy_type must be 'query'")
        supported = {
            "policy_type",
            "role_capabilities",
            "capability_rules",
            "waiver_allowed_codes",
        }
        unknown = sorted(set(spec) - supported)
        if unknown:
            raise QueryPolicyError(f"unknown query policy fields: {', '.join(unknown)}")
        raw_roles = spec.get("role_capabilities", {})
        if not isinstance(raw_roles, Mapping) or not raw_roles:
            raise QueryPolicyError("role_capabilities must be a non-empty mapping")
        roles: dict[str, tuple[QueryCapability, ...]] = {}
        for raw_role, values in raw_roles.items():
            role = _non_empty(raw_role, "role_capabilities role")
            try:
                roles[role] = tuple(
                    QueryCapability(value)
                    for value in _strings(values, f"role_capabilities.{role}")
                )
            except ValueError as exc:
                raise QueryPolicyError(f"unsupported query capability for role: {role}") from exc
        raw_rules = spec.get("capability_rules", {})
        if not isinstance(raw_rules, Mapping):
            raise QueryPolicyError("capability_rules must be a mapping")
        rules: dict[QueryCapability, Mapping[str, Any]] = {}
        rule_fields = {
            "allowed_purposes",
            "max_limit",
            "allowed_resource_ids",
            "denied_resource_ids",
            "allowed_fields",
        }
        for raw_capability, raw_rule in raw_rules.items():
            try:
                capability = QueryCapability(raw_capability)
            except ValueError as exc:
                raise QueryPolicyError(
                    f"unsupported query capability rule: {raw_capability}"
                ) from exc
            if not isinstance(raw_rule, Mapping):
                raise QueryPolicyError(f"capability_rules.{raw_capability} must be an object")
            unknown_rule = sorted(set(raw_rule) - rule_fields)
            if unknown_rule:
                raise QueryPolicyError(
                    f"unknown fields for capability_rules.{raw_capability}: {', '.join(unknown_rule)}"
                )
            rule = {
                "allowed_purposes": _strings(
                    raw_rule.get("allowed_purposes"), "allowed_purposes"
                ),
                "allowed_resource_ids": _strings(
                    raw_rule.get("allowed_resource_ids"), "allowed_resource_ids"
                ),
                "denied_resource_ids": _strings(
                    raw_rule.get("denied_resource_ids"), "denied_resource_ids"
                ),
                "allowed_fields": _strings(raw_rule.get("allowed_fields"), "allowed_fields"),
            }
            max_limit = raw_rule.get("max_limit")
            if max_limit is not None and (
                isinstance(max_limit, bool) or not isinstance(max_limit, int) or max_limit < 1
            ):
                raise QueryPolicyError("max_limit must be a positive integer")
            rule["max_limit"] = max_limit
            rules[capability] = _freeze(rule)
        return cls(
            resource_id=resource.resource_id,
            revision_id=resource.revision_id,
            role_capabilities=MappingProxyType(roles),
            capability_rules=MappingProxyType(rules),
            waiver_allowed_codes=frozenset(
                _strings(spec.get("waiver_allowed_codes"), "waiver_allowed_codes")
            ),
        )

    def evaluate(
        self,
        plan: QueryPlan,
        *,
        roles: Iterable[str],
        waivers: Iterable[QueryPolicyWaiver] = (),
    ) -> QueryPolicyReport:
        normalized_roles = tuple(sorted({_non_empty(role, "roles") for role in roles}))
        raw_findings: list[tuple[str, str, str]] = []
        if not plan.verify():
            raw_findings.append(
                ("query_plan_invalid", plan.plan_digest, "query plan digest is invalid")
            )
        authorized = any(
            plan.capability in self.role_capabilities.get(role, ()) for role in normalized_roles
        )
        if not authorized:
            raw_findings.append(
                (
                    "query_role_not_allowed",
                    plan.capability.value,
                    f"roles are not allowed to use capability: {plan.capability.value}",
                )
            )
        rule = self.capability_rules.get(plan.capability, MappingProxyType({}))
        allowed_purposes = set(rule.get("allowed_purposes", ()))
        if allowed_purposes and plan.purpose not in allowed_purposes:
            raw_findings.append(
                (
                    "query_purpose_not_allowed",
                    plan.purpose,
                    f"query purpose is not allowed: {plan.purpose}",
                )
            )
        max_limit = rule.get("max_limit")
        raw_limit = plan.parameters.get("limit")
        if raw_limit is not None and (
            isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit < 1
        ):
            raw_findings.append(
                ("query_limit_invalid", str(raw_limit), "query limit must be a positive integer")
            )
        elif max_limit is not None and raw_limit is not None and raw_limit > max_limit:
            raw_findings.append(
                (
                    "query_limit_exceeded",
                    str(raw_limit),
                    f"query limit {raw_limit} exceeds policy maximum {max_limit}",
                )
            )
        explicitly_requested = self._requested_values(plan, "resource_ids", "resource_id")
        if plan.query.startswith("aof://"):
            explicitly_requested.add(plan.query)
        resource_ids = set(explicitly_requested)
        if plan.capability is not QueryCapability.SEMANTIC_SEARCH:
            resource_ids.update(plan.resolved_resource_ids)
        allowed_resources = set(rule.get("allowed_resource_ids", ()))
        denied_resources = set(rule.get("denied_resource_ids", ()))
        for resource_id in sorted(resource_ids):
            if resource_id in denied_resources:
                raw_findings.append(
                    (
                        "query_resource_denied",
                        resource_id,
                        f"query resource is explicitly denied: {resource_id}",
                    )
                )
            elif allowed_resources and resource_id not in allowed_resources:
                raw_findings.append(
                    (
                        "query_resource_not_allowed",
                        resource_id,
                        f"query resource is not allowlisted: {resource_id}",
                    )
                )
        requested_fields = self._requested_values(plan, "fields")
        allowed_fields = set(rule.get("allowed_fields", ()))
        field_evidence = []
        for field in sorted(requested_fields):
            if allowed_fields and field not in allowed_fields:
                raw_findings.append(
                    (
                        "query_field_not_allowed",
                        field,
                        f"query field is not allowlisted: {field}",
                    )
                )
            else:
                evidence = {
                    "policy_revision": self.revision_id,
                    "plan_digest": plan.plan_digest,
                    "capability": plan.capability.value,
                    "field": field,
                    "decision": "allowed",
                }
                evidence["evidence_id"] = content_digest(evidence)
                field_evidence.append(evidence)

        findings = []
        never_waivable = {"query_plan_invalid", "query_role_not_allowed", "query_limit_invalid"}
        for code, subject, message in sorted(raw_findings):
            finding_payload = {
                "policy_revision": self.revision_id,
                "plan_digest": plan.plan_digest,
                "code": code,
                "subject": subject,
                "message": message,
            }
            findings.append(
                QueryPolicyFinding(
                    finding_id=content_digest(finding_payload),
                    code=code,
                    message=message,
                    subject=subject,
                    waiver_allowed=(
                        code in self.waiver_allowed_codes and code not in never_waivable
                    ),
                )
            )
        waiver_index = {
            (item.policy_revision, item.finding_id): item
            for item in sorted(waivers, key=lambda item: item.waiver_id)
        }
        resolved = []
        for finding in findings:
            waiver = waiver_index.get((self.revision_id, finding.finding_id))
            if finding.waiver_allowed and waiver is not None:
                finding = replace(finding, resolved=True, waiver_id=waiver.waiver_id)
            resolved.append(finding)
        conforms = plan.verify() and all(item.resolved for item in resolved)
        resource_scope: tuple[str, ...] | None = None
        if plan.capability is QueryCapability.SEMANTIC_SEARCH and (
            allowed_resources or denied_resources
        ):
            visible = set(plan.resolved_resource_ids) - denied_resources
            if allowed_resources:
                visible.intersection_update(allowed_resources)
            resource_scope = tuple(sorted(visible))
        elif allowed_resources:
            resource_scope = tuple(sorted(allowed_resources))
        payload = {
            "api_version": "aof.query-policy-report/v1",
            "policy_resource_id": self.resource_id,
            "policy_revision": self.revision_id,
            "plan_digest": plan.plan_digest,
            "roles": list(normalized_roles),
            "conforms": conforms,
            "findings": [item.to_dict() for item in resolved],
            "field_evidence": field_evidence,
            "resource_scope": list(resource_scope) if resource_scope is not None else None,
            "field_scope": sorted(allowed_fields) if allowed_fields else None,
        }
        return QueryPolicyReport(
            policy_resource_id=self.resource_id,
            policy_revision=self.revision_id,
            plan_digest=plan.plan_digest,
            roles=normalized_roles,
            conforms=conforms,
            findings=tuple(resolved),
            field_evidence=tuple(_freeze(item) for item in field_evidence),
            resource_scope=resource_scope,
            field_scope=tuple(sorted(allowed_fields)) if allowed_fields else None,
            report_digest=content_digest(payload),
        )

    @staticmethod
    def _requested_values(plan: QueryPlan, plural: str, singular: str | None = None) -> set[str]:
        values = plan.parameters.get(plural, ())
        if not isinstance(values, list | tuple):
            values = ()
        result = {str(item) for item in values if str(item)}
        if singular:
            value = plan.parameters.get(singular)
            if isinstance(value, str) and value:
                result.add(value)
        return result


@dataclass(frozen=True)
class GovernedQueryResult:
    result: QueryResult
    policy_report: QueryPolicyReport
    governed_result_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.governed-query-result/v1",
            "result": self.result.to_dict(),
            "policy_report": self.policy_report.to_dict(),
            "governed_result_digest": self.governed_result_digest,
        }


class GovernedQueryExecutor:
    def __init__(self, executor: QueryExecutor, policy: QueryPolicy) -> None:
        self.executor = executor
        self.policy = policy

    def execute(
        self,
        plan: QueryPlan,
        *,
        roles: Iterable[str],
        waivers: Iterable[QueryPolicyWaiver] = (),
    ) -> GovernedQueryResult:
        report = self.policy.evaluate(plan, roles=roles, waivers=waivers)
        if not report.conforms:
            codes = ", ".join(item.code for item in report.findings if not item.resolved)
            raise QueryPolicyError(f"query policy rejected the plan: {codes}")
        result = self.executor.execute(
            plan,
            scope=QueryExecutionScope(
                resource_ids=report.resource_scope,
                fields=report.field_scope,
            ),
        )
        payload = {
            "api_version": "aof.governed-query-result/v1",
            "result": result.to_dict(),
            "policy_report": report.to_dict(),
        }
        return GovernedQueryResult(
            result=result,
            policy_report=report,
            governed_result_digest=content_digest(payload),
        )
