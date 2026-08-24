"""Governed Proposal -> Knowledge Release lifecycle with decision provenance."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore

from .canonical import canonical_json, content_digest
from .attestations import HmacReleaseAttestor
from .compilers import CompilerRegistry
from .impact import SemanticImpactAnalyzer
from .models import ResourceKind, SemanticResource
from .releases import (
    FileReleaseRepository,
    KnowledgeRelease,
    ReleaseError,
    SqliteReleaseRepository,
)


class SemanticGovernanceError(ValueError):
    """Raised when a semantic governance transition is invalid."""


_PROPOSAL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class SemanticFinding:
    finding_id: str
    validator: str
    severity: str
    message: str
    resource_id: str | None = None
    waiver_allowed: bool = False
    details: Mapping[str, Any] = MappingProxyType({})

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "validator": self.validator,
            "severity": self.severity,
            "message": self.message,
            "resource_id": self.resource_id,
            "waiver_allowed": self.waiver_allowed,
            "details": json.loads(canonical_json(self.details)),
        }


SemanticValidator = Callable[[tuple[SemanticResource, ...]], Iterable[SemanticFinding]]


class SemanticGovernancePolicy:
    """Small deterministic RBAC policy for the release control plane."""

    _ROLES = {
        "create": {"editor", "owner", "admin"},
        "validate": {"validator", "admin"},
        "waive": {"risk-owner", "admin"},
        "request_changes": {"reviewer", "admin"},
        "approve": {"reviewer", "admin"},
        "compile": {"compiler", "admin"},
        "publish": {"publisher", "admin"},
    }

    def authorize(self, actor: str, action: str) -> None:
        role = actor.split(":", 1)[0]
        if role not in self._ROLES[action]:
            raise SemanticGovernanceError(f"actor role '{role}' cannot {action}")

    @staticmethod
    def subject(actor: str) -> str:
        return actor.split(":", 1)[-1]


class SemanticGovernanceService:
    """File-backed P0 control plane; immutable evidence lives in the decision ledger."""

    def __init__(
        self,
        root: str | Path,
        *,
        decision_store: DecisionProvenanceStore | None = None,
        compiler_registry: CompilerRegistry | None = None,
        validators: Iterable[SemanticValidator] = (),
        release_repository: FileReleaseRepository
        | SqliteReleaseRepository
        | None = None,
        access_policy: SemanticGovernancePolicy | None = None,
        release_attestor: HmacReleaseAttestor | None = None,
    ) -> None:
        self.root = Path(root)
        self.decision_store = decision_store or DecisionProvenanceStore(
            self.root / "decisions.jsonl"
        )
        self.compiler_registry = compiler_registry or CompilerRegistry()
        self.validators = tuple(validators)
        self.release_repository = release_repository or FileReleaseRepository(
            self.root / "releases"
        )
        self.access_policy = access_policy
        self.release_attestor = release_attestor

    def create_proposal(
        self,
        *,
        proposal_id: str,
        release_id: str,
        resources: Iterable[SemanticResource],
        actor: str,
        rationale: str,
        parent_release: str | None = None,
        scope: Mapping[str, Any] | None = None,
        parent_decision_ids: Iterable[str] = (),
        source_evidence: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        self._authorize(actor, "create")
        if not _PROPOSAL_ID.fullmatch(proposal_id):
            raise SemanticGovernanceError("invalid proposal_id")
        path = self._proposal_path(proposal_id)
        if path.exists():
            raise SemanticGovernanceError(f"proposal already exists: {proposal_id}")
        frozen_resources = tuple(sorted(resources, key=lambda item: item.resource_id))
        tenants = {
            resource.resource_id.split("/", 3)[2] for resource in frozen_resources
        }
        if len(tenants) != 1:
            raise SemanticGovernanceError(
                "a proposal must contain resources from exactly one tenant"
            )
        tenant_id = next(iter(tenants))
        normalized_scope = dict(scope or {})
        if normalized_scope.get("tenant_id") not in (None, tenant_id):
            raise SemanticGovernanceError(
                "proposal scope tenant_id does not match resource tenant"
            )
        normalized_scope["tenant_id"] = tenant_id
        try:
            candidate = KnowledgeRelease.build(
                release_id=release_id,
                resources=frozen_resources,
                parent_release=parent_release,
                scope=normalized_scope,
            )
        except ReleaseError as exc:
            raise SemanticGovernanceError(str(exc)) from exc
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_proposal",
            conclusion=f"created proposal {proposal_id}",
            rationale=rationale,
            parent_decision_ids=parent_decision_ids,
            evidence=[*self._resource_evidence(frozen_resources), *source_evidence],
            policies=["policy:semantic-release-governance"],
            tags=["semantic", "proposal"],
            output_entities=[
                {
                    "id": f"proposal:{proposal_id}",
                    "type": "semantic_proposal",
                    "content_hash": candidate.release_digest,
                }
            ],
        )
        manifest = {
            "api_version": "aof.proposal/v1",
            "proposal_id": proposal_id,
            "release_id": release_id,
            "parent_release": parent_release,
            "scope": normalized_scope,
            "tenant_id": tenant_id,
            "created_by": actor,
            "state": "proposed",
            "resources": [resource.to_dict() for resource in frozen_resources],
            "candidate_digest": candidate.release_digest,
            "proposal_decision_id": decision["decision"]["id"],
            "review": None,
            "waivers": [],
            "artifacts": [],
        }
        self._write(path, manifest)
        return manifest

    def get_proposal(
        self, proposal_id: str, *, tenant_id: str | None = None
    ) -> dict[str, Any]:
        path = self._proposal_path(proposal_id)
        if not path.exists():
            raise SemanticGovernanceError(f"proposal not found: {proposal_id}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if tenant_id is not None and manifest.get("tenant_id") != tenant_id:
            raise SemanticGovernanceError(f"proposal not found: {proposal_id}")
        return manifest

    def validate(self, proposal_id: str, *, actor: str) -> dict[str, Any]:
        manifest = self.get_proposal(proposal_id)
        self._authorize(actor, "validate")
        self._require_state(manifest, {"proposed"}, "validate")
        resources = self._resources(manifest)
        findings = []
        for validator in self.validators:
            for finding in validator(resources):
                if not isinstance(finding, SemanticFinding):
                    raise SemanticGovernanceError(
                        "validators must return SemanticFinding values"
                    )
                findings.append(finding.to_dict())
        findings.sort(key=lambda item: item["finding_id"])
        conforms = not any(
            item["severity"].lower() in {"error", "violation", "blocking"}
            for item in findings
        )
        review = {
            "conforms": conforms,
            "findings": findings,
            "resource_count": len(resources),
            "actor": actor,
        }
        review["review_hash"] = content_digest(review)
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_validation",
            conclusion="validation passed"
            if conforms
            else "validation requires conflict review",
            rationale=f"Validated {len(resources)} frozen semantic revisions.",
            parent_decision_ids=[manifest["proposal_decision_id"]],
            evidence=[
                {
                    "id": f"proposal:{proposal_id}:candidate",
                    "type": "release_candidate",
                    "content_hash": manifest["candidate_digest"],
                }
            ],
            policies=["policy:semantic-release-validation"],
            tags=["semantic", "validation"],
            output_entities=[
                {
                    "id": f"review:{review['review_hash']}",
                    "type": "semantic_review",
                    "content_hash": review["review_hash"],
                }
            ],
        )
        review["decision_id"] = decision["decision"]["id"]
        manifest["review"] = review
        manifest["state"] = "review" if conforms else "conflict_review"
        self._write(self._proposal_path(proposal_id), manifest)
        return {**review, "state": manifest["state"]}

    def impact(
        self, proposal_id: str, *, tenant_id: str | None = None
    ) -> dict[str, Any]:
        manifest = self.get_proposal(proposal_id, tenant_id=tenant_id)
        resources = self._resources(manifest)
        current = {resource.resource_id: resource.revision_id for resource in resources}
        previous: dict[str, str] = {}
        parent: KnowledgeRelease | None = None
        if manifest.get("parent_release"):
            parent = self._release_get(
                manifest["parent_release"], manifest["tenant_id"]
            )
            if parent is None:
                raise SemanticGovernanceError(
                    f"parent release not found: {manifest['parent_release']}"
                )
            previous = {item.resource_id: item.revision_id for item in parent.resources}
        previous_resources = []
        current_by_id = {resource.resource_id: resource for resource in resources}
        if parent is not None:
            for ref in parent.resources:
                current_resource = current_by_id.get(ref.resource_id)
                if current_resource is not None:
                    previous_resources.append(
                        replace(current_resource, revision_id=ref.revision_id)
                    )
                    continue
                parts = ref.resource_id.split("/")
                previous_resources.append(
                    SemanticResource.create(
                        resource_id=ref.resource_id,
                        kind=ResourceKind(ref.kind),
                        name=parts[-1],
                        domain=parts[-3],
                        owner="retired-resource",
                    )
                )
        detailed = SemanticImpactAnalyzer().compare(
            previous_resources,
            resources,
            compiled_artifacts=parent.compiled_artifacts if parent is not None else (),
        )
        return {
            "proposal_id": proposal_id,
            "resource_count": len(resources),
            "added": sorted(set(current) - set(previous)),
            "removed": sorted(set(previous) - set(current)),
            "changed": sorted(
                key
                for key in set(current) & set(previous)
                if current[key] != previous[key]
            ),
            "unchanged": sorted(
                key
                for key in set(current) & set(previous)
                if current[key] == previous[key]
            ),
            "affected_resource_ids": list(detailed.affected_resource_ids),
            "affected_mcp_tools": list(detailed.affected_mcp_tools),
            "affected_contract_ids": list(detailed.affected_contract_ids),
            "affected_artifacts": list(detailed.affected_artifacts),
            "impact_report_digest": detailed.report_digest,
        }

    def waive_finding(
        self,
        proposal_id: str,
        *,
        finding_id: str,
        actor: str,
        rationale: str,
        policy: str,
    ) -> dict[str, Any]:
        manifest = self.get_proposal(proposal_id)
        self._authorize(actor, "waive")
        self._require_state(manifest, {"conflict_review"}, "waive finding")
        review = manifest.get("review") or {}
        finding = next(
            (
                item
                for item in review.get("findings", [])
                if item["finding_id"] == finding_id
            ),
            None,
        )
        if finding is None:
            raise SemanticGovernanceError(f"finding not found: {finding_id}")
        if not finding.get("waiver_allowed"):
            raise SemanticGovernanceError(f"finding cannot be waived: {finding_id}")
        if any(item["finding_id"] == finding_id for item in manifest["waivers"]):
            raise SemanticGovernanceError(f"finding already waived: {finding_id}")
        waiver_payload = {
            "finding_id": finding_id,
            "actor": actor,
            "rationale": rationale,
            "policy": policy,
            "review_hash": review["review_hash"],
        }
        waiver_id = f"waiver:{content_digest(waiver_payload).split(':', 1)[1][:20]}"
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_finding_waiver",
            conclusion=f"waived {finding_id}",
            rationale=rationale,
            parent_decision_ids=[review["decision_id"]],
            evidence=[
                {
                    "id": finding_id,
                    "type": "semantic_finding",
                    "content_hash": review["review_hash"],
                }
            ],
            policies=[policy],
            tags=["semantic", "waiver"],
            output_entities=[{"id": waiver_id, "type": "semantic_waiver"}],
        )
        waiver = {
            **waiver_payload,
            "waiver_id": waiver_id,
            "decision_id": decision["decision"]["id"],
        }
        manifest["waivers"].append(waiver)
        self._write(self._proposal_path(proposal_id), manifest)
        return waiver

    def request_changes(
        self, proposal_id: str, *, actor: str, rationale: str
    ) -> dict[str, Any]:
        manifest = self.get_proposal(proposal_id)
        self._authorize(actor, "request_changes")
        self._require_state(manifest, {"review", "conflict_review"}, "request changes")
        review = manifest["review"]
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_changes_requested",
            conclusion=f"changes requested for {proposal_id}",
            rationale=rationale,
            parent_decision_ids=[review["decision_id"]],
            evidence=[
                {
                    "id": f"review:{review['review_hash']}",
                    "type": "semantic_review",
                    "content_hash": review["review_hash"],
                }
            ],
            policies=["policy:semantic-release-governance"],
            tags=["semantic", "changes-requested"],
        )
        manifest["state"] = "changes_requested"
        manifest["changes_request_decision_id"] = decision["decision"]["id"]
        self._write(self._proposal_path(proposal_id), manifest)
        return manifest

    def approve(
        self, proposal_id: str, *, actor: str, rationale: str
    ) -> dict[str, Any]:
        manifest = self.get_proposal(proposal_id)
        self._authorize(actor, "approve")
        self._require_state(manifest, {"review", "conflict_review"}, "approve")
        if self._subject(actor) == self._subject(manifest["created_by"]):
            raise SemanticGovernanceError(
                "separation of duties forbids creator self-approval"
            )
        review = manifest["review"]
        blocking = {
            item["finding_id"]
            for item in review["findings"]
            if item["severity"].lower() in {"error", "violation", "blocking"}
        }
        waived = {item["finding_id"] for item in manifest["waivers"]}
        unresolved = sorted(blocking - waived)
        if unresolved:
            raise SemanticGovernanceError(
                f"approval blocked by unresolved findings: {', '.join(unresolved)}"
            )
        parents = [
            review["decision_id"],
            *[item["decision_id"] for item in manifest["waivers"]],
        ]
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_approval",
            conclusion=f"approved {proposal_id}",
            rationale=rationale,
            parent_decision_ids=parents,
            evidence=[
                {
                    "id": f"review:{review['review_hash']}",
                    "type": "semantic_review",
                    "content_hash": review["review_hash"],
                }
            ],
            policies=["policy:semantic-release-approval"],
            tags=["semantic", "approval"],
            output_entities=[
                {"id": f"approval:{proposal_id}", "type": "semantic_approval"}
            ],
        )
        manifest["state"] = "approved"
        manifest["approval_decision_id"] = decision["decision"]["id"]
        manifest["approved_by"] = actor
        self._write(self._proposal_path(proposal_id), manifest)
        return manifest

    def compile(
        self, proposal_id: str, *, actor: str, targets: Iterable[str]
    ) -> dict[str, Any]:
        manifest = self.get_proposal(proposal_id)
        self._authorize(actor, "compile")
        self._require_state(manifest, {"approved"}, "compile")
        resources = self._resources(manifest)
        candidate = KnowledgeRelease.build(
            release_id=manifest["release_id"],
            resources=resources,
            parent_release=manifest.get("parent_release"),
            scope=manifest.get("scope"),
            validation={"review_hash": manifest["review"]["review_hash"]},
            governance={"approval_decision_id": manifest["approval_decision_id"]},
        )
        artifacts = []
        for target in sorted(set(targets)):
            artifact = self.compiler_registry.compile(
                target,
                candidate,
                self.root / "compiled" / proposal_id / target,
                resources=resources,
            )
            artifacts.append(artifact.to_dict())
        if not artifacts:
            raise SemanticGovernanceError("at least one compiler target is required")
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_compile",
            conclusion=f"compiled {len(artifacts)} artifact(s) for {proposal_id}",
            rationale="Approved frozen semantic revisions compiled through registered deterministic targets.",
            parent_decision_ids=[manifest["approval_decision_id"]],
            evidence=[
                {
                    "id": f"candidate:{proposal_id}",
                    "type": "release_candidate",
                    "content_hash": candidate.release_digest,
                }
            ],
            policies=["policy:semantic-release-compilation"],
            tags=["semantic", "compile"],
            output_entities=[
                {
                    "id": f"artifact:{item['target']}:{item['content_hash']}",
                    "type": "compiled_artifact",
                    "content_hash": item["content_hash"],
                }
                for item in artifacts
            ],
        )
        final_release = KnowledgeRelease.build(
            release_id=manifest["release_id"],
            resources=resources,
            parent_release=manifest.get("parent_release"),
            scope=manifest.get("scope"),
            compiled_artifacts=artifacts,
            validation={"review_hash": manifest["review"]["review_hash"]},
            governance={
                "approval_decision_id": manifest["approval_decision_id"],
                "compile_decision_id": decision["decision"]["id"],
            },
        )
        manifest["state"] = "ready"
        manifest["artifacts"] = artifacts
        manifest["compile_decision_id"] = decision["decision"]["id"]
        manifest["release"] = final_release.to_dict()
        self._write(self._proposal_path(proposal_id), manifest)
        return manifest

    def publish(self, proposal_id: str, *, actor: str) -> dict[str, Any]:
        manifest = self.get_proposal(proposal_id)
        self._authorize(actor, "publish")
        self._require_state(manifest, {"ready"}, "publish")
        if self._subject(actor) == self._subject(manifest["approved_by"]):
            raise SemanticGovernanceError(
                "separation of duties forbids approver self-publication"
            )
        release = KnowledgeRelease.from_dict(manifest["release"])
        self._release_publish(release, manifest["tenant_id"])
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_publish",
            conclusion=f"published {release.release_id}",
            rationale="Approved and verified compiled Knowledge Release published immutably.",
            parent_decision_ids=[manifest["compile_decision_id"]],
            evidence=[
                {
                    "id": release.release_id,
                    "type": "knowledge_release",
                    "content_hash": release.release_digest,
                }
            ],
            policies=["policy:semantic-release-publish"],
            tags=["semantic", "publish"],
            output_entities=[
                {
                    "id": release.release_id,
                    "type": "knowledge_release",
                    "content_hash": release.release_digest,
                }
            ],
        )
        manifest["state"] = "published"
        manifest["publish_decision_id"] = decision["decision"]["id"]
        if self.release_attestor is not None:
            manifest["attestation"] = self.release_attestor.sign(
                release,
                actor=actor,
                decision_id=decision["decision"]["id"],
                tenant_id=manifest["tenant_id"],
            )
        self._write(self._proposal_path(proposal_id), manifest)
        return manifest

    def _proposal_path(self, proposal_id: str) -> Path:
        if not _PROPOSAL_ID.fullmatch(proposal_id):
            raise SemanticGovernanceError("invalid proposal_id")
        return self.root / "proposals" / f"{proposal_id}.json"

    def _authorize(self, actor: str, action: str) -> None:
        if self.access_policy is not None:
            self.access_policy.authorize(actor, action)

    def _subject(self, actor: str) -> str:
        return self.access_policy.subject(actor) if self.access_policy else actor

    def _release_publish(
        self, release: KnowledgeRelease, tenant_id: str
    ) -> KnowledgeRelease:
        if isinstance(self.release_repository, SqliteReleaseRepository):
            return self.release_repository.publish(release, tenant_id=tenant_id)
        return self.release_repository.publish(release)

    def _release_get(self, release_id: str, tenant_id: str) -> KnowledgeRelease | None:
        if isinstance(self.release_repository, SqliteReleaseRepository):
            return self.release_repository.get(release_id, tenant_id=tenant_id)
        return self.release_repository.get(release_id)

    @staticmethod
    def _resources(manifest: Mapping[str, Any]) -> tuple[SemanticResource, ...]:
        return tuple(
            SemanticResource.from_dict(value) for value in manifest["resources"]
        )

    @staticmethod
    def _resource_evidence(
        resources: Iterable[SemanticResource],
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": resource.resource_id,
                "type": resource.kind.value,
                "content_hash": resource.revision_id,
            }
            for resource in resources
        ]

    @staticmethod
    def _require_state(
        manifest: Mapping[str, Any], allowed: set[str], action: str
    ) -> None:
        if manifest.get("state") not in allowed:
            raise SemanticGovernanceError(
                f"cannot {action} proposal in state {manifest.get('state')}; expected {', '.join(sorted(allowed))}"
            )

    @staticmethod
    def _write(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
        temporary.replace(path)
