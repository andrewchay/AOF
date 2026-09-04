"""Adapter for releases produced by the existing ontology governance service."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ..models import ResourceKind, SemanticResource
from .base import AdapterContext, AdapterError, safe_segment


_PARTS = {
    "ontology.ttl": (ResourceKind.ONTOLOGY, "ontology", "ontology"),
    "shapes.ttl": (ResourceKind.CONSTRAINT_SET, "constraint-set", "shapes"),
    "skos.ttl": (ResourceKind.VOCABULARY, "vocabulary", "vocabulary"),
}


def adapt_ontology_release(
    release: Mapping[str, Any], *, contents: Mapping[str, str], context: AdapterContext
) -> tuple[SemanticResource, ...]:
    ontology_id = str(release.get("ontology_id") or "").strip()
    ontology_version = str(release.get("ontology_version") or "").strip()
    if not ontology_id or not ontology_version:
        raise AdapterError("ontology release requires ontology_id and ontology_version")
    declared_hashes = release.get("content_hashes", {})
    hashes: dict[str, str] = {}
    for filename in _PARTS:
        content = contents.get(filename)
        if not isinstance(content, str):
            raise AdapterError(f"ontology release content is missing: {filename}")
        actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
        declared = str(declared_hashes.get(filename) or "").removeprefix("sha256:")
        if declared and declared != actual:
            raise AdapterError(f"ontology release content hash mismatch: {filename}")
        hashes[filename] = f"sha256:{actual}"

    ontology_resource_id = context.resource_id("ontology", ontology_id)
    resources = []
    for filename, (kind, kind_segment, suffix) in _PARTS.items():
        resource_id = (
            ontology_resource_id
            if kind is ResourceKind.ONTOLOGY
            else context.resource_id(kind_segment, f"{ontology_id}-{suffix}")
        )
        dependencies = [] if kind is ResourceKind.ONTOLOGY else [ontology_resource_id]
        resources.append(
            SemanticResource.create(
                resource_id=resource_id,
                kind=kind,
                name=safe_segment(f"{ontology_id}-{suffix}" if dependencies else ontology_id),
                display_name=f"{ontology_id} {suffix}",
                domain=context.domain,
                owner=context.owner,
                depends_on=dependencies,
                evidence=[
                    {
                        "evidence_id": f"legacy:ontology:{ontology_id}:{ontology_version}:{filename}",
                        "source_uri": f"ontology://{ontology_id}/{ontology_version}/{filename}",
                        "content_hash": hashes[filename],
                    }
                ],
                spec={
                    "format": "turtle",
                    "content": contents[filename],
                    "legacy_ontology_id": ontology_id,
                    "legacy_version": ontology_version,
                    "source_content_hash": hashes[filename],
                },
            )
        )
    return tuple(resources)
