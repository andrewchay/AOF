# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Deterministic, versioned Datalog reasoning and read-only SPARQL queries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from rdflib import Graph

from bridge.decision_provenance import DecisionProvenanceStore


class DatalogError(ValueError):
    """Raised for unsafe, invalid, or unstratifiable rule programs."""


@dataclass(frozen=True)
class Atom:
    predicate: str
    terms: tuple[str, ...]
    negated: bool = False


@dataclass(frozen=True)
class Rule:
    rule_id: str
    head: Atom
    body: tuple[Atom, ...]


def _split_top_level(text: str, delimiter: str = ",") -> list[str]:
    values, current, depth, quote = [], [], 0, None
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == delimiter and depth == 0:
            values.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        values.append("".join(current).strip())
    return values


def _is_variable(term: str) -> bool:
    return bool(term) and (term[0].isupper() or term[0] == "_")


def _constant(term: str) -> str:
    term = term.strip()
    if len(term) >= 2 and term[0] == term[-1] and term[0] in {'"', "'"}:
        return term[1:-1]
    return term


class DatalogEngine:
    """Safe stratified Datalog with deterministic fixed-point evaluation."""

    ATOM = re.compile(r"^([A-Za-z_][A-Za-z0-9_:-]*)\s*\((.*)\)$")

    def __init__(self, program: str, *, ruleset_id: str = "ruleset:default") -> None:
        self.program = program
        self.ruleset_id = ruleset_id
        self.rules = self._parse(program)
        self.ruleset_version = hashlib.sha256(program.encode("utf-8")).hexdigest()
        self.strata = self._stratify(self.rules)

    def run(
        self,
        facts: Iterable[tuple[str, Iterable[str]]],
        *,
        decision_store: DecisionProvenanceStore | None = None,
        agent_id: str = "engine:datalog",
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        fact_set = {
            (predicate, tuple(str(value) for value in values))
            for predicate, values in facts
        }
        initial = set(fact_set)
        proofs: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {
            fact: {"kind": "asserted", "fact": self._fact_json(fact)}
            for fact in fact_set
        }
        max_stratum = max(self.strata.values(), default=0)
        for stratum in range(max_stratum + 1):
            active = [
                rule
                for rule in self.rules
                if self.strata[rule.head.predicate] == stratum
            ]
            changed = True
            while changed:
                changed = False
                for rule in active:
                    for binding, inputs in self._bindings(rule.body, fact_set):
                        terms = tuple(
                            binding.get(term, _constant(term))
                            for term in rule.head.terms
                        )
                        fact = (rule.head.predicate, terms)
                        if fact not in fact_set:
                            fact_set.add(fact)
                            changed = True
                            proofs[fact] = {
                                "kind": "derived",
                                "rule_id": rule.rule_id,
                                "ruleset_id": self.ruleset_id,
                                "ruleset_version": self.ruleset_version,
                                "bindings": dict(sorted(binding.items())),
                                "inputs": [self._fact_json(item) for item in inputs],
                            }
        derived = sorted(fact_set - initial)
        input_snapshot_hash = hashlib.sha256(
            json.dumps(
                [self._fact_json(item) for item in sorted(initial)],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        result = {
            "ruleset_id": self.ruleset_id,
            "ruleset_version": self.ruleset_version,
            "asserted_facts": [self._fact_json(item) for item in sorted(initial)],
            "derived_facts": [
                {**self._fact_json(item), "proof": proofs[item]} for item in derived
            ],
            "fact_count": len(fact_set),
            "derived_count": len(derived),
            "input_snapshot_hash": input_snapshot_hash,
        }
        result["result_hash"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if decision_store is not None:
            decision = decision_store.record(
                agent_id=agent_id,
                decision_type="deterministic_inference",
                conclusion=f"derived {len(derived)} fact(s)",
                rationale="Stratified Datalog reached a deterministic fixed point.",
                evidence=(evidence or [])
                + [
                    {
                        "id": f"ruleset:{self.ruleset_id}:{self.ruleset_version}",
                        "type": "datalog_ruleset",
                        "content_hash": self.ruleset_version,
                    },
                    {
                        "id": f"facts:{input_snapshot_hash}",
                        "type": "fact_snapshot",
                        "content_hash": input_snapshot_hash,
                    },
                ],
                policies=["policy:deterministic-reasoning"],
                tags=["datalog", "inference"],
                output_entities=[
                    {
                        "id": f"inference:{result['result_hash']}",
                        "type": "derived_fact_set",
                        "content_hash": result["result_hash"],
                        "count": len(derived),
                    }
                ],
                metadata={"derived_facts": result["derived_facts"]},
            )
            result["decision_id"] = decision["decision"]["id"]
        return result

    def _bindings(
        self, body: tuple[Atom, ...], facts: set[tuple[str, tuple[str, ...]]]
    ) -> list[tuple[dict[str, str], list[tuple[str, tuple[str, ...]]]]]:
        states: list[tuple[dict[str, str], list[tuple[str, tuple[str, ...]]]]] = [
            ({}, [])
        ]
        ordered_body = tuple(atom for atom in body if not atom.negated) + tuple(
            atom for atom in body if atom.negated
        )
        for atom in ordered_body:
            next_states = []
            candidates = sorted(
                fact
                for fact in facts
                if fact[0] == atom.predicate and len(fact[1]) == len(atom.terms)
            )
            for binding, inputs in states:
                if atom.negated:
                    if not any(
                        self._unify(atom, fact[1], binding) is not None
                        for fact in candidates
                    ):
                        next_states.append((binding, inputs))
                    continue
                for fact in candidates:
                    unified = self._unify(atom, fact[1], binding)
                    if unified is not None:
                        next_states.append((unified, inputs + [fact]))
            states = next_states
        return states

    def _unify(
        self, atom: Atom, values: tuple[str, ...], binding: dict[str, str]
    ) -> dict[str, str] | None:
        result = dict(binding)
        for term, value in zip(atom.terms, values):
            if _is_variable(term):
                if term in result and result[term] != value:
                    return None
                result[term] = value
            elif _constant(term) != value:
                return None
        return result

    def _parse(self, program: str) -> list[Rule]:
        rules = []
        for line_number, raw in enumerate(program.splitlines(), start=1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if not line.endswith("."):
                raise DatalogError(f"line {line_number}: rule must end with '.'")
            statement = line[:-1].strip()
            if ":-" in statement:
                head_text, body_text = statement.split(":-", 1)
                body = tuple(
                    self._parse_atom(part, line_number)
                    for part in _split_top_level(body_text)
                )
            else:
                head_text, body = statement, ()
            head = self._parse_atom(head_text, line_number)
            if head.negated:
                raise DatalogError(f"line {line_number}: rule head cannot be negated")
            if not body:
                raise DatalogError(
                    f"line {line_number}: put asserted facts in the facts input, not the rule program"
                )
            positive_variables = {
                term
                for atom in body
                if not atom.negated
                for term in atom.terms
                if _is_variable(term)
            }
            required = {term for term in head.terms if _is_variable(term)} | {
                term
                for atom in body
                if atom.negated
                for term in atom.terms
                if _is_variable(term)
            }
            if not required.issubset(positive_variables):
                raise DatalogError(
                    f"line {line_number}: unsafe variable(s): {sorted(required - positive_variables)}"
                )
            rules.append(Rule(f"{self.ruleset_id}:rule-{line_number}", head, body))
        if not rules:
            raise DatalogError("rule program is empty")
        return rules

    def _parse_atom(self, text: str, line_number: int) -> Atom:
        text = text.strip()
        negated = text.startswith("not ")
        if negated:
            text = text[4:].strip()
        match = self.ATOM.match(text)
        if not match:
            raise DatalogError(f"line {line_number}: invalid atom: {text}")
        terms = tuple(item.strip() for item in _split_top_level(match.group(2)))
        if not terms or any(not item for item in terms):
            raise DatalogError(f"line {line_number}: atom requires terms")
        return Atom(match.group(1), terms, negated)

    @staticmethod
    def _stratify(rules: list[Rule]) -> dict[str, int]:
        predicates = {rule.head.predicate for rule in rules} | {
            atom.predicate for rule in rules for atom in rule.body
        }
        strata = {predicate: 0 for predicate in predicates}
        for _ in range(len(predicates) + 1):
            changed = False
            for rule in rules:
                for atom in rule.body:
                    required = strata[atom.predicate] + (1 if atom.negated else 0)
                    if strata[rule.head.predicate] < required:
                        strata[rule.head.predicate] = required
                        changed = True
            if not changed:
                return strata
        raise DatalogError("rule program is not stratifiable (cycle through negation)")

    @staticmethod
    def _fact_json(fact: tuple[str, tuple[str, ...]]) -> dict[str, Any]:
        payload = {"predicate": fact[0], "terms": list(fact[1])}
        payload["fact_id"] = (
            "fact:"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:20]
        )
        return payload


class RuleSetRepository:
    """Content-addressed immutable Datalog rule-set versions."""

    def __init__(
        self, root, decision_store: DecisionProvenanceStore | None = None
    ) -> None:
        from pathlib import Path

        self.root = Path(root)
        self.decision_store = decision_store or DecisionProvenanceStore(
            self.root / "decision_provenance.jsonl"
        )

    def publish(
        self, *, ruleset_id: str, program: str, actor: str, description: str = ""
    ) -> dict[str, Any]:
        if not ruleset_id or any(value in ruleset_id for value in ("/", "\\", "..")):
            raise DatalogError("invalid ruleset_id")
        engine = DatalogEngine(program, ruleset_id=ruleset_id)
        version = f"rules-{engine.ruleset_version[:12]}"
        release_dir = self.root / ruleset_id / version
        if release_dir.exists():
            raise DatalogError(
                f"rule-set version already exists: {ruleset_id}/{version}"
            )
        release_dir.mkdir(parents=True)
        (release_dir / "program.dl").write_text(program, encoding="utf-8")
        manifest = {
            "ruleset_id": ruleset_id,
            "ruleset_version": version,
            "content_hash": engine.ruleset_version,
            "description": description,
            "rule_count": len(engine.rules),
            "strata": engine.strata,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "published_by": actor,
        }
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="datalog_ruleset_publish",
            conclusion=f"published {version}",
            rationale=description
            or "Validated safe stratified Datalog rules were published immutably.",
            evidence=[
                {
                    "id": f"ruleset-source:{engine.ruleset_version}",
                    "type": "datalog_source",
                    "content_hash": engine.ruleset_version,
                }
            ],
            policies=["policy:deterministic-reasoning"],
            tags=["datalog", "ruleset", "publish"],
            output_entities=[
                {
                    "id": f"ruleset:{ruleset_id}:{version}",
                    "type": "datalog_ruleset",
                    "content_hash": engine.ruleset_version,
                }
            ],
        )
        manifest["publish_decision_id"] = decision["decision"]["id"]
        (release_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return manifest

    def get(self, ruleset_id: str, version: str) -> dict[str, Any]:
        if any(value in ruleset_id or value in version for value in ("/", "\\", "..")):
            raise DatalogError("invalid rule-set id or version")
        release_dir = self.root / ruleset_id / version
        manifest = release_dir / "manifest.json"
        if not manifest.exists():
            raise DatalogError(f"rule-set version not found: {ruleset_id}/{version}")
        return {
            "manifest": json.loads(manifest.read_text(encoding="utf-8")),
            "program": (release_dir / "program.dl").read_text(encoding="utf-8"),
        }

    def list(self, ruleset_id: str | None = None) -> list[dict[str, Any]]:
        paths = (
            self.root.glob(f"{ruleset_id}/*/manifest.json")
            if ruleset_id
            else self.root.glob("*/*/manifest.json")
        )
        values = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        return sorted(values, key=lambda item: item["published_at"], reverse=True)

    def run(
        self,
        ruleset_id: str,
        version: str,
        facts: Iterable[tuple[str, Iterable[str]]],
        *,
        agent_id: str = "engine:datalog",
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        release = self.get(ruleset_id, version)
        return DatalogEngine(release["program"], ruleset_id=ruleset_id).run(
            facts,
            decision_store=self.decision_store,
            agent_id=agent_id,
            evidence=(evidence or [])
            + [
                {
                    "id": f"ruleset:{ruleset_id}:{version}",
                    "type": "datalog_ruleset",
                    "content_hash": release["manifest"]["content_hash"],
                }
            ],
        )


class SparqlService:
    """Read-only SPARQL SELECT/ASK/CONSTRUCT/DESCRIBE over an immutable RDF snapshot."""

    UPDATE_PATTERN = re.compile(
        r"\b(INSERT|DELETE|LOAD|CLEAR|CREATE|DROP|COPY|MOVE|ADD|WITH)\b", re.I
    )

    def __init__(
        self,
        rdf_text: str,
        *,
        rdf_format: str = "turtle",
        snapshot_id: str = "snapshot:adhoc",
    ) -> None:
        self.graph = Graph()
        self.graph.parse(data=rdf_text, format=rdf_format)
        self.snapshot_id = snapshot_id

    def query(self, sparql: str) -> dict[str, Any]:
        if self.UPDATE_PATTERN.search(sparql):
            raise ValueError(
                "SPARQL Update is not allowed; published snapshots are immutable"
            )
        result = self.graph.query(sparql)
        query_hash = hashlib.sha256(sparql.encode("utf-8")).hexdigest()
        if result.type == "ASK":
            return {
                "type": "ASK",
                "boolean": bool(result.askAnswer),
                "snapshot_id": self.snapshot_id,
                "query_hash": query_hash,
            }
        if result.type in {"CONSTRUCT", "DESCRIBE"}:
            return {
                "type": result.type,
                "turtle": result.graph.serialize(format="turtle"),
                "snapshot_id": self.snapshot_id,
                "query_hash": query_hash,
            }
        variables = [str(item) for item in result.vars]
        rows = [
            {
                variables[index]: None if value is None else str(value)
                for index, value in enumerate(row)
            }
            for row in result
        ]
        return {
            "type": "SELECT",
            "variables": variables,
            "rows": rows,
            "count": len(rows),
            "snapshot_id": self.snapshot_id,
            "query_hash": query_hash,
        }
