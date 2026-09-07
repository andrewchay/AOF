# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""
Versioned, source-grounded semantic contracts.
These contracts deliberately separate a candidate assertion from a released
enterprise fact.  Retrieval systems may use candidates for review, but only a
release is consumable by downstream plans, graph projections, or agents.
⚠️ PROTOTYPE — NOT THE CANONICAL CONTRACT ⚠️
This module contains an early prototype SemanticFact/Release lifecycle
that is NOT connected to REST/MCP or any tests. The canonical semantic
contracts are in models.py (SemanticResource) and releases.py (KnowledgeRelease).
Do NOT use this module for new development. It is preserved for historical
reference only. See docs/remediation/2026-09-05/implementation-plan.md K01.
---
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
def utc_now() -> str:
return datetime.now(UTC).isoformat()
class FactStatus(StrEnum):
CANDIDATE = "candidate"
APPROVED = "approved"
REJECTED = "rejected"
RELEASED = "released"
@dataclass(frozen=True)
class SourceAsset:
id: str
tenant_id: str
source_type: str
locator: str
content_digest: str
observed_at: str
classification: str = "internal"
metadata: dict[str, Any] = field(default_factory=dict)
@dataclass(frozen=True)
class Evidence:
id: str
source_asset_id: str
locator: str
excerpt: str
content_digest: str
extractor: str
extracted_at: str
@dataclass(frozen=True)
class SemanticFact:
id: str
tenant_id: str
subject: str
predicate: str
object_value: str
evidence_ids: tuple[str, ...]
status: FactStatus
asserted_at: str
valid_from: str | None = None
valid_to: str | None = None
confidence: float = 1.0
metadata: dict[str, Any] = field(default_factory=dict)
@dataclass(frozen=True)
class Release:
id: str
tenant_id: str
fact_ids: tuple[str, ...]
digest: str
approved_by: str
released_at: str
previous_release_id: str | None = None
def to_record(value: SourceAsset | Evidence | SemanticFact | Release) -> dict[str, Any]:
record = asdict(value)
if isinstance(value, SemanticFact):
record["status"] = value.status.value
record["evidence_ids"] = list(value.evidence_ids)
if isinstance(value, Release):
record["fact_ids"] = list(value.fact_ids)
return record
Prototype status (K01): an early SemanticFact/Release lifecycle that is
NOT connected to REST/MCP or any tests. The canonical contracts are
models.py (SemanticResource) and releases.py (KnowledgeRelease). Do not
use this module for new development; see
docs/remediation/2026-09-05/implementation-plan.md.
"""