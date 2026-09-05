"""Side-effect-free counterfactual reasoning over bitemporal object snapshots."""

from __future__ import annotations
from bridge.persistence.sqlite_support import managed_sqlite_connection

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.ontology_governance.reasoning import DatalogEngine

from .action_plans import ActionPlan
from .bitemporal import BitemporalObjectStore
from .canonical import canonical_data, canonical_json, content_digest


class SimulationError(ValueError):
    """Raised when a simulation request or persisted result is invalid."""


_PREDICATE = re.compile(r"^[A-Za-z_][A-Za-z0-9_:-]*$")
Fact = tuple[str, tuple[str, ...]]


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SimulationError(f"{field} must be a non-empty string")
    return value.strip()


def _instant(value: Any, field: str) -> str:
    from datetime import datetime, timezone

    raw = _text(value, field).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SimulationError(f"{field} must be an ISO-8601 instant") from exc
    if parsed.tzinfo is None:
        raise SimulationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _facts(values: Sequence[tuple[str, Sequence[str]]]) -> tuple[Fact, ...]:
    normalized = []
    for predicate, terms in values:
        name = _text(predicate, "fact predicate")
        if not _PREDICATE.fullmatch(name):
            raise SimulationError(f"invalid fact predicate: {name}")
        values_tuple = tuple(_text(str(item), "fact term") for item in terms)
        if not values_tuple:
            raise SimulationError("fact requires terms")
        normalized.append((name, values_tuple))
    return tuple(sorted(set(normalized)))


def _fact_dict(fact: Fact) -> dict[str, Any]:
    payload = {"predicate": fact[0], "terms": list(fact[1])}
    return {
        **payload,
        "fact_id": "fact-" + content_digest(payload).split(":", 1)[1][:24],
    }


def _fact_set(values: Sequence[Mapping[str, Any]]) -> set[Fact]:
    return {
        (str(item["predicate"]), tuple(str(term) for term in item["terms"]))
        for item in values
    }


def _label(fact: Fact) -> str:
    return f"{fact[0]}({','.join(fact[1])})"


def _term(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return canonical_json(value)


@dataclass(frozen=True)
class SimulationRequest:
    simulation_id: str
    tenant_id: str
    action_plan: Mapping[str, Any]
    valid_at: str
    known_at: str
    ruleset_id: str
    program: str
    field_predicates: Mapping[str, str]
    assertions: tuple[Fact, ...]
    retractions: tuple[Fact, ...]
    outcome_predicates: tuple[str, ...]
    blocking_predicates: tuple[str, ...]
    request_digest: str

    @classmethod
    def create(
        cls,
        *,
        simulation_id: str,
        action_plan: ActionPlan,
        valid_at: str,
        known_at: str,
        ruleset_id: str,
        program: str,
        field_predicates: Mapping[str, str],
        assumptions: Mapping[str, Sequence[tuple[str, Sequence[str]]]],
        outcome_predicates: Sequence[str],
        blocking_predicates: Sequence[str] = (),
    ) -> "SimulationRequest":
        if not action_plan.verify():
            raise SimulationError("action plan digest mismatch")
        mappings = {
            _text(field, "object field"): _text(predicate, "field predicate")
            for field, predicate in field_predicates.items()
        }
        if any(not _PREDICATE.fullmatch(value) for value in mappings.values()):
            raise SimulationError("field predicates must be valid Datalog predicates")
        assertions = _facts(tuple(assumptions.get("assertions", ())))
        retractions = _facts(tuple(assumptions.get("retractions", ())))
        if set(assertions) & set(retractions):
            raise SimulationError("an assumption cannot assert and retract one fact")
        outcomes = tuple(sorted({_text(item, "outcome predicate") for item in outcome_predicates}))
        blockers = tuple(sorted({_text(item, "blocking predicate") for item in blocking_predicates}))
        if not outcomes or not set(blockers).issubset(outcomes):
            raise SimulationError(
                "outcome predicates are required and must include all blocking predicates"
            )
        DatalogEngine(program, ruleset_id=ruleset_id)
        payload = {
            "api_version": "aof.simulation-request/v1",
            "simulation_id": _text(simulation_id, "simulation_id"),
            "tenant_id": action_plan.tenant_id,
            "action_plan": action_plan.to_dict(),
            "valid_at": _instant(valid_at, "valid_at"),
            "known_at": _instant(known_at, "known_at"),
            "ruleset_id": _text(ruleset_id, "ruleset_id"),
            "program": _text(program, "program"),
            "field_predicates": canonical_data(mappings),
            "assertions": [_fact_dict(item) for item in assertions],
            "retractions": [_fact_dict(item) for item in retractions],
            "outcome_predicates": list(outcomes),
            "blocking_predicates": list(blockers),
        }
        return cls(
            simulation_id=payload["simulation_id"],
            tenant_id=action_plan.tenant_id,
            action_plan=payload["action_plan"],
            valid_at=payload["valid_at"],
            known_at=payload["known_at"],
            ruleset_id=payload["ruleset_id"],
            program=payload["program"],
            field_predicates=payload["field_predicates"],
            assertions=assertions,
            retractions=retractions,
            outcome_predicates=outcomes,
            blocking_predicates=blockers,
            request_digest=content_digest(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(
            {
                "api_version": "aof.simulation-request/v1",
                "simulation_id": self.simulation_id,
                "tenant_id": self.tenant_id,
                "action_plan": self.action_plan,
                "valid_at": self.valid_at,
                "known_at": self.known_at,
                "ruleset_id": self.ruleset_id,
                "program": self.program,
                "field_predicates": self.field_predicates,
                "assertions": [_fact_dict(item) for item in self.assertions],
                "retractions": [_fact_dict(item) for item in self.retractions],
                "outcome_predicates": self.outcome_predicates,
                "blocking_predicates": self.blocking_predicates,
                "request_digest": self.request_digest,
            }
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SimulationRequest":
        plan = ActionPlan.from_dict(value.get("action_plan", {}))
        request = cls.create(
            simulation_id=str(value.get("simulation_id", "")),
            action_plan=plan,
            valid_at=str(value.get("valid_at", "")),
            known_at=str(value.get("known_at", "")),
            ruleset_id=str(value.get("ruleset_id", "")),
            program=str(value.get("program", "")),
            field_predicates=value.get("field_predicates", {}),
            assumptions={
                "assertions": [
                    (item["predicate"], item["terms"])
                    for item in value.get("assertions", ())
                ],
                "retractions": [
                    (item["predicate"], item["terms"])
                    for item in value.get("retractions", ())
                ],
            },
            outcome_predicates=value.get("outcome_predicates", ()),
            blocking_predicates=value.get("blocking_predicates", ()),
        )
        if value.get("request_digest") != request.request_digest:
            raise SimulationError("simulation request digest mismatch")
        return request


@dataclass(frozen=True)
class SimulationRun:
    run_id: str
    tenant_id: str
    simulation_id: str
    request: Mapping[str, Any]
    actor: str
    snapshot_evidence: tuple[Mapping[str, Any], ...]
    baseline_facts: tuple[Mapping[str, Any], ...]
    baseline_outcomes: tuple[Mapping[str, Any], ...]
    candidate_outcomes: tuple[Mapping[str, Any], ...]
    impact: Mapping[str, Any]
    state: str
    baseline_result_hash: str
    candidate_result_hash: str
    audit_decision_id: str
    run_digest: str

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(self.__dict__)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SimulationRun":
        payload = dict(value)
        for field in (
            "snapshot_evidence",
            "baseline_facts",
            "baseline_outcomes",
            "candidate_outcomes",
        ):
            payload[field] = tuple(payload[field])
        run = cls(**payload)
        SimulationRequest.from_dict(run.request)
        expected = content_digest(
            {key: item for key, item in run.to_dict().items() if key != "run_digest"}
        )
        if run.run_digest != expected:
            raise SimulationError("simulation run digest mismatch")
        return run


class SqliteBitemporalSimulationService:
    def __init__(
        self,
        path: str | Path,
        *,
        objects: BitemporalObjectStore,
        decision_store: DecisionProvenanceStore,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.objects = objects
        self.decisions = decision_store
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS simulation_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    simulation_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    run_digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE (tenant_id, simulation_id)
                );
                PRAGMA user_version=1;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)
    def _connection(self):
        """Transaction + close context manager (W09.01: no leaked connections)."""
        return managed_sqlite_connection(self._connect)


    def simulate(self, request: SimulationRequest, *, actor: str) -> SimulationRun:
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT request_digest, payload FROM simulation_runs "
                "WHERE tenant_id=? AND simulation_id=?",
                (request.tenant_id, request.simulation_id),
            ).fetchone()
        if existing is not None:
            if existing[0] != request.request_digest:
                raise SimulationError(
                    "simulation_id is already bound to a different request"
                )
            run = SimulationRun.from_dict(json.loads(existing[1]))
            self._record_decision(run)
            return run
        plan = ActionPlan.from_dict(request.action_plan)
        baseline: set[Fact] = set()
        snapshots = []
        for object_id in plan.object_ids:
            snapshot = self.objects.snapshot(
                tenant_id=request.tenant_id,
                object_type_id=plan.target_object_type_id,
                object_id=object_id,
                valid_at=request.valid_at,
                known_at=request.known_at,
            )
            snapshots.append(
                {
                    "object_id": object_id,
                    "snapshot_digest": snapshot["snapshot_digest"],
                    "evidence": snapshot["evidence"],
                }
            )
            for field, predicate in request.field_predicates.items():
                if field in snapshot["values"]:
                    baseline.add(
                        (predicate, (object_id, _term(snapshot["values"][field])))
                    )
        candidate = (baseline | set(request.assertions)) - set(request.retractions)
        engine = DatalogEngine(request.program, ruleset_id=request.ruleset_id)
        baseline_result = engine.run(baseline)
        candidate_result = engine.run(candidate)
        baseline_outcomes = self._outcomes(
            baseline_result["derived_facts"], request.outcome_predicates
        )
        candidate_outcomes = self._outcomes(
            candidate_result["derived_facts"], request.outcome_predicates
        )
        before, after = _fact_set(baseline_outcomes), _fact_set(candidate_outcomes)
        added, removed = after - before, before - after
        affected = sorted(
            {
                fact[1][0]
                for fact in added | removed
                if fact[1] and fact[1][0] in plan.object_ids
            }
        )
        impact = {
            "added": sorted(_label(item) for item in added),
            "removed": sorted(_label(item) for item in removed),
            "affected_object_ids": affected,
        }
        state = (
            "blocked"
            if any(item[0] in request.blocking_predicates for item in after)
            else "review"
        )
        identity = content_digest(
            {
                "tenant_id": request.tenant_id,
                "simulation_id": request.simulation_id,
                "request_digest": request.request_digest,
                "snapshots": snapshots,
            }
        ).split(":", 1)[1][:24]
        decision_id = f"decision:simulation:simulation-{identity}"
        payload = {
            "run_id": f"simulation-{identity}",
            "tenant_id": request.tenant_id,
            "simulation_id": request.simulation_id,
            "request": request.to_dict(),
            "actor": _text(actor, "actor"),
            "snapshot_evidence": tuple(snapshots),
            "baseline_facts": tuple(_fact_dict(item) for item in sorted(baseline)),
            "baseline_outcomes": baseline_outcomes,
            "candidate_outcomes": candidate_outcomes,
            "impact": impact,
            "state": state,
            "baseline_result_hash": baseline_result["result_hash"],
            "candidate_result_hash": candidate_result["result_hash"],
            "audit_decision_id": decision_id,
        }
        run = SimulationRun(**payload, run_digest=content_digest(payload))
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO simulation_runs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.tenant_id,
                    run.simulation_id,
                    request.request_digest,
                    run.run_digest,
                    canonical_json(run.to_dict()),
                ),
            )
        self._record_decision(run)
        return run

    def replay(self, run_id: str, *, tenant_id: str) -> dict[str, Any]:
        run = self.get(run_id, tenant_id=tenant_id)
        request = SimulationRequest.from_dict(run.request)
        baseline = _fact_set(run.baseline_facts)
        candidate = (baseline | set(request.assertions)) - set(request.retractions)
        engine = DatalogEngine(request.program, ruleset_id=request.ruleset_id)
        before = engine.run(baseline)["result_hash"]
        after = engine.run(candidate)["result_hash"]
        return {
            "source_run_id": run.run_id,
            "source_run_digest": run.run_digest,
            "baseline_result_hash": before,
            "candidate_result_hash": after,
            "reproduced": (
                before == run.baseline_result_hash
                and after == run.candidate_result_hash
            ),
        }

    def get(self, run_id: str, *, tenant_id: str) -> SimulationRun:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM simulation_runs WHERE run_id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
        if row is None:
            raise SimulationError(f"simulation run not found: {run_id}")
        return SimulationRun.from_dict(json.loads(row[0]))

    def list_runs(self, *, tenant_id: str) -> list[SimulationRun]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM simulation_runs WHERE tenant_id=? ORDER BY rowid DESC",
                (tenant_id,),
            ).fetchall()
        return [SimulationRun.from_dict(json.loads(row[0])) for row in rows]

    def verify_all(self) -> dict[str, Any]:
        errors = []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT run_id, run_digest, payload FROM simulation_runs"
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"sqlite integrity check failed: {integrity}")
        for run_id, digest, raw in rows:
            try:
                run = SimulationRun.from_dict(json.loads(raw))
                if run.run_id != run_id or run.run_digest != digest:
                    raise SimulationError("indexed simulation identity mismatch")
            except Exception as exc:
                errors.append(f"run/{run_id}: {exc}")
        return {"valid": not errors, "simulation_run_count": len(rows), "errors": errors}

    def backup_to(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self._connect()
        backup = sqlite3.connect(target)
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()
        return target

    def _record_decision(self, run: SimulationRun) -> None:
        if self.decisions.get(run.audit_decision_id) is not None:
            return
        self.decisions.record(
            decision_id=run.audit_decision_id,
            agent_id=run.actor,
            decision_type="bitemporal_counterfactual_simulation",
            conclusion=run.state,
            rationale="Compare baseline and candidate deterministic inference without invoking an action connector.",
            evidence=[
                {
                    "id": str(run.request["action_plan"]["plan_digest"]),
                    "type": "action_plan",
                    "content_hash": str(run.request["action_plan"]["plan_digest"]),
                },
                *[
                    {
                        "id": item["object_id"],
                        "type": "bitemporal_object_snapshot",
                        "content_hash": item["snapshot_digest"],
                    }
                    for item in run.snapshot_evidence
                ],
            ],
            output_entities=[
                {
                    "id": run.run_id,
                    "type": "simulation_run",
                    "content_hash": run.run_digest,
                }
            ],
            tenant_id=run.tenant_id,
            policies=["policy:counterfactual-impact"],
            tags=["simulation", "bitemporal", run.state],
            metadata={"impact": canonical_data(run.impact)},
        )

    @staticmethod
    def _outcomes(
        derived: Sequence[Mapping[str, Any]], predicates: Sequence[str]
    ) -> tuple[Mapping[str, Any], ...]:
        wanted = set(predicates)
        return tuple(
            item
            for item in derived
            if str(item.get("predicate")) in wanted
        )
