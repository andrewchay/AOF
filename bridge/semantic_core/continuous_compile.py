# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
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
    promotion_mode: str = "manual"

    def __post_init__(self) -> None:
        if self.promotion_mode not in {"manual", "automatic"}:
            raise ValueError("promotion_mode must be manual or automatic")


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

    def run_cycle(
        self,
        ingestion_run_id: str,
        *,
        tenant_id: str,
        release_id: str,
        resources: Iterable[SemanticResource],
        actor: str,
        validator: str,
        targets: Iterable[str],
        channel: str,
        compiler_policy: CompilerPolicy | None = None,
        parent_release: str | None = None,
        approver: str | None = None,
        compiler: str | None = None,
        replay_compiler: str | None = None,
        publisher: str | None = None,
    ) -> dict:
        staged = self.stage(
            ingestion_run_id,
            tenant_id=tenant_id,
            release_id=release_id,
            resources=resources,
            actor=actor,
            validator=validator,
            parent_release=parent_release,
        )
        if staged["state"] != "review":
            return staged
        if self.policy.promotion_mode == "manual":
            return {**staged, "state": "awaiting_approval", "gate_state": "review"}
        required = {
            "compiler_policy": compiler_policy,
            "approver": approver,
            "compiler": compiler,
            "replay_compiler": replay_compiler,
            "publisher": publisher,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(
                f"automatic promotion requires: {', '.join(sorted(missing))}"
            )
        promoted = self.approve_compile_promote(
            staged["proposal_id"],
            compiler_policy=compiler_policy,
            targets=targets,
            channel=channel,
            approver=approver,
            compiler=compiler,
            replay_compiler=replay_compiler,
            publisher=publisher,
        )
        return {**staged, **promoted, "gate_state": "review"}

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
            decision_id = f"decision:ingestion-schema-drift:{run.run_id}"
            if self.decisions.get(decision_id) is None:
                self.decisions.record(
                    decision_id=decision_id,
                    agent_id=validator,
                    decision_type="knowledge_ingestion_schema_drift_blocked",
                    conclusion=f"blocked {run.run_id} pending schema drift review",
                    rationale="The continuous compilation policy forbids unreviewed schema drift.",
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
                    status="blocked",
                    tenant_id=tenant_id,
                    policies=["policy:continuous-compile:no-schema-drift"],
                    tags=["continuous-compile", "schema-drift", "blocked"],
                )
            return {
                "state": "schema_drift_review",
                "ingestion_run_id": run.run_id,
                "change_set_digest": changes.change_set_digest,
                "schema_drift": changes.schema_drift,
                "decision_id": decision_id,
            }
        proposal_id = f"continuous-{run.run_id.removeprefix('ingest-')}"
        try:
            existing = self.governance.get_proposal(
                proposal_id, tenant_id=tenant_id
            )
        except Exception as exc:
            if "proposal not found:" not in str(exc):
                raise
        else:
            return {
                "state": existing["state"],
                "proposal_id": proposal_id,
                "candidate_digest": existing["candidate_digest"],
                "ingestion_run_id": run.run_id,
                "change_set_digest": changes.change_set_digest,
                "impact": self.governance.impact(proposal_id, tenant_id=tenant_id),
                "idempotent_replay": True,
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
