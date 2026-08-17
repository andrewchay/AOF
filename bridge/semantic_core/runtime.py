"""Verified consumers for deterministic Semantic IR runtime artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from rdflib import Graph, RDF
from rdflib.namespace import SH

from bridge.ontology_governance import DatalogEngine

from .canonical import canonical_json
from .compilers import CompiledArtifact, CompilerError, CompilerRegistry
from .releases import KnowledgeRelease


class SemanticRuntimeConsumer:
    def __init__(self, registry: CompilerRegistry) -> None:
        self.registry = registry

    def consume(
        self,
        artifact: CompiledArtifact,
        output_dir: str | Path,
        *,
        release: KnowledgeRelease,
        query: str = "",
        facts: Iterable[tuple[str, list[str]]] = (),
    ) -> dict[str, Any]:
        verification = self.registry.verify(artifact, output_dir, release=release)
        if not verification.valid:
            raise CompilerError("; ".join(verification.findings))
        payload = json.loads((Path(output_dir) / artifact.uri).read_text(encoding="utf-8"))
        if payload.get("source_release_digest") != release.release_digest:
            raise CompilerError("runtime bundle source release digest mismatch")
        if artifact.target in {"owl", "shacl"}:
            graph = Graph()
            for resource in payload["resources"]:
                spec = resource.get("spec", {})
                fmt = {"ttl": "turtle", "rdfxml": "xml", "jsonld": "json-ld"}.get(
                    str(spec.get("format", "turtle")).lower(), spec.get("format", "turtle")
                )
                graph.parse(data=spec["content"], format=fmt)
            result = {"triple_count": len(graph)}
            if artifact.target == "shacl":
                result["shape_count"] = len(set(graph.subjects(RDF.type, SH.NodeShape)))
            return result
        if artifact.target == "datalog":
            derived = []
            for ruleset in payload["rulesets"]:
                run = DatalogEngine(ruleset["program"], ruleset_id=ruleset["resource_id"]).run(facts)
                derived.extend(run["derived_facts"])
            return {"derived_count": len(derived), "derived_facts": derived}
        if artifact.target == "rag":
            needle = query.casefold()
            hits = [
                {"resource_id": item["resource_id"], "revision_id": item["revision_id"]}
                for item in payload["resources"]
                if needle and needle in canonical_json(item).casefold()
            ]
            return {"query": query, "hits": hits, "count": len(hits)}
        if artifact.target == "mcp":
            return {
                "resource_count": len(payload["mcp_resources"]),
                "tool_count": len(payload["mcp_tools"]),
                "resources": payload["mcp_resources"],
                "tools": payload["mcp_tools"],
            }
        raise CompilerError(f"no runtime consumer registered for target: {artifact.target}")
