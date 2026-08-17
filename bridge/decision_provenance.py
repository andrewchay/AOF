"""Append-only, PROV-O-inspired provenance for Agent decisions.

The module deliberately keeps decision provenance separate from request audit logs:
an audit log answers *what endpoint was called*, while this ledger answers *why an
agent reached a conclusion, what it used, and what later work it influenced*.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROV_CONTEXT = {
    "prov": "http://www.w3.org/ns/prov#",
    "aof": "https://aof.dev/ns/provenance#",
    "Decision": "aof:Decision",
    "Agent": "prov:Agent",
    "Entity": "prov:Entity",
    "used": {"@id": "prov:used", "@type": "@id"},
    "generated": {"@id": "prov:generated", "@type": "@id"},
    "wasInformedBy": {"@id": "prov:wasInformedBy", "@type": "@id"},
}


class DecisionProvenanceError(ValueError):
    """Raised when a provenance record cannot be safely created or read."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceRef:
    """An immutable reference to input evidence (a PROV Entity)."""

    id: str
    type: str = "evidence"
    uri: str | None = None
    content_hash: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionRecord:
    """A decision Activity with the minimum information needed for an audit."""

    id: str
    recorded_at: str
    agent_id: str
    decision_type: str
    conclusion: str
    rationale: str
    status: str = "completed"
    tenant_id: str | None = None
    session_id: str | None = None
    evidence: list[EvidenceRef] = field(default_factory=list)
    parent_decision_ids: list[str] = field(default_factory=list)
    output_entities: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [asdict(item) for item in self.evidence]
        return data


class DecisionProvenanceStore:
    """JSONL ledger with a per-record hash chain and deterministic local queries."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = os.environ.get("AOF_DECISION_PROVENANCE_FILE")
        self.path = Path(path or configured or "data/audit/decision_provenance.jsonl")

    def record(
        self,
        *,
        agent_id: str,
        decision_type: str,
        conclusion: str,
        rationale: str,
        evidence: Iterable[dict[str, Any] | EvidenceRef] | None = None,
        parent_decision_ids: Iterable[str] | None = None,
        output_entities: Iterable[dict[str, Any]] | None = None,
        tags: Iterable[str] | None = None,
        policies: Iterable[str] | None = None,
        status: str = "completed",
        tenant_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (agent_id, decision_type, conclusion, rationale)
        ):
            raise DecisionProvenanceError(
                "agent_id, decision_type, conclusion, and rationale must be non-empty strings"
            )
        record_id = decision_id or f"decision:{uuid.uuid4()}"
        parents = list(dict.fromkeys(parent_decision_ids or []))
        if record_id in parents:
            raise DecisionProvenanceError("a decision cannot be its own parent")
        entries = self._entries()
        existing = {item["decision"]["id"] for item in entries}
        if record_id in existing:
            raise DecisionProvenanceError(f"decision already exists: {record_id}")
        unknown = sorted(set(parents) - existing)
        if unknown:
            raise DecisionProvenanceError(
                f"unknown parent decision(s): {', '.join(unknown)}"
            )
        refs = [
            item if isinstance(item, EvidenceRef) else EvidenceRef(**item)
            for item in (evidence or [])
        ]
        decision = DecisionRecord(
            id=record_id,
            recorded_at=_now(),
            agent_id=agent_id,
            decision_type=decision_type,
            conclusion=conclusion,
            rationale=rationale,
            status=status,
            tenant_id=tenant_id,
            session_id=session_id,
            evidence=refs,
            parent_decision_ids=parents,
            output_entities=list(output_entities or []),
            tags=sorted(set(tags or [])),
            policies=sorted(set(policies or [])),
            metadata=metadata or {},
        )
        previous_hash = entries[-1]["integrity"]["hash"] if entries else None
        payload = {"decision": decision.to_dict(), "previous_hash": previous_hash}
        entry = {
            **payload,
            "integrity": {"algorithm": "sha256", "hash": _hash(payload)},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_json(entry) + "\n")
        return entry

    def get(self, decision_id: str) -> dict[str, Any] | None:
        return next(
            (
                entry
                for entry in self._entries()
                if entry["decision"]["id"] == decision_id
            ),
            None,
        )

    def causal_chain(
        self, decision_id: str, direction: str = "ancestors", max_depth: int = 8
    ) -> dict[str, Any]:
        if direction not in {"ancestors", "descendants"}:
            raise DecisionProvenanceError("direction must be ancestors or descendants")
        if not 1 <= max_depth <= 50:
            raise DecisionProvenanceError("max_depth must be between 1 and 50")
        entries = self._entries()
        by_id = {entry["decision"]["id"]: entry for entry in entries}
        if decision_id not in by_id:
            raise DecisionProvenanceError(f"decision not found: {decision_id}")
        children: dict[str, list[str]] = {}
        for entry in entries:
            for parent in entry["decision"].get("parent_decision_ids", []):
                children.setdefault(parent, []).append(entry["decision"]["id"])
        queue = deque([(decision_id, 0)])
        visited = {decision_id}
        nodes, edges = [by_id[decision_id]], []
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            neighbors = (
                by_id[current]["decision"].get("parent_decision_ids", [])
                if direction == "ancestors"
                else children.get(current, [])
            )
            for other in sorted(neighbors):
                edges.append(
                    {
                        "from": current if direction == "descendants" else other,
                        "to": other if direction == "descendants" else current,
                        "type": "wasInformedBy",
                    }
                )
                if other not in visited and other in by_id:
                    visited.add(other)
                    nodes.append(by_id[other])
                    queue.append((other, depth + 1))
        return {
            "root_decision_id": decision_id,
            "direction": direction,
            "nodes": nodes,
            "edges": edges,
        }

    def find_precedents(
        self,
        decision_type: str,
        tags: Iterable[str] | None = None,
        tenant_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        wanted = set(tags or [])
        matches = []
        for entry in self._entries():
            decision = entry["decision"]
            if decision["decision_type"] != decision_type or (
                tenant_id and decision.get("tenant_id") != tenant_id
            ):
                continue
            overlap = wanted.intersection(decision.get("tags", []))
            score = len(overlap) / len(wanted) if wanted else 1.0
            matches.append(
                {
                    "decision": entry,
                    "similarity": score,
                    "matching_tags": sorted(overlap),
                }
            )
        return sorted(
            matches,
            key=lambda item: (
                item["similarity"],
                item["decision"]["decision"]["recorded_at"],
            ),
            reverse=True,
        )[:limit]

    def impact(self, decision_id: str, max_depth: int = 8) -> dict[str, Any]:
        chain = self.causal_chain(
            decision_id, direction="descendants", max_depth=max_depth
        )
        outputs = []
        for entry in chain["nodes"]:
            for entity in entry["decision"].get("output_entities", []):
                outputs.append(
                    {"decision_id": entry["decision"]["id"], "entity": entity}
                )
        return {
            **chain,
            "affected_entities": outputs,
            "affected_decision_count": max(0, len(chain["nodes"]) - 1),
        }

    def audit_trail(self, decision_id: str) -> dict[str, Any]:
        chain = self.causal_chain(decision_id, direction="ancestors", max_depth=50)
        integrity = self.verify_integrity()
        return {
            "@context": PROV_CONTEXT,
            "@id": decision_id,
            "@type": "Decision",
            "generated_at": _now(),
            "causal_chain": chain,
            "integrity": integrity,
            "compliance": {
                "policy_references": sorted(
                    {
                        policy
                        for node in chain["nodes"]
                        for policy in node["decision"].get("policies", [])
                    }
                ),
                "evidence_complete": all(
                    node["decision"].get("evidence") for node in chain["nodes"]
                ),
                "rationale_complete": all(
                    node["decision"].get("rationale") for node in chain["nodes"]
                ),
            },
        }

    def verify_integrity(self) -> dict[str, Any]:
        previous_hash = None
        for index, entry in enumerate(self._entries(), start=1):
            payload = {
                "decision": entry.get("decision"),
                "previous_hash": entry.get("previous_hash"),
            }
            if entry.get("previous_hash") != previous_hash or entry.get(
                "integrity", {}
            ).get("hash") != _hash(payload):
                return {
                    "valid": False,
                    "entries_checked": index,
                    "failed_decision_id": entry.get("decision", {}).get("id"),
                }
            previous_hash = entry["integrity"]["hash"]
        return {
            "valid": True,
            "entries_checked": index if "index" in locals() else 0,
            "head_hash": previous_hash,
        }

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise DecisionProvenanceError(
                    f"invalid provenance ledger at line {line_number}"
                ) from exc
        return entries
