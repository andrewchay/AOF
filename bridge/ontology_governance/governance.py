"""Versioned OWL/SKOS governance with a SHACL Core release gate.

The validator implements the deliberately bounded SHACL Core constraints used by
AOF's release contract. Unsupported constraint predicates are reported as gate
errors instead of being silently ignored.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import DCTERMS, OWL, SH, SKOS, XSD

from bridge.decision_provenance import DecisionProvenanceStore


AOF = Namespace("https://aof.dev/ns/governance#")
SUPPORTED_CONSTRAINTS = {
    SH.minCount,
    SH.maxCount,
    SH.datatype,
    SH["class"],
    SH.nodeKind,
    SH.minInclusive,
    SH.maxInclusive,
    SH.minLength,
    SH.maxLength,
    SH.pattern,
    SH["in"],
}


class OntologyGovernanceError(ValueError):
    """Raised when a governed lifecycle invariant is violated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_rdf(text: str, *, name: str) -> Graph:
    graph = Graph()
    errors = []
    for fmt in ("turtle", "xml", "json-ld", "n3", "nt"):
        try:
            graph.parse(data=text, format=fmt)
            return graph
        except Exception as exc:  # parser diagnostics are consolidated below
            errors.append(f"{fmt}: {exc}")
            graph = Graph()
    raise OntologyGovernanceError(f"invalid RDF in {name}: {errors[0]}")


def _rdf_list(graph: Graph, head: URIRef | BNode) -> list[Any]:
    values, current, seen = [], head, set()
    while current and current != RDF.nil:
        if current in seen:
            raise OntologyGovernanceError("cyclic RDF list in SHACL shape")
        seen.add(current)
        first = graph.value(current, RDF.first)
        if first is None:
            break
        values.append(first)
        current = graph.value(current, RDF.rest)
    return values


class ShaclCoreValidator:
    """Deterministic SHACL Core subset for release-critical constraints."""

    def validate(self, data_graph: Graph, shapes_graph: Graph) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        node_shapes = sorted(
            set(shapes_graph.subjects(RDF.type, SH.NodeShape)), key=str
        )
        for shape in node_shapes:
            targets = self._targets(data_graph, shapes_graph, shape)
            for predicate in shapes_graph.predicates(shape, None):
                allowed = {
                    RDF.type,
                    SH.targetNode,
                    SH.targetClass,
                    SH.targetSubjectsOf,
                    SH.targetObjectsOf,
                    SH.property,
                    SH.severity,
                    SH.message,
                    SH.name,
                    SH.order,
                    SH["class"],
                    SH.nodeKind,
                }
                if str(predicate).startswith(str(SH)) and predicate not in allowed:
                    unsupported.append(
                        self._unsupported(
                            shape,
                            shape,
                            f"unsupported SHACL node constraint: {predicate}",
                        )
                    )
            required_node_class = shapes_graph.value(shape, SH["class"])
            required_node_kind = shapes_graph.value(shape, SH.nodeKind)
            for focus in targets:
                if required_node_class and not self._is_instance_of(
                    data_graph, focus, required_node_class
                ):
                    findings.append(
                        self._node_finding(
                            shape,
                            focus,
                            "ClassConstraintComponent",
                            f"focus node must be an instance of {required_node_class}",
                        )
                    )
                if required_node_kind and not self._matches_node_kind(
                    focus, required_node_kind
                ):
                    findings.append(
                        self._node_finding(
                            shape,
                            focus,
                            "NodeKindConstraintComponent",
                            f"focus node must be {self._local(required_node_kind)}",
                        )
                    )
            for prop_shape in shapes_graph.objects(shape, SH.property):
                path = shapes_graph.value(prop_shape, SH.path)
                if not isinstance(path, URIRef):
                    unsupported.append(
                        self._unsupported(
                            shape, prop_shape, "only direct URI sh:path is supported"
                        )
                    )
                    continue
                for predicate in shapes_graph.predicates(prop_shape, None):
                    if predicate not in SUPPORTED_CONSTRAINTS | {
                        SH.path,
                        RDF.type,
                        SH.severity,
                        SH.message,
                        SH.name,
                        SH.order,
                    }:
                        if str(predicate).startswith(str(SH)):
                            unsupported.append(
                                self._unsupported(
                                    shape,
                                    prop_shape,
                                    f"unsupported SHACL constraint: {predicate}",
                                )
                            )
                for focus in targets:
                    values = list(data_graph.objects(focus, path))
                    findings.extend(
                        self._validate_property(
                            shapes_graph,
                            shape,
                            prop_shape,
                            focus,
                            path,
                            values,
                            data_graph,
                        )
                    )
        findings.extend(self._closed_world_conflicts(data_graph))
        for item in unsupported:
            item["finding_id"] = f"finding:{_digest(item)[:20]}"
        conforms = not findings and not unsupported
        return {
            "conforms": conforms,
            "findings": findings + unsupported,
            "summary": {
                "violations": sum(
                    1 for item in findings if item["severity"] == "Violation"
                ),
                "warnings": sum(
                    1 for item in findings if item["severity"] == "Warning"
                ),
                "unsupported_constraints": len(unsupported),
                "shapes_evaluated": len(node_shapes),
            },
        }

    def _targets(self, data: Graph, shapes: Graph, shape: Any) -> list[Any]:
        targets = set(shapes.objects(shape, SH.targetNode))
        for target_class in shapes.objects(shape, SH.targetClass):
            targets.update(
                subject
                for subject in data.subjects(RDF.type, None)
                if self._is_instance_of(data, subject, target_class)
            )
        for predicate in shapes.objects(shape, SH.targetSubjectsOf):
            targets.update(data.subjects(predicate, None))
        for predicate in shapes.objects(shape, SH.targetObjectsOf):
            targets.update(data.objects(None, predicate))
        return sorted(targets, key=str)

    def _validate_property(
        self,
        shapes: Graph,
        shape: Any,
        prop_shape: Any,
        focus: Any,
        path: URIRef,
        values: list[Any],
        data: Graph,
    ) -> list[dict[str, Any]]:
        findings = []
        severity = self._local(
            shapes.value(prop_shape, SH.severity)
            or shapes.value(shape, SH.severity)
            or SH.Violation
        )
        message = str(shapes.value(prop_shape, SH.message) or "")

        def add(component: str, value: Any = None, detail: str = "") -> None:
            finding = {
                "type": "shacl_violation",
                "severity": severity,
                "source_shape": str(shape),
                "property_shape": str(prop_shape),
                "focus_node": str(focus),
                "path": str(path),
                "value": None if value is None else str(value),
                "constraint_component": component,
                "message": message or detail,
            }
            finding["finding_id"] = f"finding:{_digest(finding)[:20]}"
            findings.append(finding)

        min_count = shapes.value(prop_shape, SH.minCount)
        max_count = shapes.value(prop_shape, SH.maxCount)
        if min_count is not None and len(values) < int(min_count):
            add(
                "MinCountConstraintComponent",
                detail=f"expected at least {int(min_count)} value(s), found {len(values)}",
            )
        if max_count is not None and len(values) > int(max_count):
            add(
                "MaxCountConstraintComponent",
                detail=f"expected at most {int(max_count)} value(s), found {len(values)}",
            )
        datatype = shapes.value(prop_shape, SH.datatype)
        required_class = shapes.value(prop_shape, SH["class"])
        node_kind = shapes.value(prop_shape, SH.nodeKind)
        allowed_head = shapes.value(prop_shape, SH["in"])
        allowed = set(_rdf_list(shapes, allowed_head)) if allowed_head else None
        for value in values:
            if datatype and (
                not isinstance(value, Literal)
                or (
                    value.datatype != datatype
                    and not (datatype == XSD.string and value.datatype is None)
                )
            ):
                add(
                    "DatatypeConstraintComponent",
                    value,
                    f"value must have datatype {datatype}",
                )
            if required_class and not self._is_instance_of(data, value, required_class):
                add(
                    "ClassConstraintComponent",
                    value,
                    f"value must be an instance of {required_class}",
                )
            if node_kind and not self._matches_node_kind(value, node_kind):
                add(
                    "NodeKindConstraintComponent",
                    value,
                    f"value must be {self._local(node_kind)}",
                )
            if allowed is not None and value not in allowed:
                add("InConstraintComponent", value, "value is outside the allowed set")
            self._validate_literal_ranges(shapes, prop_shape, value, add)
        return findings

    def _validate_literal_ranges(
        self, shapes: Graph, prop_shape: Any, value: Any, add
    ) -> None:
        if not isinstance(value, Literal):
            return
        native = value.toPython()
        for predicate, operator, component in (
            (SH.minInclusive, lambda a, b: a >= b, "MinInclusiveConstraintComponent"),
            (SH.maxInclusive, lambda a, b: a <= b, "MaxInclusiveConstraintComponent"),
        ):
            bound = shapes.value(prop_shape, predicate)
            if bound is not None:
                try:
                    valid = operator(native, bound.toPython())
                except TypeError:
                    valid = False
                if not valid:
                    add(
                        component,
                        value,
                        f"value violates {self._local(predicate)} {bound}",
                    )
        text = str(value)
        for predicate, operator, component in (
            (SH.minLength, lambda n, b: n >= b, "MinLengthConstraintComponent"),
            (SH.maxLength, lambda n, b: n <= b, "MaxLengthConstraintComponent"),
        ):
            bound = shapes.value(prop_shape, predicate)
            if bound is not None and not operator(len(text), int(bound)):
                add(
                    component,
                    value,
                    f"string length violates {self._local(predicate)} {bound}",
                )
        pattern = shapes.value(prop_shape, SH.pattern)
        if pattern is not None:
            import re

            if re.search(str(pattern), text) is None:
                add(
                    "PatternConstraintComponent",
                    value,
                    f"value does not match {pattern}",
                )

    def _closed_world_conflicts(self, data: Graph) -> list[dict[str, Any]]:
        findings = []
        disjoint = {
            (left, right) for left, right in data.subject_objects(OWL.disjointWith)
        }
        for left, right in disjoint:
            for entity in set(data.subjects(RDF.type, left)).intersection(
                data.subjects(RDF.type, right)
            ):
                item = {
                    "type": "owl_conflict",
                    "severity": "Violation",
                    "focus_node": str(entity),
                    "constraint_component": "DisjointClassesConflict",
                    "message": f"entity is typed as disjoint classes {left} and {right}",
                    "left_class": str(left),
                    "right_class": str(right),
                }
                item["finding_id"] = f"finding:{_digest(item)[:20]}"
                findings.append(item)
        for prop in data.subjects(RDF.type, OWL.FunctionalProperty):
            for subject in set(data.subjects(prop, None)):
                values = set(data.objects(subject, prop))
                if len(values) > 1:
                    item = {
                        "type": "owl_conflict",
                        "severity": "Violation",
                        "focus_node": str(subject),
                        "path": str(prop),
                        "constraint_component": "FunctionalPropertyConflict",
                        "message": f"functional property has {len(values)} distinct values",
                    }
                    item["finding_id"] = f"finding:{_digest(item)[:20]}"
                    findings.append(item)
        return findings

    @staticmethod
    def _matches_node_kind(value: Any, node_kind: Any) -> bool:
        return {
            SH.IRI: isinstance(value, URIRef),
            SH.BlankNode: isinstance(value, BNode),
            SH.Literal: isinstance(value, Literal),
            SH.BlankNodeOrIRI: isinstance(value, (BNode, URIRef)),
            SH.BlankNodeOrLiteral: isinstance(value, (BNode, Literal)),
            SH.IRIOrLiteral: isinstance(value, (URIRef, Literal)),
        }.get(node_kind, False)

    @staticmethod
    def _is_instance_of(graph: Graph, entity: Any, required_class: Any) -> bool:
        frontier = list(graph.objects(entity, RDF.type))
        seen = set()
        while frontier:
            candidate = frontier.pop()
            if candidate == required_class:
                return True
            if candidate not in seen:
                seen.add(candidate)
                frontier.extend(graph.objects(candidate, RDFS.subClassOf))
        return False

    @staticmethod
    def _local(value: Any) -> str:
        return str(value).rsplit("#", 1)[-1].rsplit("/", 1)[-1]

    def _unsupported(self, shape: Any, prop_shape: Any, message: str) -> dict[str, Any]:
        return {
            "type": "unsupported_shacl_constraint",
            "severity": "Violation",
            "source_shape": str(shape),
            "property_shape": str(prop_shape),
            "message": message,
        }

    def _node_finding(
        self, shape: Any, focus: Any, component: str, message: str
    ) -> dict[str, Any]:
        finding = {
            "type": "shacl_violation",
            "severity": "Violation",
            "source_shape": str(shape),
            "focus_node": str(focus),
            "constraint_component": component,
            "message": message,
        }
        finding["finding_id"] = f"finding:{_digest(finding)[:20]}"
        return finding


def validate_skos_graph(graph: Graph) -> list[dict[str, Any]]:
    """Return deterministic SKOS integrity findings for a merged RDF snapshot."""
    findings = []
    for concept in graph.subjects(RDF.type, SKOS.Concept):
        labels: dict[str, int] = {}
        for label in graph.objects(concept, SKOS.prefLabel):
            lang = label.language or ""
            labels[lang] = labels.get(lang, 0) + 1
        for lang, count in labels.items():
            if count > 1:
                item = {
                    "type": "skos_conflict",
                    "severity": "Violation",
                    "focus_node": str(concept),
                    "constraint_component": "UniquePrefLabelPerLanguage",
                    "message": f"concept has {count} prefLabel values for language '{lang}'",
                }
                item["finding_id"] = f"finding:{_digest(item)[:20]}"
                findings.append(item)
        if (concept, SKOS.broader, concept) in graph or (
            concept,
            SKOS.narrower,
            concept,
        ) in graph:
            item = {
                "type": "skos_conflict",
                "severity": "Violation",
                "focus_node": str(concept),
                "constraint_component": "SelfHierarchyConflict",
                "message": "concept cannot be broader/narrower than itself",
            }
            item["finding_id"] = f"finding:{_digest(item)[:20]}"
            findings.append(item)
        deprecated = graph.value(concept, OWL.deprecated)
        if deprecated and bool(deprecated.toPython()):
            replacements = set(graph.objects(concept, DCTERMS.isReplacedBy)) | set(
                graph.objects(concept, SKOS.exactMatch)
            )
            if not replacements:
                item = {
                    "type": "skos_conflict",
                    "severity": "Violation",
                    "focus_node": str(concept),
                    "constraint_component": "DeprecatedConceptRequiresReplacement",
                    "message": "deprecated concept must declare dcterms:isReplacedBy or skos:exactMatch",
                }
                item["finding_id"] = f"finding:{_digest(item)[:20]}"
                findings.append(item)
    broader = {
        concept: set(graph.objects(concept, SKOS.broader))
        for concept in graph.subjects(RDF.type, SKOS.Concept)
    }
    for concept in broader:
        stack, seen = list(broader.get(concept, set())), set()
        while stack:
            current = stack.pop()
            if current == concept:
                item = {
                    "type": "skos_conflict",
                    "severity": "Violation",
                    "focus_node": str(concept),
                    "constraint_component": "HierarchyCycleConflict",
                    "message": "skos:broader hierarchy contains a cycle",
                }
                item["finding_id"] = f"finding:{_digest(item)[:20]}"
                findings.append(item)
                break
            if current not in seen:
                seen.add(current)
                stack.extend(broader.get(current, set()))
    return findings


class OntologyGovernanceService:
    """Draft/review/approve/publish workflow with immutable review evidence."""

    def __init__(
        self, root: str | Path, decision_store: DecisionProvenanceStore | None = None
    ) -> None:
        self.root = Path(root)
        self.decision_store = decision_store or DecisionProvenanceStore(
            self.root / "decision_provenance.jsonl"
        )
        self.validator = ShaclCoreValidator()

    def create_draft(
        self,
        *,
        ontology_id: str,
        created_by: str,
        ontology_text: str,
        shapes_text: str,
        skos_text: str = "",
        base_version: str | None = None,
    ) -> dict[str, Any]:
        if not ontology_id.strip() or not created_by.strip():
            raise OntologyGovernanceError("ontology_id and created_by are required")
        _parse_rdf(ontology_text, name="ontology")
        _parse_rdf(shapes_text, name="shapes")
        if skos_text.strip():
            _parse_rdf(skos_text, name="skos")
        draft_id = f"draft:{uuid.uuid4()}"
        draft_dir = self._draft_dir(draft_id)
        draft_dir.mkdir(parents=True)
        (draft_dir / "ontology.ttl").write_text(ontology_text, encoding="utf-8")
        (draft_dir / "shapes.ttl").write_text(shapes_text, encoding="utf-8")
        (draft_dir / "skos.ttl").write_text(skos_text, encoding="utf-8")
        manifest = {
            "draft_id": draft_id,
            "ontology_id": ontology_id,
            "created_by": created_by,
            "created_at": _now(),
            "base_version": base_version,
            "state": "draft",
            "revision": 1,
        }
        self._write_json(draft_dir / "manifest.json", manifest)
        return manifest

    def update_draft(
        self,
        draft_id: str,
        *,
        actor: str,
        ontology_text: str | None = None,
        shapes_text: str | None = None,
        skos_text: str | None = None,
    ) -> dict[str, Any]:
        manifest = self.get_draft(draft_id)
        if manifest["state"] not in {"draft", "changes_requested"}:
            raise OntologyGovernanceError(
                f"draft cannot be edited in state {manifest['state']}"
            )
        draft_dir = self._draft_dir(draft_id)
        for name, text in (
            ("ontology.ttl", ontology_text),
            ("shapes.ttl", shapes_text),
            ("skos.ttl", skos_text),
        ):
            if text is not None:
                if name != "skos.ttl" or text.strip():
                    _parse_rdf(text, name=name)
                (draft_dir / name).write_text(text, encoding="utf-8")
        manifest.update(
            {
                "state": "draft",
                "revision": manifest["revision"] + 1,
                "updated_at": _now(),
                "updated_by": actor,
            }
        )
        self._write_json(draft_dir / "manifest.json", manifest)
        return manifest

    def validate_draft(self, draft_id: str, *, actor: str) -> dict[str, Any]:
        manifest = self.get_draft(draft_id)
        draft_dir = self._draft_dir(draft_id)
        data = _parse_rdf(
            (draft_dir / "ontology.ttl").read_text(encoding="utf-8"), name="ontology"
        )
        skos_text = (draft_dir / "skos.ttl").read_text(encoding="utf-8")
        if skos_text.strip():
            skos = _parse_rdf(skos_text, name="skos")
            for triple in skos:
                data.add(triple)
        shapes = _parse_rdf(
            (draft_dir / "shapes.ttl").read_text(encoding="utf-8"), name="shapes"
        )
        result = self.validator.validate(data, shapes)
        skos_findings = self._validate_skos(data)
        result["findings"].extend(skos_findings)
        result["summary"]["skos_conflicts"] = len(skos_findings)
        result["summary"]["violations"] = sum(
            1 for item in result["findings"] if item["severity"] == "Violation"
        )
        result["conforms"] = result["conforms"] and not result["findings"]
        result.update(
            {
                "review_id": f"review:{uuid.uuid4()}",
                "draft_id": draft_id,
                "revision": manifest["revision"],
                "actor": actor,
                "recorded_at": _now(),
            }
        )
        self._chain_append(draft_dir / "reviews.jsonl", result)
        input_hashes = {
            name: hashlib.sha256((draft_dir / name).read_bytes()).hexdigest()
            for name in ("ontology.ttl", "shapes.ttl", "skos.ttl")
        }
        parent_ids = (
            [manifest["changes_request_decision_id"]]
            if manifest.get("changes_request_decision_id")
            else []
        )
        validation_decision = self.decision_store.record(
            agent_id=actor,
            decision_type="ontology_validation",
            conclusion="conforms"
            if result["conforms"]
            else f"blocked_by_{len(result['findings'])}_finding(s)",
            rationale="SHACL Core, OWL conflict, and SKOS governance checks completed without modifying the draft.",
            parent_decision_ids=parent_ids,
            evidence=[
                {
                    "id": f"{draft_id}:{name}",
                    "type": "ontology_input",
                    "content_hash": digest,
                }
                for name, digest in input_hashes.items()
            ],
            policies=["policy:ontology-release-gate"],
            tags=["ontology", "validation"],
            output_entities=[
                {
                    "id": result["review_id"],
                    "type": "governance_review",
                    "content_hash": result["integrity"]["hash"],
                    "conforms": result["conforms"],
                }
            ],
            metadata={
                "finding_ids": [item["finding_id"] for item in result["findings"]],
                "summary": result["summary"],
            },
        )
        manifest.update(
            {
                "state": "validated" if result["conforms"] else "conflict_review",
                "latest_review_id": result["review_id"],
                "validated_at": _now(),
                "validation_decision_id": validation_decision["decision"]["id"],
            }
        )
        self._write_json(draft_dir / "manifest.json", manifest)
        result["decision_id"] = validation_decision["decision"]["id"]
        return result

    def waive_finding(
        self,
        draft_id: str,
        *,
        finding_id: str,
        actor: str,
        rationale: str,
        policy: str,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        if not rationale.strip() or not policy.strip():
            raise OntologyGovernanceError("waiver rationale and policy are required")
        review = self._latest_review(draft_id)
        if finding_id not in {item["finding_id"] for item in review["findings"]}:
            raise OntologyGovernanceError(
                f"finding not found in latest review: {finding_id}"
            )
        waiver = {
            "waiver_id": f"waiver:{uuid.uuid4()}",
            "finding_id": finding_id,
            "draft_id": draft_id,
            "review_id": review["review_id"],
            "actor": actor,
            "rationale": rationale,
            "policy": policy,
            "expires_at": expires_at,
            "recorded_at": _now(),
        }
        self._chain_append(self._draft_dir(draft_id) / "waivers.jsonl", waiver)
        manifest = self.get_draft(draft_id)
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="ontology_finding_waiver",
            conclusion=f"waived {finding_id}",
            rationale=rationale,
            parent_decision_ids=[manifest["validation_decision_id"]],
            evidence=[
                {
                    "id": review["review_id"],
                    "type": "governance_review",
                    "content_hash": review["integrity"]["hash"],
                }
            ],
            policies=[policy],
            tags=["ontology", "waiver"],
            output_entities=[
                {
                    "id": waiver["waiver_id"],
                    "type": "governance_waiver",
                    "content_hash": waiver["integrity"]["hash"],
                }
            ],
            metadata={"finding_id": finding_id, "expires_at": expires_at},
        )
        manifest.setdefault("waiver_decision_ids", []).append(
            decision["decision"]["id"]
        )
        self._write_json(self._draft_dir(draft_id) / "manifest.json", manifest)
        waiver["decision_id"] = decision["decision"]["id"]
        return waiver

    def request_changes(
        self, draft_id: str, *, reviewer: str, rationale: str
    ) -> dict[str, Any]:
        manifest = self.get_draft(draft_id)
        if manifest["state"] not in {"validated", "conflict_review"}:
            raise OntologyGovernanceError(
                f"changes cannot be requested in state {manifest['state']}"
            )
        review = self._latest_review(draft_id)
        decision = self.decision_store.record(
            agent_id=reviewer,
            decision_type="ontology_changes_requested",
            conclusion="changes_requested",
            rationale=rationale,
            parent_decision_ids=[manifest["validation_decision_id"]],
            evidence=[
                {
                    "id": review["review_id"],
                    "type": "shacl_review",
                    "content_hash": review["integrity"]["hash"],
                }
            ],
            policies=["policy:ontology-release-gate"],
            tags=["ontology", "review"],
            output_entities=[
                {
                    "id": draft_id,
                    "type": "ontology_draft",
                    "revision": manifest["revision"],
                }
            ],
        )
        action = {
            "action_id": f"action:{uuid.uuid4()}",
            "type": "changes_requested",
            "draft_id": draft_id,
            "revision": manifest["revision"],
            "review_id": review["review_id"],
            "reviewer": reviewer,
            "rationale": rationale,
            "decision_id": decision["decision"]["id"],
            "recorded_at": _now(),
        }
        self._chain_append(self._draft_dir(draft_id) / "actions.jsonl", action)
        manifest.update(
            {
                "state": "changes_requested",
                "changes_requested_at": _now(),
                "changes_requested_by": reviewer,
                "changes_request_decision_id": decision["decision"]["id"],
            }
        )
        self._write_json(self._draft_dir(draft_id) / "manifest.json", manifest)
        return {"manifest": manifest, "action": action, "decision": decision}

    def approve(
        self,
        draft_id: str,
        *,
        approver: str,
        rationale: str,
        policies: Iterable[str] = (),
    ) -> dict[str, Any]:
        manifest = self.get_draft(draft_id)
        if self._actor_subject(approver) == self._actor_subject(
            str(manifest["created_by"])
        ):
            raise OntologyGovernanceError(
                "separation of duties: draft creator cannot approve the release"
            )
        review = self._latest_review(draft_id)
        review_integrity = self._verify_chain(
            self._draft_dir(draft_id) / "reviews.jsonl"
        )
        waiver_integrity = self._verify_chain(
            self._draft_dir(draft_id) / "waivers.jsonl"
        )
        if not review_integrity["valid"] or not waiver_integrity["valid"]:
            raise OntologyGovernanceError(
                "approval blocked because review or waiver evidence integrity is invalid"
            )
        if review["revision"] != manifest["revision"]:
            raise OntologyGovernanceError(
                "latest validation does not cover the current draft revision"
            )
        waived = {
            item["finding_id"]
            for item in self._read_jsonl(self._draft_dir(draft_id) / "waivers.jsonl")
        }
        unresolved = [
            item
            for item in review["findings"]
            if item["severity"] == "Violation" and item["finding_id"] not in waived
        ]
        if unresolved:
            raise OntologyGovernanceError(
                f"approval blocked by {len(unresolved)} unresolved violation(s)"
            )
        evidence = [
            {
                "id": review["review_id"],
                "type": "shacl_review",
                "content_hash": review["integrity"]["hash"],
            }
        ]
        for waiver in self._read_jsonl(self._draft_dir(draft_id) / "waivers.jsonl"):
            evidence.append(
                {
                    "id": waiver["waiver_id"],
                    "type": "governance_waiver",
                    "content_hash": waiver["integrity"]["hash"],
                }
            )
        decision = self.decision_store.record(
            agent_id=approver,
            decision_type="ontology_release_approval",
            conclusion="approved",
            rationale=rationale,
            evidence=evidence,
            parent_decision_ids=[
                manifest["validation_decision_id"],
                *manifest.get("waiver_decision_ids", []),
            ],
            policies=list(policies),
            tags=["ontology", "approval"],
            output_entities=[
                {
                    "id": draft_id,
                    "type": "ontology_draft",
                    "revision": manifest["revision"],
                }
            ],
        )
        manifest.update(
            {
                "state": "approved",
                "approved_at": _now(),
                "approved_by": approver,
                "approval_decision_id": decision["decision"]["id"],
            }
        )
        self._write_json(self._draft_dir(draft_id) / "manifest.json", manifest)
        return {"manifest": manifest, "decision": decision}

    def publish(self, draft_id: str, *, actor: str) -> dict[str, Any]:
        manifest = self.get_draft(draft_id)
        if manifest["state"] != "approved":
            raise OntologyGovernanceError("only an approved draft can be published")
        if self._actor_subject(actor) == self._actor_subject(
            str(manifest["approved_by"])
        ):
            raise OntologyGovernanceError(
                "separation of duties: approver cannot publish the release"
            )
        draft_dir = self._draft_dir(draft_id)
        contents = {
            name: (draft_dir / name).read_text(encoding="utf-8")
            for name in ("ontology.ttl", "shapes.ttl", "skos.ttl")
        }
        content_hashes = {
            name: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for name, text in contents.items()
        }
        version = f"v-{_digest(content_hashes)[:12]}"
        release_dir = self.root / "published" / manifest["ontology_id"] / version
        if release_dir.exists():
            raise OntologyGovernanceError(
                f"ontology version already published: {version}"
            )
        release_dir.mkdir(parents=True)
        for name, text in contents.items():
            (release_dir / name).write_text(text, encoding="utf-8")
        previous = self._latest_release(manifest["ontology_id"])
        changes = self._diff_release(previous, contents)
        release = {
            "ontology_id": manifest["ontology_id"],
            "ontology_version": version,
            "shape_version": f"shapes-{content_hashes['shapes.ttl'][:12]}",
            "skos_version": f"skos-{content_hashes['skos.ttl'][:12]}",
            "draft_id": draft_id,
            "revision": manifest["revision"],
            "published_at": _now(),
            "published_by": actor,
            "approval_decision_id": manifest["approval_decision_id"],
            "content_hashes": content_hashes,
            "previous_version": previous["ontology_version"] if previous else None,
            "change_set": changes,
        }
        release["integrity"] = {"algorithm": "sha256", "hash": _digest(release)}
        self._write_json(release_dir / "manifest.json", release)
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="ontology_publish",
            conclusion=f"published {version}",
            rationale="Approved ontology draft passed governance gate and was published immutably.",
            parent_decision_ids=[manifest["approval_decision_id"]],
            evidence=[{"id": manifest["latest_review_id"], "type": "shacl_review"}],
            policies=["policy:ontology-release-gate"],
            tags=["ontology", "publish"],
            output_entities=[
                {
                    "id": f"ontology:{manifest['ontology_id']}:{version}",
                    "type": "ontology_release",
                    "content_hash": release["integrity"]["hash"],
                }
            ],
        )
        release["publish_decision_id"] = decision["decision"]["id"]
        self._write_json(release_dir / "manifest.json", release)
        manifest.update(
            {
                "state": "published",
                "published_version": version,
                "publish_decision_id": decision["decision"]["id"],
            }
        )
        self._write_json(draft_dir / "manifest.json", manifest)
        return {"release": release, "decision": decision}

    def impact_preview(self, draft_id: str) -> dict[str, Any]:
        manifest = self.get_draft(draft_id)
        draft_dir = self._draft_dir(draft_id)
        contents = {
            name: (draft_dir / name).read_text(encoding="utf-8")
            for name in ("ontology.ttl", "shapes.ttl", "skos.ttl")
        }
        previous = self._latest_release(manifest["ontology_id"])
        changes = self._diff_release(previous, contents)
        changed_terms = sorted(
            {
                item[2]
                for group in ("added", "removed")
                for item in changes[group]
                if item[1].endswith(
                    (
                        "subClassOf",
                        "type",
                        "broader",
                        "narrower",
                        "exactMatch",
                        "prefLabel",
                    )
                )
            }
        )
        return {
            "draft_id": draft_id,
            "base_version": previous["ontology_version"] if previous else None,
            "change_set": changes,
            "potentially_affected_terms": changed_terms,
            "requires_revalidation": bool(changes["added"] or changes["removed"]),
        }

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        path = self._draft_dir(draft_id) / "manifest.json"
        if not path.exists():
            raise OntologyGovernanceError(f"draft not found: {draft_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_drafts(self) -> list[dict[str, Any]]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((self.root / "drafts").glob("*/manifest.json"))
        ]

    def get_draft_bundle(self, draft_id: str) -> dict[str, Any]:
        manifest = self.get_draft(draft_id)
        draft_dir = self._draft_dir(draft_id)
        return {
            "manifest": manifest,
            "ontology_text": (draft_dir / "ontology.ttl").read_text(encoding="utf-8"),
            "shapes_text": (draft_dir / "shapes.ttl").read_text(encoding="utf-8"),
            "skos_text": (draft_dir / "skos.ttl").read_text(encoding="utf-8"),
            "reviews": self._read_jsonl(draft_dir / "reviews.jsonl"),
            "waivers": self._read_jsonl(draft_dir / "waivers.jsonl"),
            "actions": self._read_jsonl(draft_dir / "actions.jsonl"),
        }

    def get_release(self, ontology_id: str, version: str) -> dict[str, Any]:
        if any(part in ontology_id or part in version for part in ("/", "\\", "..")):
            raise OntologyGovernanceError("invalid ontology id or version")
        release_dir = self.root / "published" / ontology_id / version
        manifest_path = release_dir / "manifest.json"
        if not manifest_path.exists():
            raise OntologyGovernanceError(
                f"ontology release not found: {ontology_id}/{version}"
            )
        return {
            "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
            "ontology_text": (release_dir / "ontology.ttl").read_text(encoding="utf-8"),
            "shapes_text": (release_dir / "shapes.ttl").read_text(encoding="utf-8"),
            "skos_text": (release_dir / "skos.ttl").read_text(encoding="utf-8"),
        }

    def list_releases(self, ontology_id: str | None = None) -> list[dict[str, Any]]:
        base = self.root / "published"
        paths = (
            base.glob(f"{ontology_id}/*/manifest.json")
            if ontology_id
            else base.glob("*/*/manifest.json")
        )
        releases = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        return sorted(releases, key=lambda item: item["published_at"], reverse=True)

    def workbench_summary(self) -> dict[str, Any]:
        drafts = self.list_drafts()
        releases = self.list_releases()
        state_counts: dict[str, int] = {}
        findings = {"total": 0, "unresolved": 0, "waived": 0}
        evidence_valid = True
        for draft in drafts:
            state = str(draft["state"])
            state_counts[state] = state_counts.get(state, 0) + 1
            draft_dir = self._draft_dir(str(draft["draft_id"]))
            reviews = self._read_jsonl(draft_dir / "reviews.jsonl")
            waivers = self._read_jsonl(draft_dir / "waivers.jsonl")
            waived = {str(item["finding_id"]) for item in waivers}
            if reviews:
                latest = reviews[-1]
                latest_findings = latest.get("findings", [])
                findings["total"] += len(latest_findings)
                findings["waived"] += sum(
                    1 for item in latest_findings if item.get("finding_id") in waived
                )
                findings["unresolved"] += sum(
                    1
                    for item in latest_findings
                    if item.get("severity") == "Violation"
                    and item.get("finding_id") not in waived
                )
            evidence_valid = evidence_valid and self._verify_chain(
                draft_dir / "reviews.jsonl"
            )["valid"]
            evidence_valid = evidence_valid and self._verify_chain(
                draft_dir / "waivers.jsonl"
            )["valid"]
        payload = {
            "api_version": "aof.ontology-workbench-summary/v1",
            "draft_count": len(drafts),
            "release_count": len(releases),
            "state_counts": state_counts,
            "findings": findings,
            "evidence_integrity": {
                "valid": evidence_valid,
                "decision_ledger": self.decision_store.verify_integrity(),
            },
            "recent_drafts": sorted(
                drafts,
                key=lambda item: item.get("updated_at", item.get("created_at", "")),
                reverse=True,
            )[:8],
            "recent_releases": releases[:8],
        }
        return {**payload, "snapshot_digest": _digest(payload)}

    def audit_trail(self, draft_id: str) -> dict[str, Any]:
        manifest = self.get_draft(draft_id)
        decision_id = next(
            (
                manifest.get(field)
                for field in (
                    "publish_decision_id",
                    "approval_decision_id",
                    "validation_decision_id",
                    "changes_request_decision_id",
                )
                if manifest.get(field)
            ),
            None,
        )
        if decision_id is None:
            return {
                "api_version": "aof.ontology-audit-trail/v1",
                "draft_id": draft_id,
                "decision_id": None,
                "causal_chain": {"nodes": [], "edges": []},
                "integrity": self.decision_store.verify_integrity(),
            }
        return {
            "api_version": "aof.ontology-audit-trail/v1",
            "draft_id": draft_id,
            "decision_id": decision_id,
            **self.decision_store.audit_trail(str(decision_id)),
        }

    def _validate_skos(self, graph: Graph) -> list[dict[str, Any]]:
        return validate_skos_graph(graph)

    @staticmethod
    def _actor_subject(actor: str) -> str:
        return actor.rsplit(":", 1)[-1]

    def _diff_release(
        self, previous: dict[str, Any] | None, contents: dict[str, str]
    ) -> dict[str, Any]:
        current_graph = Graph()
        for name, text in contents.items():
            if text.strip() and name != "shapes.ttl":
                for triple in _parse_rdf(text, name=name):
                    current_graph.add(triple)
        previous_graph = Graph()
        if previous:
            release_dir = (
                self.root
                / "published"
                / previous["ontology_id"]
                / previous["ontology_version"]
            )
            for name in ("ontology.ttl", "skos.ttl"):
                text = (release_dir / name).read_text(encoding="utf-8")
                if text.strip():
                    for triple in _parse_rdf(text, name=name):
                        previous_graph.add(triple)
        def encode(triple: tuple[Any, Any, Any]) -> list[str]:
            return [str(part) for part in triple]
        return {
            "added": sorted(
                (encode(t) for t in set(current_graph) - set(previous_graph))
            ),
            "removed": sorted(
                (encode(t) for t in set(previous_graph) - set(current_graph))
            ),
        }

    def _latest_release(self, ontology_id: str) -> dict[str, Any] | None:
        manifests = list(
            (self.root / "published" / ontology_id).glob("*/manifest.json")
        )
        releases = [json.loads(path.read_text(encoding="utf-8")) for path in manifests]
        return (
            max(releases, key=lambda item: item["published_at"]) if releases else None
        )

    def _latest_review(self, draft_id: str) -> dict[str, Any]:
        reviews = self._read_jsonl(self._draft_dir(draft_id) / "reviews.jsonl")
        if not reviews:
            raise OntologyGovernanceError(
                "draft must be validated before approval or waiver"
            )
        return reviews[-1]

    def _draft_dir(self, draft_id: str) -> Path:
        if not draft_id.startswith("draft:") or any(
            char in draft_id for char in ("/", "\\", "..")
        ):
            raise OntologyGovernanceError("invalid draft id")
        return self.root / "drafts" / draft_id

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _append_jsonl(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(value) + "\n")

    def _chain_append(self, path: Path, value: dict[str, Any]) -> None:
        entries = self._read_jsonl(path)
        value["previous_hash"] = entries[-1]["integrity"]["hash"] if entries else None
        value["integrity"] = {
            "algorithm": "sha256",
            "hash": _digest(
                {key: item for key, item in value.items() if key != "integrity"}
            ),
        }
        self._append_jsonl(path, value)

    def _verify_chain(self, path: Path) -> dict[str, Any]:
        previous = None
        for index, entry in enumerate(self._read_jsonl(path), start=1):
            payload = {key: value for key, value in entry.items() if key != "integrity"}
            if entry.get("previous_hash") != previous or entry.get("integrity", {}).get(
                "hash"
            ) != _digest(payload):
                return {"valid": False, "entries_checked": index}
            previous = entry["integrity"]["hash"]
        return {
            "valid": True,
            "entries_checked": index if "index" in locals() else 0,
            "head_hash": previous,
        }

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        return (
            [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if path.exists()
            else []
        )
