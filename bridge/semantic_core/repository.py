"""Small durable repository for the trusted semantic vertical slice.

The reference implementation uses one atomically replaced JSON state file so
it can run without a database.  It is intentionally a system-of-record for
the semantic release boundary, not a graph or vector-store replacement.
"""
"""
⚠️ PROTOTYPE — NOT THE CANONICAL CONTRACT ⚠️

This module contains an early prototype SemanticFact/Release lifecycle
that is NOT connected to REST/MCP or any tests. The canonical semantic
contracts are in models.py (SemanticResource) and releases.py (KnowledgeRelease).

Do NOT use this module for new development. It is preserved for historical
reference only. See docs/remediation/2026-09-05/implementation-plan.md K01.

---



from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class SemanticRepository:
    def __init__(self, root: Path):
        self.root = root
        self.state_file = root / "semantic_state.json"

    def load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {
                "sources": {},
                "evidence": {},
                "facts": {},
                "proposals": {},
                "releases": {},
            }
        payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("semantic state must be a JSON object")
        for key in ("sources", "evidence", "facts", "proposals", "releases"):
            if not isinstance(payload.get(key, {}), dict):
                raise ValueError(f"semantic state has invalid {key}")
            payload.setdefault(key, {})
        return payload

    def save(self, state: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".semantic-state-", dir=self.root)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.state_file)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
