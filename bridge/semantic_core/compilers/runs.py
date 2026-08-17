"""Immutable compilation runs, reproducibility checks, and channel pointers."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore

from ..canonical import canonical_data, canonical_json, content_digest
from ..models import SemanticResource
from ..releases import KnowledgeRelease
from .base import CompilePlan, CompilerRegistry
from .governance import CompilationWaiver, CompilerPolicy


class CompilationRunError(ValueError):
    """Raised when a governed compilation run transition is unsafe."""


_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_RUN_FIELDS = {
    "api_version",
    "run_id",
    "plan_digest",
    "release_id",
    "release_digest",
    "requested_targets",
    "compiler_lock",
    "policy_resource_id",
    "policy_revision",
    "policy_report_digest",
    "waiver_ids",
    "artifacts",
    "reproducible",
    "replay_of",
    "decision_id",
    "run_digest",
}


def _validate_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise CompilationRunError(f"{field} must use lowercase safe characters")
    return value


def _non_empty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompilationRunError(f"{field} must be a non-empty string")
    return value.strip()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class CompilationRun:
    run_id: str
    plan_digest: str
    release_id: str
    release_digest: str
    requested_targets: tuple[str, ...]
    compiler_lock: Mapping[str, str]
    policy_resource_id: str
    policy_revision: str
    policy_report_digest: str
    waiver_ids: tuple[str, ...]
    artifacts: tuple[Mapping[str, Any], ...]
    reproducible: bool
    replay_of: str | None
    decision_id: str
    run_digest: str

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        plan: CompilePlan,
        release: KnowledgeRelease,
        policy: CompilerPolicy,
        policy_report_digest: str,
        waiver_ids: Iterable[str],
        artifacts: Iterable[Mapping[str, Any]],
        reproducible: bool,
        replay_of: str | None,
        decision_id: str,
    ) -> "CompilationRun":
        _validate_id(run_id, "run_id")
        if replay_of is not None:
            _validate_id(replay_of, "replay_of")
            if replay_of == run_id:
                raise CompilationRunError("a compilation run cannot replay itself")
        normalized_artifacts = [canonical_data(dict(item)) for item in artifacts]
        payload = {
            "api_version": "aof.compilation-run/v1",
            "run_id": run_id,
            "plan_digest": plan.plan_digest,
            "release_id": release.release_id,
            "release_digest": release.release_digest,
            "requested_targets": list(plan.requested_targets),
            "compiler_lock": canonical_data(plan.compiler_lock),
            "policy_resource_id": policy.resource_id,
            "policy_revision": policy.revision_id,
            "policy_report_digest": policy_report_digest,
            "waiver_ids": sorted(set(waiver_ids)),
            "artifacts": normalized_artifacts,
            "reproducible": reproducible,
            "replay_of": replay_of,
            "decision_id": _non_empty(decision_id, "decision_id"),
        }
        return cls._from_payload(payload, content_digest(payload))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompilationRun":
        if set(value) != _RUN_FIELDS:
            raise CompilationRunError("compilation run fields do not match the v1 contract")
        payload = {key: canonical_data(item) for key, item in value.items() if key != "run_digest"}
        if payload.get("api_version") != "aof.compilation-run/v1":
            raise CompilationRunError("unsupported compilation run api_version")
        supplied = str(value.get("run_digest", ""))
        if supplied != content_digest(payload):
            raise CompilationRunError("run_digest does not match compilation run content")
        return cls._from_payload(payload, supplied)

    @classmethod
    def _from_payload(cls, payload: Mapping[str, Any], digest: str) -> "CompilationRun":
        run_id = _validate_id(str(payload.get("run_id", "")), "run_id")
        replay_of = payload.get("replay_of")
        if replay_of is not None:
            _validate_id(str(replay_of), "replay_of")
        return cls(
            run_id=run_id,
            plan_digest=str(payload.get("plan_digest", "")),
            release_id=str(payload.get("release_id", "")),
            release_digest=str(payload.get("release_digest", "")),
            requested_targets=tuple(str(item) for item in payload.get("requested_targets", ())),
            compiler_lock=_freeze(payload.get("compiler_lock", {})),
            policy_resource_id=str(payload.get("policy_resource_id", "")),
            policy_revision=str(payload.get("policy_revision", "")),
            policy_report_digest=str(payload.get("policy_report_digest", "")),
            waiver_ids=tuple(str(item) for item in payload.get("waiver_ids", ())),
            artifacts=tuple(_freeze(item) for item in payload.get("artifacts", ())),
            reproducible=bool(payload.get("reproducible", False)),
            replay_of=str(replay_of) if replay_of is not None else None,
            decision_id=str(payload.get("decision_id", "")),
            run_digest=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.compilation-run/v1",
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "requested_targets": list(self.requested_targets),
            "compiler_lock": canonical_data(self.compiler_lock),
            "policy_resource_id": self.policy_resource_id,
            "policy_revision": self.policy_revision,
            "policy_report_digest": self.policy_report_digest,
            "waiver_ids": list(self.waiver_ids),
            "artifacts": [canonical_data(item) for item in self.artifacts],
            "reproducible": self.reproducible,
            "replay_of": self.replay_of,
            "decision_id": self.decision_id,
            "run_digest": self.run_digest,
        }


class CompilationRunRepository:
    """File-backed immutable run records with auditable mutable channel pointers."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def put(self, run: CompilationRun) -> CompilationRun:
        path = self._run_path(run.run_id)
        if path.exists():
            existing = CompilationRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if existing.run_digest != run.run_digest:
                raise CompilationRunError(f"compilation run cannot be overwritten: {run.run_id}")
            return existing
        self._write(path, run.to_dict())
        return run

    def get(self, run_id: str) -> CompilationRun | None:
        path = self._run_path(run_id)
        if not path.exists():
            return None
        return CompilationRun.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def get_channel(self, channel: str) -> dict[str, Any] | None:
        path = self._channel_path(channel)
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        supplied = value.pop("pointer_digest", None)
        if supplied != content_digest(value):
            raise CompilationRunError(f"channel pointer digest mismatch: {channel}")
        return {**value, "pointer_digest": supplied}

    def advance_channel(self, channel: str, event: Mapping[str, Any]) -> dict[str, Any]:
        _validate_id(channel, "channel")
        current = self.get_channel(channel)
        history = list(current["history"]) if current else []
        normalized_event = canonical_data(dict(event))
        event_payload = {key: value for key, value in normalized_event.items() if key != "event_digest"}
        normalized_event["event_digest"] = content_digest(event_payload)
        history.append(normalized_event)
        payload = {
            "api_version": "aof.compilation-channel/v1",
            "channel": channel,
            "run_id": normalized_event["run_id"],
            "version": len(history),
            "history": history,
        }
        pointer = {**payload, "pointer_digest": content_digest(payload)}
        self._write(self._channel_path(channel), pointer)
        return pointer

    def _run_path(self, run_id: str) -> Path:
        return self.root / "runs" / f"{_validate_id(run_id, 'run_id')}.json"

    def _channel_path(self, channel: str) -> Path:
        return self.root / "channels" / f"{_validate_id(channel, 'channel')}.json"

    @staticmethod
    def _write(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
        temporary.replace(path)


class CompilationRunService:
    """Execute policy-conforming plans and govern their deployment pointers."""

    def __init__(
        self,
        repository: CompilationRunRepository,
        *,
        registry: CompilerRegistry,
        decision_store: DecisionProvenanceStore,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.decision_store = decision_store

    def execute(
        self,
        *,
        run_id: str,
        plan: CompilePlan,
        policy: CompilerPolicy,
        release: KnowledgeRelease,
        resources: Iterable[SemanticResource],
        actor: str,
        rationale: str,
        waivers: Iterable[CompilationWaiver] = (),
        parent_decision_ids: Iterable[str] = (),
    ) -> CompilationRun:
        return self._execute(
            run_id=run_id,
            plan=plan,
            policy=policy,
            release=release,
            resources=resources,
            actor=actor,
            rationale=rationale,
            waivers=waivers,
            parent_decision_ids=parent_decision_ids,
            replay_of=None,
            expected_artifacts=None,
            expected_policy_report_digest=None,
        )

    def replay(
        self,
        source_run_id: str,
        *,
        run_id: str,
        policy: CompilerPolicy,
        release: KnowledgeRelease,
        resources: Iterable[SemanticResource],
        actor: str,
        rationale: str,
        waivers: Iterable[CompilationWaiver] = (),
    ) -> CompilationRun:
        source = self._require_run(source_run_id)
        if policy.revision_id != source.policy_revision:
            raise CompilationRunError("replay requires the exact compiler policy revision")
        frozen_resources = tuple(resources)
        plan = self.registry.plan(
            release, resources=frozen_resources, targets=source.requested_targets
        )
        if plan.plan_digest != source.plan_digest:
            raise CompilationRunError("replay inputs do not reproduce the source compile plan")
        return self._execute(
            run_id=run_id,
            plan=plan,
            policy=policy,
            release=release,
            resources=frozen_resources,
            actor=actor,
            rationale=rationale,
            waivers=waivers,
            parent_decision_ids=(source.decision_id,),
            replay_of=source.run_id,
            expected_artifacts=source.artifacts,
            expected_policy_report_digest=source.policy_report_digest,
        )

    def promote(
        self,
        run_id: str,
        *,
        channel: str,
        actor: str,
        approved_by: str,
        rationale: str,
    ) -> dict[str, Any]:
        run = self._require_run(run_id)
        if not run.reproducible:
            raise CompilationRunError("promotion requires an independent replay")
        actor = _non_empty(actor, "actor")
        approved_by = _non_empty(approved_by, "approved_by")
        rationale = _non_empty(rationale, "rationale")
        if self._subject(actor) == self._subject(approved_by):
            raise CompilationRunError("promotion requires separation between approver and publisher")
        current = self.repository.get_channel(channel)
        if current and current["run_id"] == run_id:
            raise CompilationRunError(f"channel already points to compilation run: {run_id}")
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_compile_promotion",
            conclusion=f"promoted {run_id} to {channel}",
            rationale=rationale,
            parent_decision_ids=[run.decision_id],
            evidence=[
                {"id": f"run:{run_id}", "type": "compilation_run", "content_hash": run.run_digest}
            ],
            policies=[f"{run.policy_resource_id}@{run.policy_revision}"],
            tags=["semantic", "compile", "promotion"],
            output_entities=[{"id": f"channel:{channel}", "type": "compilation_channel"}],
        )
        return self.repository.advance_channel(
            channel,
            {
                "action": "promote",
                "run_id": run_id,
                "previous_run_id": current["run_id"] if current else None,
                "actor": actor,
                "approved_by": approved_by,
                "decision_id": decision["decision"]["id"],
            },
        )

    def rollback(
        self,
        *,
        channel: str,
        to_run_id: str,
        actor: str,
        rationale: str,
    ) -> dict[str, Any]:
        current = self.repository.get_channel(channel)
        if current is None:
            raise CompilationRunError(f"compilation channel does not exist: {channel}")
        if current["run_id"] == to_run_id:
            raise CompilationRunError(f"channel already points to compilation run: {to_run_id}")
        historical = {event["run_id"] for event in current["history"]}
        if to_run_id not in historical:
            raise CompilationRunError("rollback target was never promoted to this channel")
        target = self._require_run(to_run_id)
        if not target.reproducible:
            raise CompilationRunError("rollback target is not independently reproducible")
        actor = _non_empty(actor, "actor")
        rationale = _non_empty(rationale, "rationale")
        current_decision = current["history"][-1]["decision_id"]
        parents = list(dict.fromkeys([current_decision, target.decision_id]))
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="semantic_compile_rollback",
            conclusion=f"rolled {channel} back to {to_run_id}",
            rationale=rationale,
            parent_decision_ids=parents,
            evidence=[
                {"id": f"run:{to_run_id}", "type": "compilation_run", "content_hash": target.run_digest}
            ],
            policies=[f"{target.policy_resource_id}@{target.policy_revision}"],
            tags=["semantic", "compile", "rollback"],
            output_entities=[{"id": f"channel:{channel}", "type": "compilation_channel"}],
        )
        return self.repository.advance_channel(
            channel,
            {
                "action": "rollback",
                "run_id": to_run_id,
                "previous_run_id": current["run_id"],
                "actor": actor,
                "approved_by": None,
                "decision_id": decision["decision"]["id"],
            },
        )

    def _execute(
        self,
        *,
        run_id: str,
        plan: CompilePlan,
        policy: CompilerPolicy,
        release: KnowledgeRelease,
        resources: Iterable[SemanticResource],
        actor: str,
        rationale: str,
        waivers: Iterable[CompilationWaiver],
        parent_decision_ids: Iterable[str],
        replay_of: str | None,
        expected_artifacts: Iterable[Mapping[str, Any]] | None,
        expected_policy_report_digest: str | None,
    ) -> CompilationRun:
        _validate_id(run_id, "run_id")
        if self.repository.get(run_id) is not None:
            raise CompilationRunError(f"compilation run already exists: {run_id}")
        actor = _non_empty(actor, "actor")
        rationale = _non_empty(rationale, "rationale")
        frozen_resources = tuple(resources)
        frozen_waivers = tuple(waivers)
        actual_plan = self.registry.plan(
            release, resources=frozen_resources, targets=plan.requested_targets
        )
        if actual_plan.plan_digest != plan.plan_digest:
            raise CompilationRunError("compile plan does not match the supplied release and resources")
        report = policy.evaluate(plan, waivers=frozen_waivers)
        if not report.conforms:
            raise CompilationRunError("compiler policy report does not conform")
        if (
            expected_policy_report_digest is not None
            and report.report_digest != expected_policy_report_digest
        ):
            raise CompilationRunError("replay requires the exact compiler policy report and waivers")
        artifacts = []
        for step in plan.steps:
            artifact = self.registry.compile(
                step.target,
                release,
                self.repository.root / "artifacts" / run_id / step.target,
                resources=frozen_resources,
            )
            if artifact.compiler != step.compiler:
                raise CompilationRunError("compiled artifact violates the compiler lock")
            artifacts.append(artifact.to_dict())
        actual_identity = self._artifact_identity(artifacts)
        expected_identity = (
            self._artifact_identity(expected_artifacts) if expected_artifacts is not None else None
        )
        reproducible = expected_identity is not None and actual_identity == expected_identity
        decision_type = "semantic_compile_replay" if replay_of else "semantic_compile_run"
        conclusion = (
            f"reproduced compilation run {replay_of}"
            if reproducible
            else f"compiled immutable run {run_id}"
        )
        if replay_of and not reproducible:
            conclusion = f"compilation replay diverged from {replay_of}"
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type=decision_type,
            conclusion=conclusion,
            rationale=rationale,
            parent_decision_ids=parent_decision_ids,
            evidence=[
                {"id": release.release_id, "type": "knowledge_release", "content_hash": release.release_digest},
                {"id": f"plan:{plan.plan_digest}", "type": "compile_plan", "content_hash": plan.plan_digest},
                {
                    "id": f"policy-report:{report.report_digest}",
                    "type": "compiler_policy_report",
                    "content_hash": report.report_digest,
                },
            ],
            policies=[f"{policy.resource_id}@{policy.revision_id}"],
            tags=["semantic", "compile", "replay" if replay_of else "run"],
            status="failed" if replay_of and not reproducible else "completed",
            output_entities=[{"id": f"run:{run_id}", "type": "compilation_run"}],
        )
        run = CompilationRun.build(
            run_id=run_id,
            plan=plan,
            release=release,
            policy=policy,
            policy_report_digest=report.report_digest,
            waiver_ids=(
                finding.waiver_id
                for finding in report.findings
                if finding.resolved and finding.waiver_id is not None
            ),
            artifacts=artifacts,
            reproducible=reproducible,
            replay_of=replay_of,
            decision_id=decision["decision"]["id"],
        )
        return self.repository.put(run)

    def _require_run(self, run_id: str) -> CompilationRun:
        run = self.repository.get(run_id)
        if run is None:
            raise CompilationRunError(f"compilation run not found: {run_id}")
        if self.decision_store.get(run.decision_id) is None:
            raise CompilationRunError(f"compilation run decision is missing: {run.decision_id}")
        return run

    @staticmethod
    def _artifact_identity(
        artifacts: Iterable[Mapping[str, Any]],
    ) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            sorted(
                (
                    str(item.get("target", "")),
                    str(item.get("compiler", "")),
                    str(item.get("content_hash", "")),
                )
                for item in artifacts
            )
        )

    @staticmethod
    def _subject(actor: str) -> str:
        return actor.split(":", 1)[-1]
