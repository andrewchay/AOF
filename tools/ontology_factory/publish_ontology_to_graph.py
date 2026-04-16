#!/usr/bin/env python3
"""Publish OWL ontology into online graph storage (Nebula/Cognee).

Pipeline:
1) URI sanitation check (strict by default)
2) OWL -> property graph mapping
3) Persist to GraphBackend
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def _publish(
    owl_file: Path,
    backend: str,
    dataset: str,
    nebula_space: str,
    allow_external_uri: bool,
    dry_run: bool,
) -> dict:
    from tools.ontology_factory.ontology_uri_sanitizer import find_external_entity_uris
    from tools.ontology_factory.rdf_to_property_graph_mapper import map_owl_to_property_graph

    findings = find_external_entity_uris(owl_file)
    if findings and not allow_external_uri:
        sample = ", ".join(f["iri"] for f in findings[:3])
        raise RuntimeError(
            f"Blocked by URI sanitizer: found {len(findings)} suspicious entity URIs; sample: {sample}"
        )

    nodes, edges = map_owl_to_property_graph(owl_file)
    edge_count = len(edges)
    node_count = len(nodes)

    if dry_run:
        return {
            "dry_run": True,
            "backend": backend,
            "dataset": dataset,
            "owl": str(owl_file),
            "nodes": node_count,
            "edges": edge_count,
            "uri_findings": len(findings),
        }

    from bridge.storage import StorageConfig, StorageFactory
    from bridge.storage.base import Node, Triple

    cfg = StorageConfig(
        backend_type=backend,
        default_dataset=dataset,
        nebula_space=nebula_space,
    )
    graph = StorageFactory.create(cfg)

    connected = await graph.connect()
    if not connected:
        raise RuntimeError(f"Failed to connect graph backend: {backend}")

    try:
        # 1) add nodes (ensures isolated nodes are preserved)
        for n in nodes:
            await graph.add_node(
                Node(id=n.id, labels=n.labels, properties=n.properties)
            )

        # 2) add edges as triples
        node_map = {n.id: n for n in nodes}
        triples = []
        for e in edges:
            src = node_map.get(e.source_id)
            dst = node_map.get(e.target_id)
            if not src or not dst:
                continue
            triples.append(
                Triple(
                    subject=Node(id=src.id, labels=src.labels, properties=src.properties),
                    predicate=e.relation_type,
                    object=Node(id=dst.id, labels=dst.labels, properties=dst.properties),
                    edge_properties=e.properties,
                )
            )

        inserted = await graph.add_triples(triples)
    finally:
        await graph.disconnect()

    return {
        "backend": backend,
        "dataset": dataset,
        "owl": str(owl_file),
        "nodes": node_count,
        "edges": edge_count,
        "inserted_triples": inserted,
        "uri_findings": len(findings),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish OWL ontology to graph backend")
    parser.add_argument("--owl-file", required=True)
    parser.add_argument("--backend", default="nebula", choices=["nebula", "cognee"])
    parser.add_argument("--dataset", default="default")
    parser.add_argument("--nebula-space", default="aof_default")
    parser.add_argument("--allow-external-uri", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-json", default="")
    args = parser.parse_args()

    owl_file = Path(args.owl_file).resolve()
    if not owl_file.exists():
        raise SystemExit(f"owl file not found: {owl_file}")

    result = asyncio.run(
        _publish(
            owl_file=owl_file,
            backend=args.backend,
            dataset=args.dataset,
            nebula_space=args.nebula_space,
            allow_external_uri=args.allow_external_uri,
            dry_run=args.dry_run,
        )
    )

    if args.report_json:
        out = Path(args.report_json).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"- report: {out}")

    print("[published] owl -> graph")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
