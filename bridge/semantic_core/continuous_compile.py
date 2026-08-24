"""Orchestrate governed ingestion changes into replayed semantic releases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from bridge.decision_provenance import DecisionProvenanceStore

from .compilers import CompilationRunService, CompilerPolicy
from .continuous_ingest import SqliteContinuousIngestionRepository
from .governance import SemanticGovernanceService
from .models import SemanticResource
from .releases import KnowledgeRelease


@dataclass(frozen=True)
class ContinuousCompilePolicy:
    allow_schema_drift: bool = False


class ContinuousKnowledgeCompiler:
    """One deep boundary from an immutable IngestionRun to a promoted channel."""

    def __init__(
        self,
        ingestion: SqliteContinuousIngestionRepository,
        governance: SemanticGovernanceService,
        compilation: CompilationRunService,
        decisions: DecisionProvenanceStore,
        *,
        policy: ContinuousCompilePolicy = ContinuousCompilePolicy(),
    ) -> None:
        self.ingestion = ingestion
        self.governance = governance
        self.compilation = compilation
        self.decisions = decisions
        self.policy = policy

    def stage(
        self,
        ingestion_run_id: str,
        *,
        tenant_id: str,
        release_id: str,
        resources: Iterable[SemanticResource],
        actor: str,
        validator: str,
        parent_release: str | None = None,
    ) -> dict:
        run = self.ingestion.get_run(ingestion_run_id, tenant_id=tenant_id)
        changes = self.ingestion.get_change_set(ingestion_run_id, tenant_id=tenant_id)
        if any(changes.schema_drift.values()) and not self.policy.allow_schema_drift:
            return {
                "state": "schema_drift_review",
                "ingestion_run_id": run.run_id,
                "change_set_digest": changes.change_set_digest,
                "schema_drift": changes.schema_drift,
            }
        accepted = self.decisions.record(
            agent_id=actor,
            decision_type="knowledge_ingestion_accepted",
            conclusion=f"accepted {run.run_id} for semantic governance",
            rationale="Immutable source snapshot and deterministic ChangeSet entered the release gate.",
            evidence=[
                {
                    "id": run.run_id,
                    "type": "ingestion_run",
                    "content_hash": run.run_digest,
                },
                {
                    "id": changes.change_set_digest,
                    "type": "knowledge_change_set",
                    "content_hash": changes.change_set_digest,
                },
            ],
            tenant_id=tenant_id,
        )
        proposal_id = f"continuous-{run.run_id.removeprefix('ingest-')}"
        proposal = self.governance.create_proposal(
            proposal_id=proposal_id,
            release_id=release_id,
            resources=resources,
            actor=actor,
            rationale=f"Generated from {run.run_id} / {changes.change_set_digest}.",
            parent_release=parent_release,
            scope={"tenant_id": tenant_id},
            parent_decision_ids=[accepted["decision"]["id"]],
            source_evidence=[
                {
                    "id": run.run_id,
                    "type": "ingestion_run",
                    "content_hash": run.run_digest,
                },
                {
                    "id": changes.change_set_digest,
                    "type": "knowledge_change_set",
                    "content_hash": changes.change_set_digest,
                },
            ],
        )
        review = self.governance.validate(proposal_id, actor=validator)
        return {
            "state": review["state"],
            "proposal_id": proposal_id,
            "candidate_digest": proposal["candidate_digest"],
            "ingestion_run_id": run.run_id,
            "change_set_digest": changes.change_set_digest,
            "impact": self.governance.impact(proposal_id, tenant_id=tenant_id),
        }

    def approve_compile_promote(
        self,
        proposal_id: str,
        *,
        compiler_policy: CompilerPolicy,
        targets: Iterable[str],
        channel: str,
        approver: str,
        compiler: str,
        replay_compiler: str,
        publisher: str,
    ) -> dict:
        self.governance.approve(
            proposal_id,
            actor=approver,
            rationale="Continuous ingestion change reviewed.",
        )
        self.governance.compile(proposal_id, actor=compiler, targets=targets)
        published = self.governance.publish(proposal_id, actor=publisher)
        release = KnowledgeRelease.from_dict(published["release"])
        resources = tuple(
            SemanticResource.from_dict(item) for item in published["resources"]
        )
        suffix = proposal_id.removeprefix("continuous-")
        plan = self.compilation.registry.plan(
            release, resources=resources, targets=targets
        )
        first = self.compilation.execute(
            run_id=f"cc-{suffix}-1",
            plan=plan,
            policy=compiler_policy,
            release=release,
            resources=resources,
            actor=compiler,
            rationale="Compile published ingestion release.",
            parent_decision_ids=[published["publish_decision_id"]],
        )
        replay = self.compilation.replay(
            first.run_id,
            run_id=f"cc-{suffix}-2",
            policy=compiler_policy,
            release=release,
            resources=resources,
            actor=replay_compiler,
            rationale="Independently reproduce continuous compilation.",
        )
        pointer = self.compilation.promote(
            replay.run_id,
            channel=channel,
            actor=publisher,
            approved_by=approver,
            rationale="Promote reviewed reproducible ingestion release.",
        )
        return {
            "state": "promoted",
            "proposal_id": proposal_id,
            "release_id": release.release_id,
            "release_digest": release.release_digest,
            "compilation_run_id": replay.run_id,
            "run_digest": replay.run_digest,
            "channel": pointer,
        }
