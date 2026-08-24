"""Persistent truth-maintained Datalog runs with assertion and retraction deltas."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.ontology_governance.reasoning import DatalogEngine

from .canonical import canonical_data, canonical_json, content_digest


class IncrementalReasoningError(ValueError):
    """Raised when a persisted reasoning transition cannot be trusted."""


Fact = tuple[str, tuple[str, ...]]


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IncrementalReasoningError(f"{field} must be a non-empty string")
    return value.strip()


def _instant(value: Any, field: str) -> str:
    raw = _text(value, field).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise IncrementalReasoningError(f"{field} must be an ISO-8601 instant") from exc
    if parsed.tzinfo is None:
        raise IncrementalReasoningError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _fact(value: tuple[str, Iterable[str]]) -> Fact:
    predicate = _text(value[0], "fact predicate")
    terms = tuple(_text(str(item), "fact term") for item in value[1])
    if not terms:
        raise IncrementalReasoningError("fact requires at least one term")
    return predicate, terms


def _fact_label(value: Fact) -> str:
    return f"{value[0]}({','.join(value[1])})"


def _fact_json(value: Fact) -> dict[str, Any]:
    payload = {"predicate": value[0], "terms": list(value[1])}
    return {
        **payload,
        "fact_id": "fact-" + content_digest(payload).split(":", 1)[1][:24],
    }


@dataclass(frozen=True)
class ReasoningFactChange:
    change_id: str
    tenant_id: str
    effective_at: str
    assertions: tuple[Fact, ...]
    retractions: tuple[Fact, ...]
    source: Mapping[str, Any]
    change_digest: str

    @classmethod
    def create(
        cls,
        *,
        change_id: str,
        tenant_id: str,
        effective_at: str | None = None,
        assertions: Sequence[tuple[str, Iterable[str]]] = (),
        retractions: Sequence[tuple[str, Iterable[str]]] = (),
        source: Mapping[str, Any],
    ) -> "ReasoningFactChange":
        added = tuple(sorted({_fact(item) for item in assertions}))
        removed = tuple(sorted({_fact(item) for item in retractions}))
        if set(added) & set(removed):
            raise IncrementalReasoningError(
                "one change cannot assert and retract the same fact"
            )
        if not added and not removed:
            raise IncrementalReasoningError("reasoning change cannot be empty")
        if not isinstance(source, Mapping) or not source:
            raise IncrementalReasoningError("source must be a non-empty semantic object")
        normalized_source = canonical_data(source)
        source_time = normalized_source.get("occurred_at")
        ordered_at = effective_at or (
            source_time if isinstance(source_time, str) else "9999-12-31T23:59:59+00:00"
        )
        payload = {
            "change_id": _text(change_id, "change_id"),
            "tenant_id": _text(tenant_id, "tenant_id"),
            "effective_at": _instant(ordered_at, "effective_at"),
            "assertions": [_fact_json(item) for item in added],
            "retractions": [_fact_json(item) for item in removed],
            "source": normalized_source,
        }
        return cls(
            change_id=payload["change_id"],
            tenant_id=payload["tenant_id"],
            effective_at=payload["effective_at"],
            assertions=added,
            retractions=removed,
            source=payload["source"],
            change_digest=content_digest(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(
            {
                "change_id": self.change_id,
                "tenant_id": self.tenant_id,
                "effective_at": self.effective_at,
                "assertions": [_fact_json(item) for item in self.assertions],
                "retractions": [_fact_json(item) for item in self.retractions],
                "source": self.source,
                "change_digest": self.change_digest,
            }
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReasoningFactChange":
        change = cls.create(
            change_id=str(value.get("change_id", "")),
            tenant_id=str(value.get("tenant_id", "")),
            effective_at=str(value.get("effective_at", "")),
            assertions=[
                (item["predicate"], item["terms"])
                for item in value.get("assertions", ())
            ],
            retractions=[
                (item["predicate"], item["terms"])
                for item in value.get("retractions", ())
            ],
            source=value.get("source", {}),
        )
        if value.get("change_digest") != change.change_digest:
            raise IncrementalReasoningError("reasoning change digest mismatch")
        return change


@dataclass(frozen=True)
class IncrementalReasoningRun:
    run_id: str
    tenant_id: str
    ruleset_id: str
    ruleset_version: str
    change_id: str
    change_digest: str
    actor: str
    asserted_facts: tuple[Mapping[str, Any], ...]
    derived_facts: tuple[Mapping[str, Any], ...]
    delta: Mapping[str, Any]
    result_hash: str
    status: str
    audit_decision_id: str
    run_digest: str

    def to_dict(self) -> dict[str, Any]:
        return canonical_data(self.__dict__)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IncrementalReasoningRun":
        payload = dict(value)
        payload["asserted_facts"] = tuple(payload["asserted_facts"])
        payload["derived_facts"] = tuple(payload["derived_facts"])
        run = cls(**payload)
        expected = content_digest(
            {key: item for key, item in run.to_dict().items() if key != "run_digest"}
        )
        if run.run_digest != expected:
            raise IncrementalReasoningError("reasoning run digest mismatch")
        return run


class SqliteIncrementalReasoningRuntime:
    """Apply immutable fact changes and persist the materialized deterministic closure."""

    def __init__(
        self,
        path: str | Path,
        *,
        decision_store: DecisionProvenanceStore,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.decisions = decision_store
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reasoning_state (
                    tenant_id TEXT NOT NULL,
                    ruleset_id TEXT NOT NULL,
                    ruleset_version TEXT NOT NULL,
                    state_digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, ruleset_id)
                );
                CREATE TABLE IF NOT EXISTS reasoning_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    ruleset_id TEXT NOT NULL,
                    change_id TEXT NOT NULL,
                    change_digest TEXT NOT NULL,
                    run_digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE (tenant_id, ruleset_id, change_id)
                );
                CREATE TABLE IF NOT EXISTS reasoning_changes (
                    tenant_id TEXT NOT NULL,
                    ruleset_id TEXT NOT NULL,
                    change_id TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    change_digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, ruleset_id, change_id)
                );
                PRAGMA user_version=2;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def apply(
        self,
        *,
        ruleset_id: str,
        program: str,
        change: ReasoningFactChange,
        actor: str,
    ) -> IncrementalReasoningRun:
        ruleset = _text(ruleset_id, "ruleset_id")
        engine = DatalogEngine(program, ruleset_id=ruleset)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT change_digest, payload FROM reasoning_runs "
                "WHERE tenant_id=? AND ruleset_id=? AND change_id=?",
                (change.tenant_id, ruleset, change.change_id),
            ).fetchone()
            if existing is not None:
                if existing[0] != change.change_digest:
                    raise IncrementalReasoningError(
                        "change_id is already bound to different fact content"
                    )
                connection.commit()
                run = IncrementalReasoningRun.from_dict(json.loads(existing[1]))
                self._record_decision(run, change)
                return run
            row = connection.execute(
                "SELECT ruleset_version, payload FROM reasoning_state "
                "WHERE tenant_id=? AND ruleset_id=?",
                (change.tenant_id, ruleset),
            ).fetchone()
            if row is None:
                previous_asserted: set[Fact] = set()
                previous_derived: set[Fact] = set()
            else:
                if row[0] != engine.ruleset_version:
                    raise IncrementalReasoningError(
                        "ruleset_id cannot change its program version"
                    )
                state = json.loads(row[1])
                previous_asserted = self._facts(state["asserted_facts"])
                previous_derived = self._facts(state["derived_facts"])
            change_rows = connection.execute(
                "SELECT payload FROM reasoning_changes WHERE tenant_id=? AND ruleset_id=?",
                (change.tenant_id, ruleset),
            ).fetchall()
            changes = [
                ReasoningFactChange.from_dict(json.loads(item[0]))
                for item in change_rows
            ]
            changes.append(change)
            asserted: set[Fact] = set()
            for ordered in sorted(
                changes, key=lambda item: (item.effective_at, item.change_id)
            ):
                asserted.update(ordered.assertions)
                asserted.difference_update(ordered.retractions)
            inference = engine.run(asserted)
            derived_payload = tuple(inference["derived_facts"])
            derived = self._facts(derived_payload)
            asserted_payload = tuple(_fact_json(item) for item in sorted(asserted))
            delta = {
                "asserted_added": sorted(
                    _fact_label(item) for item in asserted - previous_asserted
                ),
                "asserted_removed": sorted(
                    _fact_label(item) for item in previous_asserted - asserted
                ),
                "derived_added": sorted(
                    _fact_label(item) for item in derived - previous_derived
                ),
                "derived_removed": sorted(
                    _fact_label(item) for item in previous_derived - derived
                ),
            }
            identity = content_digest(
                {
                    "tenant_id": change.tenant_id,
                    "ruleset_id": ruleset,
                    "ruleset_version": engine.ruleset_version,
                    "change_digest": change.change_digest,
                    "input_snapshot_hash": inference["input_snapshot_hash"],
                }
            ).split(":", 1)[1][:24]
            decision_id = f"decision:reasoning:reason-{identity}"
            payload = {
                "run_id": f"reason-{identity}",
                "tenant_id": change.tenant_id,
                "ruleset_id": ruleset,
                "ruleset_version": engine.ruleset_version,
                "change_id": change.change_id,
                "change_digest": change.change_digest,
                "actor": _text(actor, "actor"),
                "asserted_facts": asserted_payload,
                "derived_facts": derived_payload,
                "delta": delta,
                "result_hash": inference["result_hash"],
                "status": "succeeded",
                "audit_decision_id": decision_id,
            }
            run = IncrementalReasoningRun(
                **payload, run_digest=content_digest(payload)
            )
            state_payload = {
                "tenant_id": change.tenant_id,
                "ruleset_id": ruleset,
                "ruleset_version": engine.ruleset_version,
                "program": program,
                "asserted_facts": asserted_payload,
                "derived_facts": derived_payload,
                "result_hash": inference["result_hash"],
            }
            state = {
                **state_payload,
                "state_digest": content_digest(state_payload),
            }
            connection.execute(
                "INSERT OR REPLACE INTO reasoning_state VALUES (?, ?, ?, ?, ?)",
                (
                    change.tenant_id,
                    ruleset,
                    engine.ruleset_version,
                    state["state_digest"],
                    canonical_json(state),
                ),
            )
            connection.execute(
                "INSERT INTO reasoning_changes VALUES (?, ?, ?, ?, ?, ?)",
                (
                    change.tenant_id,
                    ruleset,
                    change.change_id,
                    change.effective_at,
                    change.change_digest,
                    canonical_json(change.to_dict()),
                ),
            )
            connection.execute(
                "INSERT INTO reasoning_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.tenant_id,
                    run.ruleset_id,
                    run.change_id,
                    run.change_digest,
                    run.run_digest,
                    canonical_json(run.to_dict()),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self._record_decision(run, change)
        return run

    def query(
        self, *, tenant_id: str, ruleset_id: str, predicate: str | None = None
    ) -> list[dict[str, Any]]:
        state = self._state(tenant_id=tenant_id, ruleset_id=ruleset_id)
        facts = [*state["asserted_facts"], *state["derived_facts"]]
        if predicate is not None:
            facts = [item for item in facts if item["predicate"] == predicate]
        return sorted(facts, key=lambda item: (item["predicate"], item["terms"]))

    def explain(
        self, *, tenant_id: str, ruleset_id: str, fact_id: str
    ) -> dict[str, Any]:
        matches = [
            item
            for item in self.query(tenant_id=tenant_id, ruleset_id=ruleset_id)
            if item["fact_id"] == fact_id
        ]
        if not matches:
            raise IncrementalReasoningError(f"reasoning fact not found: {fact_id}")
        return matches[0]

    def replay(self, run_id: str, *, tenant_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM reasoning_runs WHERE run_id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
            state_row = connection.execute(
                "SELECT payload FROM reasoning_state WHERE tenant_id=? AND ruleset_id=("
                "SELECT ruleset_id FROM reasoning_runs WHERE run_id=? AND tenant_id=?"
                ")",
                (tenant_id, run_id, tenant_id),
            ).fetchone()
        if row is None or state_row is None:
            raise IncrementalReasoningError(f"reasoning run not found: {run_id}")
        run = IncrementalReasoningRun.from_dict(json.loads(row[0]))
        state = json.loads(state_row[0])
        asserted = self._facts(run.asserted_facts)
        replay = DatalogEngine(
            state["program"], ruleset_id=run.ruleset_id
        ).run(asserted)
        return {
            "source_run_id": run.run_id,
            "source_run_digest": run.run_digest,
            "replayed_result_hash": replay["result_hash"],
            "reproduced": replay["result_hash"] == run.result_hash,
        }

    def get_run(self, run_id: str, *, tenant_id: str) -> IncrementalReasoningRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM reasoning_runs WHERE run_id=? AND tenant_id=?",
                (run_id, tenant_id),
            ).fetchone()
        if row is None:
            raise IncrementalReasoningError(f"reasoning run not found: {run_id}")
        return IncrementalReasoningRun.from_dict(json.loads(row[0]))

    def list_runs(self, *, tenant_id: str) -> list[IncrementalReasoningRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM reasoning_runs WHERE tenant_id=? ORDER BY rowid DESC",
                (tenant_id,),
            ).fetchall()
        return [IncrementalReasoningRun.from_dict(json.loads(row[0])) for row in rows]

    def verify_all(self) -> dict[str, Any]:
        errors: list[str] = []
        with self._connect() as connection:
            states = connection.execute(
                "SELECT tenant_id, ruleset_id, state_digest, payload FROM reasoning_state"
            ).fetchall()
            runs = connection.execute(
                "SELECT run_id, run_digest, payload FROM reasoning_runs"
            ).fetchall()
            changes = connection.execute(
                "SELECT tenant_id, ruleset_id, change_id, change_digest, payload "
                "FROM reasoning_changes"
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"sqlite integrity check failed: {integrity}")
        for tenant_id, ruleset_id, digest, raw in states:
            value = json.loads(raw)
            payload = {key: item for key, item in value.items() if key != "state_digest"}
            if value.get("state_digest") != digest or content_digest(payload) != digest:
                errors.append(f"state/{tenant_id}/{ruleset_id}")
        for run_id, digest, raw in runs:
            value = json.loads(raw)
            payload = {key: item for key, item in value.items() if key != "run_digest"}
            if value.get("run_digest") != digest or content_digest(payload) != digest:
                errors.append(f"run/{run_id}")
        for tenant_id, ruleset_id, change_id, digest, raw in changes:
            try:
                change = ReasoningFactChange.from_dict(json.loads(raw))
                if change.change_id != change_id or change.change_digest != digest:
                    raise IncrementalReasoningError("indexed change identity mismatch")
            except Exception as exc:
                errors.append(f"change/{tenant_id}/{ruleset_id}/{change_id}: {exc}")
        return {
            "valid": not errors,
            "state_count": len(states),
            "run_count": len(runs),
            "change_count": len(changes),
            "errors": errors,
        }

    def _state(self, *, tenant_id: str, ruleset_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_digest, payload FROM reasoning_state "
                "WHERE tenant_id=? AND ruleset_id=?",
                (tenant_id, ruleset_id),
            ).fetchone()
        if row is None:
            raise IncrementalReasoningError(
                f"reasoning state not found: {ruleset_id}"
            )
        state = json.loads(row[1])
        payload = {key: item for key, item in state.items() if key != "state_digest"}
        if state.get("state_digest") != row[0] or content_digest(payload) != row[0]:
            raise IncrementalReasoningError("reasoning state digest mismatch")
        return state

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

    def _record_decision(
        self, run: IncrementalReasoningRun, change: ReasoningFactChange
    ) -> None:
        if self.decisions.get(run.audit_decision_id) is not None:
            return
        self.decisions.record(
            decision_id=run.audit_decision_id,
            agent_id=run.actor,
            decision_type="incremental_deterministic_inference",
            conclusion=(
                f"applied {run.change_id}: "
                f"+{len(run.delta['derived_added'])}/-{len(run.delta['derived_removed'])} derived facts"
            ),
            rationale="A fact change was atomically applied and the release-pinned Datalog closure was truth-maintained.",
            evidence=[
                {
                    "id": change.change_id,
                    "type": "reasoning_fact_change",
                    "content_hash": change.change_digest,
                },
                {
                    "id": f"ruleset:{run.ruleset_id}:{run.ruleset_version}",
                    "type": "datalog_ruleset",
                    "content_hash": run.ruleset_version,
                },
            ],
            output_entities=[
                {
                    "id": run.run_id,
                    "type": "incremental_reasoning_run",
                    "content_hash": run.run_digest,
                }
            ],
            tenant_id=run.tenant_id,
            policies=["policy:deterministic-reasoning"],
            tags=["datalog", "incremental", "truth-maintenance"],
            metadata={"delta": canonical_data(run.delta), "source": change.source},
        )

    @staticmethod
    def _facts(values: Iterable[Mapping[str, Any]]) -> set[Fact]:
        return {
            (str(item["predicate"]), tuple(str(term) for term in item["terms"]))
            for item in values
        }
