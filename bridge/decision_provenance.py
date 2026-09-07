"""Append-only, PROV-O-inspired provenance for Agent decisions.

The module deliberately keeps decision provenance separate from request audit logs:
an audit log answers *what endpoint was called*, while this ledger answers *why an
agent reached a conclusion, what it used, and what later work it influenced*.

W02.01: the store is now a compatibility layer over
:class:`bridge.persistence.decision_ledger_repository.DecisionLedgerRepository`.
The default backend is SQLite (``reference-local`` profile); set
``AOF_DECISION_LEDGER_BACKEND=jsonl`` to force the legacy file-based ledger.
When the store path ends with ``.jsonl`` the legacy backend is selected
automatically so that existing file-based callers keep working unchanged.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from bridge.persistence.decision_ledger_repository import (
    LedgerConflictError,
    LedgerEntry,
    SQLiteDecisionLedgerRepository,
)


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


class LedgerIntegrityError(DecisionProvenanceError):
    """Raised when the ledger fails integrity verification."""


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


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

_BACKEND_ENV = "AOF_DECISION_LEDGER_BACKEND"
_DEFAULT_BACKEND = "sqlite"


def _select_backend(path: Path) -> str:
    """Select ledger backend.

    Precedence:
    1. ``AOF_DECISION_LEDGER_BACKEND`` environment variable
    2. ``.jsonl`` suffix → legacy JSONL backend (backward compatibility)
    3. default → ``sqlite``
    """
    env = os.environ.get(_BACKEND_ENV, "").strip().lower()
    if env in {"jsonl", "sqlite"}:
        return env
    if path.suffix == ".jsonl":
        return "jsonl"
    return _DEFAULT_BACKEND


# ---------------------------------------------------------------------------
# JSONL legacy implementation (kept for migration and opt-in)
# ---------------------------------------------------------------------------

class _JSONLLedgerBackend:
    """Legacy JSONL file ledger with fcntl locking and sidecar checkpoint."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock_path = path.with_suffix(".lock")
        self._checkpoint_path = path.with_suffix(".checkpoint")

    def _acquire_lock(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(self._lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return lock_fd

    @staticmethod
    def _release_lock(lock_fd) -> None:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

    def _read_checkpoint(self) -> tuple[int, str] | None:
        try:
            data = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
            return int(data["entry_count"]), str(data["head_hash"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def _write_checkpoint(self, entry_count: int, head_hash: str) -> None:
        self._checkpoint_path.write_text(
            _canonical_json({"entry_count": entry_count, "head_hash": head_hash}),
            encoding="utf-8",
        )

    def _entries_unlocked(self) -> list[dict[str, Any]]:
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

    def _entries_verified_locked(self) -> list[dict[str, Any]]:
        entries = self._entries_unlocked()
        if not entries:
            return entries
        checkpoint = self._read_checkpoint()
        if checkpoint is not None and checkpoint[0] != len(entries):
            raise LedgerIntegrityError(
                f"ledger length mismatch: checkpoint records "
                f"{checkpoint[0]} entries but file contains {len(entries)}"
            )
        previous_hash = None
        for index, entry in enumerate(entries, start=1):
            payload = {
                "decision": entry.get("decision"),
                "previous_hash": entry.get("previous_hash"),
            }
            if entry.get("previous_hash") != previous_hash or entry.get(
                "integrity", {}
            ).get("hash") != _hash(payload):
                raise LedgerIntegrityError(
                    f"ledger integrity check failed at entry {index} "
                    f"(decision: {entry.get('decision', {}).get('id', 'unknown')})"
                )
            previous_hash = entry["integrity"]["hash"]
        return entries

    def record(self, decision: DecisionRecord) -> dict[str, Any]:
        lock_fd = self._acquire_lock()
        try:
            entries = self._entries_unlocked()
            existing = {item["decision"]["id"] for item in entries}
            if decision.id in existing:
                raise DecisionProvenanceError(f"decision already exists: {decision.id}")
            unknown = sorted(set(decision.parent_decision_ids) - existing)
            if unknown:
                raise DecisionProvenanceError(
                    f"unknown parent decision(s): {', '.join(unknown)}"
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
            self._write_checkpoint(len(entries) + 1, entry["integrity"]["hash"])
            return entry
        finally:
            self._release_lock(lock_fd)

    def entries_verified(self) -> list[dict[str, Any]]:
        lock_fd = self._acquire_lock()
        try:
            return self._entries_verified_locked()
        finally:
            self._release_lock(lock_fd)

    def verify_integrity(self) -> dict[str, Any]:
        lock_fd = self._acquire_lock()
        try:
            previous_hash = None
            for index, entry in enumerate(self._entries_unlocked(), start=1):
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
        finally:
            self._release_lock(lock_fd)


# ---------------------------------------------------------------------------
# SQLite backend adapter
# ---------------------------------------------------------------------------

class _SQLiteLedgerBackend:
    """Adapter from DecisionProvenanceStore semantics to the Repository SPI."""

    def __init__(self, path: Path) -> None:
        # Map legacy JSONL path to SQLite path: same directory, .sqlite suffix
        sqlite_path = path.with_suffix(".sqlite")
        self._repo = SQLiteDecisionLedgerRepository(sqlite_path)
        self._legacy_path = path  # kept for migrate_from_jsonl

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _entry_to_dict(entry: LedgerEntry) -> dict[str, Any]:
        return entry.to_dict()

    def _all_entries(self) -> list[dict[str, Any]]:
        return [self._entry_to_dict(e) for e in self._repo.all_entries()]

    # -- public API mirroring DecisionProvenanceStore -------------------

    def record(self, decision: DecisionRecord) -> dict[str, Any]:
        tenant_id = decision.tenant_id or "__default__"
        head = self._repo.head(tenant_id)
        sequence = (head[0] + 1) if head else 1
        previous_hash = head[1] if head else None

        payload = {"decision": decision.to_dict(), "previous_hash": previous_hash}
        entry_hash = _hash(payload)
        entry = LedgerEntry(
            tenant_id=tenant_id,
            sequence=sequence,
            decision_id=decision.id,
            payload_digest=_hash(decision.to_dict()),
            previous_hash=previous_hash,
            entry_hash=entry_hash,
            payload=payload,
            recorded_at=decision.recorded_at,
        )
        try:
            appended = self._repo.append(entry)
        except LedgerConflictError as exc:
            raise DecisionProvenanceError(str(exc)) from exc
        return self._entry_to_dict(appended)

    def entries_verified(self) -> list[dict[str, Any]]:
        result = self._repo.verify_chain()
        if not result["valid"]:
            raise LedgerIntegrityError(
                f"ledger integrity check failed at entry {result['entries_checked']} "
                f"(decision: {result.get('failed_decision_id', 'unknown')})"
            )
        return self._all_entries()

    def verify_integrity(self) -> dict[str, Any]:
        return self._repo.verify_chain()

    def migrate_from_jsonl(self, path: str | Path | None = None) -> dict[str, Any]:
        source = Path(path) if path else self._legacy_path
        return self._repo.migrate_from_jsonl(source)


# ---------------------------------------------------------------------------
# Public store — compatibility layer
# ---------------------------------------------------------------------------

class DecisionProvenanceStore:
    """Append-only decision ledger with deterministic local queries.

    The public API is unchanged from the JSONL implementation. Internally
    the store delegates to a backend selected by
    ``AOF_DECISION_LEDGER_BACKEND`` (``sqlite`` by default, ``jsonl`` for
    legacy behaviour). When the store path ends with ``.jsonl`` the legacy
    backend is selected automatically so that existing file-based callers
    keep working unchanged.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        configured = os.environ.get("AOF_DECISION_PROVENANCE_FILE")
        self.path = Path(path or configured or "data/audit/decision_provenance.jsonl")
        backend = _select_backend(self.path)
        if backend == "jsonl":
            self._backend: _JSONLLedgerBackend | _SQLiteLedgerBackend = (
                _JSONLLedgerBackend(self.path)
            )
        else:
            self._backend = _SQLiteLedgerBackend(self.path)

    # ------------------------------------------------------------------
    # public API (unchanged signatures)
    # ------------------------------------------------------------------

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
        return self._backend.record(decision)

    def get(self, decision_id: str) -> dict[str, Any] | None:
        entries = self._entries_verified()
        return next(
            (entry for entry in entries if entry["decision"]["id"] == decision_id),
            None,
        )

    def causal_chain(
        self, decision_id: str, direction: str = "ancestors", max_depth: int = 8
    ) -> dict[str, Any]:
        if direction not in {"ancestors", "descendants"}:
            raise DecisionProvenanceError("direction must be ancestors or descendants")
        if not 1 <= max_depth <= 50:
            raise DecisionProvenanceError("max_depth must be between 1 and 50")
        entries = self._entries_verified()
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
        for entry in self._entries_verified():
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
        return self._backend.verify_integrity()

    # ------------------------------------------------------------------
    # migration helper
    # ------------------------------------------------------------------

    def migrate_from_jsonl(self, path: str | Path | None = None) -> dict[str, Any]:
        """Import a JSONL ledger into the current backend.

        Only available on the SQLite backend; raises on JSONL backend.
        """
        if isinstance(self._backend, _SQLiteLedgerBackend):
            return self._backend.migrate_from_jsonl(path)
        raise DecisionProvenanceError(
            "migrate_from_jsonl is only supported on the sqlite backend"
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _entries_verified(self) -> list[dict[str, Any]]:
        return self._backend.entries_verified()

    # Keep backward-compatible alias
    _entries = _entries_verified
