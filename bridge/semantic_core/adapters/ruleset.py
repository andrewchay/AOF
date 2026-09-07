# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Adapter for existing immutable Datalog RuleSetRepository releases."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ..models import ResourceKind, SemanticResource
from .base import AdapterContext, AdapterError, safe_segment


def adapt_ruleset_release(
    release: Mapping[str, Any], *, context: AdapterContext
) -> SemanticResource:
    manifest = release.get("manifest", release)
    if not isinstance(manifest, Mapping):
        raise AdapterError("ruleset release manifest must be an object")
    program = release.get("program")
    ruleset_id = str(manifest.get("ruleset_id") or "").strip()
    version = str(manifest.get("ruleset_version") or "").strip()
    if not ruleset_id or not version or not isinstance(program, str) or not program.strip():
        raise AdapterError("ruleset release requires ruleset_id, ruleset_version, and program")
    source_hash = f"sha256:{hashlib.sha256(program.encode('utf-8')).hexdigest()}"
    return SemanticResource.create(
        resource_id=context.resource_id("rule-set", ruleset_id),
        kind=ResourceKind.RULE_SET,
        name=safe_segment(ruleset_id),
        display_name=str(manifest.get("description") or ruleset_id),
        description=str(manifest.get("description") or ""),
        domain=context.domain,
        owner=context.owner,
        evidence=[
            {
                "evidence_id": f"legacy:ruleset:{ruleset_id}:{version}",
                "source_uri": f"ruleset://{ruleset_id}/{version}/program.dl",
                "content_hash": source_hash,
            }
        ],
        spec={
            "language": "datalog",
            "program": program,
            "legacy_version": version,
            "legacy_content_hash": manifest.get("content_hash"),
            "source_content_hash": source_hash,
        },
    )
