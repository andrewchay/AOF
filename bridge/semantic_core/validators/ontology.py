# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Bridge the existing SHACL/OWL/SKOS gate into unified Semantic Proposals."""

from __future__ import annotations

from typing import Any

from rdflib import Graph

from bridge.ontology_governance import ShaclCoreValidator, validate_skos_graph

from ..governance import SemanticFinding
from ..models import ResourceKind, SemanticResource


_ONTOLOGY_KINDS = {
    ResourceKind.ONTOLOGY,
    ResourceKind.CONSTRAINT_SET,
    ResourceKind.VOCABULARY,
}


def _parse_resource(resource: SemanticResource) -> Graph:
    content = resource.spec.get("content")
    if not isinstance(content, str):
        raise ValueError("spec.content must contain RDF text")
    declared = str(resource.spec.get("format", "turtle")).lower()
    formats = {
        "ttl": "turtle",
        "rdf/xml": "xml",
        "rdfxml": "xml",
        "jsonld": "json-ld",
    }
    graph = Graph()
    graph.parse(data=content, format=formats.get(declared, declared))
    return graph


def ontology_release_validator(
    resources: tuple[SemanticResource, ...],
) -> tuple[SemanticFinding, ...]:
    """Validate every ontology-bearing release as one deterministic RDF snapshot."""

    relevant = tuple(resource for resource in resources if resource.kind in _ONTOLOGY_KINDS)
    if not relevant:
        return ()

    data_graph, shapes_graph = Graph(), Graph()
    for resource in relevant:
        try:
            graph = _parse_resource(resource)
        except Exception as exc:
            return (
                SemanticFinding(
                    finding_id=f"finding:rdf-parse:{resource.revision_id.split(':', 1)[1][:16]}",
                    validator="ontology-shacl-owl-skos",
                    severity="Violation",
                    message=f"invalid RDF in {resource.resource_id}: {exc}",
                    resource_id=resource.resource_id,
                    waiver_allowed=False,
                    details={"type": "rdf_parse_error", "resource_kind": resource.kind.value},
                ),
            )
        target = shapes_graph if resource.kind is ResourceKind.CONSTRAINT_SET else data_graph
        for triple in graph:
            target.add(triple)

    result = ShaclCoreValidator().validate(data_graph, shapes_graph)
    result["findings"].extend(validate_skos_graph(data_graph))
    findings = []
    for item in result["findings"]:
        details: dict[str, Any] = {key: value for key, value in item.items() if key != "finding_id"}
        findings.append(
            SemanticFinding(
                finding_id=item["finding_id"],
                validator="ontology-shacl-owl-skos",
                severity=item.get("severity", "Violation"),
                message=item.get("message", "ontology governance finding"),
                waiver_allowed=item.get("type") != "unsupported_shacl_constraint",
                details=details,
            )
        )
    return tuple(sorted(findings, key=lambda finding: finding.finding_id))
