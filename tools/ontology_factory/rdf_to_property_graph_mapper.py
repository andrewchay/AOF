#!/usr/bin/env python3
"""Map OWL/RDF XML to AOF property-graph JSON artifacts.

Output schema:
- nodes: [{id, labels, properties}]
- edges: [{id, source_id, target_id, relation_type, properties}]
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
OWL_NS = "http://www.w3.org/2002/07/owl#"

RDF_ABOUT = f"{{{RDF_NS}}}about"
RDF_RESOURCE = f"{{{RDF_NS}}}resource"
RDF_DATATYPE = f"{{{RDF_NS}}}datatype"


@dataclass
class PGNode:
    id: str
    labels: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class PGEdge:
    id: str
    source_id: str
    target_id: str
    relation_type: str
    properties: dict[str, Any] = field(default_factory=dict)


def _short_name(iri: str) -> str:
    parsed = urlparse(iri)
    if parsed.fragment:
        return parsed.fragment
    if parsed.path:
        return Path(parsed.path).name or iri
    return iri


def _slug(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "entity"


def _node_id(iri: str) -> str:
    return _slug(_short_name(iri))


def _ensure_node(nodes: dict[str, PGNode], iri: str, label: str) -> str:
    node_id = _node_id(iri)
    if node_id not in nodes:
        nodes[node_id] = PGNode(
            id=node_id,
            labels=[label],
            properties={
                "iri": iri,
                "name": _short_name(iri),
                "kind": label,
            },
        )
    else:
        if label not in nodes[node_id].labels:
            nodes[node_id].labels.append(label)
    return node_id


def _add_edge(edges: list[PGEdge], source_id: str, target_id: str, rel: str, properties: dict[str, Any] | None = None) -> None:
    edge_id = f"{source_id}_{rel}_{target_id}_{len(edges)+1}"
    edges.append(
        PGEdge(
            id=edge_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=rel,
            properties=properties or {},
        )
    )


def map_owl_to_property_graph(owl_file: Path) -> tuple[list[PGNode], list[PGEdge]]:
    tree = ET.parse(owl_file)
    root = tree.getroot()

    nodes: dict[str, PGNode] = {}
    edges: list[PGEdge] = []

    # 1) Classes
    for cls in root.findall(f".//{{{OWL_NS}}}Class"):
        iri = cls.attrib.get(RDF_ABOUT, "")
        if not iri:
            continue
        cls_id = _ensure_node(nodes, iri, "Class")

        for sub in cls.findall(f"{{{RDFS_NS}}}subClassOf"):
            parent_iri = sub.attrib.get(RDF_RESOURCE, "")
            if not parent_iri:
                continue
            parent_id = _ensure_node(nodes, parent_iri, "Class")
            _add_edge(edges, cls_id, parent_id, "subClassOf")

    # 2) Properties
    for tag, kind in (
        (f"{{{OWL_NS}}}ObjectProperty", "ObjectProperty"),
        (f"{{{OWL_NS}}}DatatypeProperty", "DatatypeProperty"),
        (f"{{{OWL_NS}}}AnnotationProperty", "AnnotationProperty"),
        (f"{{{OWL_NS}}}FunctionalProperty", "FunctionalProperty"),
    ):
        for prop in root.findall(f".//{tag}"):
            iri = prop.attrib.get(RDF_ABOUT, "")
            if not iri:
                continue
            prop_id = _ensure_node(nodes, iri, "Property")
            nodes[prop_id].properties["property_kind"] = kind

            for domain in prop.findall(f"{{{RDFS_NS}}}domain"):
                domain_iri = domain.attrib.get(RDF_RESOURCE, "")
                if not domain_iri:
                    continue
                domain_id = _ensure_node(nodes, domain_iri, "Class")
                _add_edge(edges, prop_id, domain_id, "domain")

            for rng in prop.findall(f"{{{RDFS_NS}}}range"):
                rng_iri = rng.attrib.get(RDF_RESOURCE, "")
                if not rng_iri:
                    continue
                # xsd ranges may be datatype IRI, keep as Datatype node
                label = "Datatype" if "xmlschema" in rng_iri.lower() else "Class"
                rng_id = _ensure_node(nodes, rng_iri, label)
                _add_edge(edges, prop_id, rng_id, "range")

    # 3) Individuals
    for ind in root.findall(f".//{{{OWL_NS}}}NamedIndividual"):
        iri = ind.attrib.get(RDF_ABOUT, "")
        if not iri:
            continue
        ind_id = _ensure_node(nodes, iri, "Individual")

        # rdf:type links
        for typ in ind.findall(f"{{{RDF_NS}}}type"):
            type_iri = typ.attrib.get(RDF_RESOURCE, "")
            if not type_iri:
                continue
            type_id = _ensure_node(nodes, type_iri, "Class")
            _add_edge(edges, ind_id, type_id, "instanceOf")

        # literal/object assertions
        for child in list(ind):
            pred_tag = child.tag
            if pred_tag in {f"{{{RDF_NS}}}type"}:
                continue
            pred_local = pred_tag.split("}")[-1] if "}" in pred_tag else pred_tag
            pred_id = _ensure_node(nodes, f"urn:property:{pred_local}", "Property")
            nodes[pred_id].properties["property_kind"] = "AssertionProperty"

            obj_res = child.attrib.get(RDF_RESOURCE)
            if obj_res:
                obj_id = _ensure_node(nodes, obj_res, "Individual")
                _add_edge(edges, ind_id, obj_id, pred_local)
                continue

            lit = (child.text or "").strip()
            if lit:
                dt = child.attrib.get(RDF_DATATYPE, "")
                lit_id = _ensure_node(nodes, f"urn:literal:{_slug(lit)[:80]}", "Literal")
                nodes[lit_id].properties["value"] = lit
                if dt:
                    nodes[lit_id].properties["datatype"] = dt
                _add_edge(edges, ind_id, lit_id, pred_local)

    return list(nodes.values()), edges


def export_graph_json(nodes: list[PGNode], edges: list[PGEdge], out_nodes: Path, out_edges: Path) -> None:
    out_nodes.parent.mkdir(parents=True, exist_ok=True)
    out_edges.parent.mkdir(parents=True, exist_ok=True)
    out_nodes.write_text(
        json.dumps([n.__dict__ for n in nodes], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    out_edges.write_text(
        json.dumps([e.__dict__ for e in edges], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Map OWL to property graph JSON")
    parser.add_argument("--owl-file", required=True)
    parser.add_argument("--out-nodes", required=True)
    parser.add_argument("--out-edges", required=True)
    args = parser.parse_args()

    owl_file = Path(args.owl_file).resolve()
    if not owl_file.exists():
        raise SystemExit(f"owl file not found: {owl_file}")

    nodes, edges = map_owl_to_property_graph(owl_file)
    export_graph_json(nodes, edges, Path(args.out_nodes).resolve(), Path(args.out_edges).resolve())

    print("[done] owl -> property graph")
    print(f"- owl: {owl_file}")
    print(f"- nodes: {args.out_nodes} ({len(nodes)})")
    print(f"- edges: {args.out_edges} ({len(edges)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
