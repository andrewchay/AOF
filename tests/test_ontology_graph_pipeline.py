"""Tests for OWL -> online graph migration toolchain."""

from __future__ import annotations

import json
from pathlib import Path

from tools.ontology_factory.ontology_uri_sanitizer import find_external_entity_uris
from tools.ontology_factory.rdf_to_property_graph_mapper import map_owl_to_property_graph, export_graph_json


def _write_min_owl(path: Path) -> None:
    path.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\"
         xmlns:rdfs=\"http://www.w3.org/2000/01/rdf-schema#\"
         xmlns:owl=\"http://www.w3.org/2002/07/owl#\">
  <owl:Class rdf:about=\"http://example.org/ontology#User\"/>
  <owl:Class rdf:about=\"http://example.org/ontology#Order\">
    <rdfs:subClassOf rdf:resource=\"http://example.org/ontology#User\"/>
  </owl:Class>
  <owl:ObjectProperty rdf:about=\"http://example.org/ontology#relatesToUser\">
    <rdfs:domain rdf:resource=\"http://example.org/ontology#Order\"/>
    <rdfs:range rdf:resource=\"http://example.org/ontology#User\"/>
  </owl:ObjectProperty>
  <owl:NamedIndividual rdf:about=\"http://example.org/ontology#u_1\">
    <rdf:type rdf:resource=\"http://example.org/ontology#User\"/>
  </owl:NamedIndividual>
</rdf:RDF>
""",
        encoding="utf-8",
    )


def _write_polluted_owl(path: Path) -> None:
    path.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\"
         xmlns:owl=\"http://www.w3.org/2002/07/owl#\">
  <owl:Class rdf:about=\"https://www.w3schools.com/ontology#BadClass\"/>
</rdf:RDF>
""",
        encoding="utf-8",
    )


def test_mapper_builds_expected_graph(tmp_path: Path) -> None:
    owl = tmp_path / "mini.owl"
    out_nodes = tmp_path / "nodes.json"
    out_edges = tmp_path / "edges.json"
    _write_min_owl(owl)

    nodes, edges = map_owl_to_property_graph(owl)

    assert len(nodes) >= 4
    assert len(edges) >= 4
    assert any("Class" in n.labels for n in nodes)
    assert any(e.relation_type == "subClassOf" for e in edges)
    assert any(e.relation_type == "instanceOf" for e in edges)

    export_graph_json(nodes, edges, out_nodes, out_edges)
    assert out_nodes.exists()
    assert out_edges.exists()

    nodes_payload = json.loads(out_nodes.read_text(encoding="utf-8"))
    edges_payload = json.loads(out_edges.read_text(encoding="utf-8"))
    assert isinstance(nodes_payload, list)
    assert isinstance(edges_payload, list)


def test_uri_sanitizer_detects_suspicious_external_uri(tmp_path: Path) -> None:
    owl = tmp_path / "polluted.owl"
    _write_polluted_owl(owl)

    findings = find_external_entity_uris(owl)

    assert len(findings) == 1
    assert "w3schools" in findings[0]["iri"]
