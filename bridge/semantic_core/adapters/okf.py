# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Adapter for existing OKF/LLM Wiki bundles."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..canonical import content_digest
from ..models import ResourceKind, SemanticResource
from .base import AdapterContext, AdapterError, safe_segment


def adapt_okf_bundle(
    bundle: str | Path, *, bundle_id: str, context: AdapterContext
) -> SemanticResource:
    root = Path(bundle)
    if not root.is_dir() or not (root / "index.md").is_file():
        raise AdapterError("OKF bundle requires a directory containing index.md")
    files = []
    for path in sorted(root.rglob("*.md")):
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise AdapterError(f"OKF file escapes bundle root: {path}") from exc
        raw = path.read_bytes()
        files.append(
            {
                "path": relative,
                "content_hash": f"sha256:{hashlib.sha256(raw).hexdigest()}",
                "size": len(raw),
            }
        )
    bundle_hash = content_digest(files)
    return SemanticResource.create(
        resource_id=context.resource_id("retrieval-profile", f"{bundle_id}-okf"),
        kind=ResourceKind.RETRIEVAL_PROFILE,
        name=safe_segment(f"{bundle_id}-okf"),
        display_name=f"{bundle_id} OKF bundle",
        domain=context.domain,
        owner=context.owner,
        evidence=[
            {
                "evidence_id": f"legacy:okf:{safe_segment(bundle_id)}:{bundle_hash}",
                "source_uri": f"okf://{safe_segment(bundle_id)}",
                "content_hash": bundle_hash,
            }
        ],
        spec={
            "format": "okf-markdown",
            "bundle_id": bundle_id,
            "bundle_hash": bundle_hash,
            "files": files,
            "legacy_source": "okf_bundle",
        },
    )
