"""Governed policy evaluation for deterministic compile plans."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from ..canonical import canonical_data, content_digest
from ..models import ResourceKind, SemanticResource
from .base import CompilePlan


class CompilerPolicyError(ValueError):
    """Raised when a compiler policy resource has an invalid contract."""


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompilerPolicyError(f"{field} must be a non-empty string")
    return value.strip()


def _string_set(value: Any, field: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CompilerPolicyError(f"{field} must be a list of non-empty strings")
    return frozenset(_non_empty(item, field) for item in value)


@dataclass(frozen=True)
class CompilationWaiver:
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
    ) -> "CompilationWaiver":
        payload = {
            "api_version": "aof.compilation-waiver/v1",
            "finding_id": _non_empty(finding_id, "finding_id"),
            "policy_revision": _non_empty(policy_revision, "policy_revision"),
            "actor": _non_empty(actor, "actor"),
            "rationale": _non_empty(rationale, "rationale"),
            "authority": _non_empty(authority, "authority"),
        }
        return cls(**{key: value for key, value in payload.items() if key != "api_version"}, waiver_id=content_digest(payload))

    def to_dict(self) -> dict[str, str]:
        return {
            "api_version": "aof.compilation-waiver/v1",
            "finding_id": self.finding_id,
            "policy_revision": self.policy_revision,
            "actor": self.actor,
            "rationale": self.rationale,
            "authority": self.authority,
            "waiver_id": self.waiver_id,
        }


@dataclass(frozen=True)
class CompilerPolicyFinding:
    finding_id: str
    code: str
    target: str
    message: str
    waiver_allowed: bool
    resolved: bool = False
    waiver_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "code": self.code,
            "target": self.target,
            "message": self.message,
            "waiver_allowed": self.waiver_allowed,
            "resolved": self.resolved,
            "waiver_id": self.waiver_id,
        }


@dataclass(frozen=True)
class CompilerPolicyReport:
    policy_resource_id: str
    policy_revision: str
    plan_digest: str
    conforms: bool
    findings: tuple[CompilerPolicyFinding, ...]
    report_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.compiler-policy-report/v1",
            "policy_resource_id": self.policy_resource_id,
            "policy_revision": self.policy_revision,
            "plan_digest": self.plan_digest,
            "conforms": self.conforms,
            "findings": [finding.to_dict() for finding in self.findings],
            "report_digest": self.report_digest,
        }


@dataclass(frozen=True)
class CompilerPolicy:
    resource_id: str
    revision_id: str
    allowed_compilers: Mapping[str, frozenset[str]]
    required_targets: frozenset[str]
    denied_targets: frozenset[str]
    waiver_allowed_codes: frozenset[str]

    @classmethod
    def from_resource(cls, resource: SemanticResource) -> "CompilerPolicy":
        if resource.kind is not ResourceKind.POLICY:
            raise CompilerPolicyError("compiler policy must be a Policy semantic resource")
        spec = resource.spec
        if spec.get("policy_type") != "compiler":
            raise CompilerPolicyError("compiler policy spec.policy_type must be 'compiler'")
        supported = {
            "policy_type",
            "allowed_compilers",
            "required_targets",
            "denied_targets",
            "waiver_allowed_codes",
        }
        unknown = sorted(set(spec) - supported)
        if unknown:
            raise CompilerPolicyError(f"unknown compiler policy fields: {', '.join(unknown)}")
        raw_allowed = spec.get("allowed_compilers", {})
        if not isinstance(raw_allowed, Mapping):
            raise CompilerPolicyError("allowed_compilers must map targets to compiler identities")
        allowed: dict[str, frozenset[str]] = {}
        for raw_target, identities in raw_allowed.items():
            target = _non_empty(raw_target, "allowed_compilers target")
            values = _string_set(identities, f"allowed_compilers.{target}")
            if any(not identity.startswith(f"{target}@") for identity in values):
                raise CompilerPolicyError(
                    f"allowed_compilers.{target} identities must start with '{target}@'"
                )
            allowed[target] = values
        required = _string_set(spec.get("required_targets"), "required_targets")
        denied = _string_set(spec.get("denied_targets"), "denied_targets")
        overlap = sorted(required & denied)
        if overlap:
            raise CompilerPolicyError(
                f"targets cannot be both required and denied: {', '.join(overlap)}"
            )
        return cls(
            resource_id=resource.resource_id,
            revision_id=resource.revision_id,
            allowed_compilers=MappingProxyType(allowed),
            required_targets=required,
            denied_targets=denied,
            waiver_allowed_codes=_string_set(
                spec.get("waiver_allowed_codes"), "waiver_allowed_codes"
            ),
        )

    def evaluate(
        self,
        plan: CompilePlan,
        *,
        waivers: Iterable[CompilationWaiver] = (),
    ) -> CompilerPolicyReport:
        raw_findings: list[tuple[str, str, str]] = []
        for diagnostic in plan.diagnostics:
            raw_findings.append(
                (
                    "compile_plan_invalid",
                    str(diagnostic.get("target", "")),
                    str(diagnostic.get("message", "compile plan is invalid")),
                )
            )
        planned_targets = set(plan.compiler_lock)
        for target in sorted(self.required_targets - planned_targets):
            raw_findings.append(
                ("required_target_missing", target, f"required compiler target is missing: {target}")
            )
        for target in sorted(planned_targets):
            identity = plan.compiler_lock[target]
            if target in self.denied_targets:
                raw_findings.append(
                    ("compiler_target_denied", target, f"compiler target is explicitly denied: {target}")
                )
            elif target not in self.allowed_compilers:
                raw_findings.append(
                    ("compiler_target_not_allowed", target, f"compiler target is not allowlisted: {target}")
                )
            elif identity not in self.allowed_compilers[target]:
                raw_findings.append(
                    (
                        "compiler_version_not_allowed",
                        target,
                        f"compiler identity is not allowlisted: {identity}",
                    )
                )

        findings: list[CompilerPolicyFinding] = []
        for code, target, message in sorted(raw_findings):
            finding_payload = {
                "policy_revision": self.revision_id,
                "plan_digest": plan.plan_digest,
                "code": code,
                "target": target,
                "message": message,
            }
            findings.append(
                CompilerPolicyFinding(
                    finding_id=content_digest(finding_payload),
                    code=code,
                    target=target,
                    message=message,
                    waiver_allowed=code in self.waiver_allowed_codes and code != "compile_plan_invalid",
                )
            )

        waiver_index: dict[tuple[str, str], CompilationWaiver] = {}
        for waiver in sorted(waivers, key=lambda item: item.waiver_id):
            waiver_index.setdefault((waiver.policy_revision, waiver.finding_id), waiver)
        resolved = []
        for finding in findings:
            waiver = waiver_index.get((self.revision_id, finding.finding_id))
            if finding.waiver_allowed and waiver is not None:
                finding = replace(finding, resolved=True, waiver_id=waiver.waiver_id)
            resolved.append(finding)
        conforms = plan.valid and all(finding.resolved for finding in resolved)
        payload = {
            "api_version": "aof.compiler-policy-report/v1",
            "policy_resource_id": self.resource_id,
            "policy_revision": self.revision_id,
            "plan_digest": plan.plan_digest,
            "conforms": conforms,
            "findings": [finding.to_dict() for finding in resolved],
        }
        return CompilerPolicyReport(
            policy_resource_id=self.resource_id,
            policy_revision=self.revision_id,
            plan_digest=plan.plan_digest,
            conforms=conforms,
            findings=tuple(resolved),
            report_digest=content_digest(canonical_data(payload)),
        )
