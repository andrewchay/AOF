#!/usr/bin/env python3
"""Apply user feedback patches to TEST_DATA ontology.

Implements the feedback-driven iteration pattern from AOF methodology.
See docs/internal/methodology/00-META-02_迭代工作流.md for process details.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
EX_NS = "http://example.org/ontology#"
OWL_NS = "http://www.w3.org/2002/07/owl#"

ET.register_namespace("ex", EX_NS)
ET.register_namespace("rdf", RDF_NS)
ET.register_namespace("rdfs", RDFS_NS)

RDF = "{" + RDF_NS + "}"
RDFS = "{" + RDFS_NS + "}"


def _safe_name(raw: str) -> str:
    out = []
    for ch in raw.strip():
        if ch.isalnum() or ch in {"_", ".", "-"}:
            out.append(ch)
        else:
            out.append("_")
    name = "".join(out).strip("_")
    return name or "item"


def _has_about(root: ET.Element, name: str) -> bool:
    target = EX_NS + name
    for d in root.findall(RDF + "Description"):
        if d.attrib.get(RDF + "about") == target:
            return True
    return False


def _get_or_create_desc(root: ET.Element, name: str) -> ET.Element:
    target = EX_NS + name
    for d in root.findall(RDF + "Description"):
        if d.attrib.get(RDF + "about") == target:
            return d
    d = ET.Element(RDF + "Description", {RDF + "about": target})
    root.append(d)
    return d


def _ensure_type(desc: ET.Element, type_uri: str) -> bool:
    for t in desc.findall(RDF + "type"):
        if t.attrib.get(RDF + "resource") == type_uri:
            return False
    ET.SubElement(desc, RDF + "type", {RDF + "resource": type_uri})
    return True


def _ensure_obj(desc: ET.Element, pred: str, target_name: str) -> bool:
    pred_tag = "{" + EX_NS + "}" + pred
    target_uri = EX_NS + target_name
    for ch in desc.findall(pred_tag):
        if ch.attrib.get(RDF + "resource") == target_uri:
            return False
    ET.SubElement(desc, pred_tag, {RDF + "resource": target_uri})
    return True


def _add_comment(desc: ET.Element, text: str) -> bool:
    c = ET.SubElement(desc, RDFS + "comment")
    c.text = text
    return True


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            rows.append(json.loads(s))
    return rows


def _apply(root: ET.Element, patch: dict, dry_run: bool) -> str:
    action = patch.get("action", "").strip()
    if not action:
        return "skip:missing_action"

    if action == "add_class":
        name = _safe_name(str(patch.get("name", "")))
        if not name:
            return "skip:bad_name"
        if _has_about(root, name):
            return f"noop:class_exists:{name}"
        if dry_run:
            return f"dryrun:add_class:{name}"
        d = _get_or_create_desc(root, name)
        _ensure_type(d, OWL_NS + "Class")
        if patch.get("comment"):
            _add_comment(d, str(patch["comment"]))
        return f"ok:add_class:{name}"

    if action == "add_individual":
        name = _safe_name(str(patch.get("name", "")))
        cls = _safe_name(str(patch.get("class", "Process")))
        if not name:
            return "skip:bad_name"
        if dry_run:
            return f"dryrun:add_individual:{name}:{cls}"
        d = _get_or_create_desc(root, name)
        _ensure_type(d, EX_NS + cls)
        _ensure_type(d, OWL_NS + "NamedIndividual")
        if patch.get("comment"):
            _add_comment(d, str(patch["comment"]))
        return f"ok:add_individual:{name}:{cls}"

    if action == "add_relation":
        src = _safe_name(str(patch.get("source", "")))
        pred = _safe_name(str(patch.get("predicate", "")))
        tgt = _safe_name(str(patch.get("target", "")))
        if not src or not pred or not tgt:
            return "skip:bad_relation"
        if dry_run:
            return f"dryrun:add_relation:{src}-{pred}->{tgt}"
        sd = _get_or_create_desc(root, src)
        _ensure_obj(sd, pred, tgt)
        return f"ok:add_relation:{src}-{pred}->{tgt}"

    if action == "map_term":
        term = _safe_name(str(patch.get("term", "")))
        raw_cls = str(patch.get("class", "")).strip()
        if not term:
            return "skip:bad_term"
        if not raw_cls or raw_cls.upper().startswith("TODO"):
            return f"skip:unresolved_class:{term}"

        cls = _safe_name(raw_cls)
        if cls.lower() in {"todo_class", "todo", "tbd", "unknown"}:
            return f"skip:unresolved_class:{term}"

        if dry_run:
            return f"dryrun:map_term:{term}:{cls}"
        d = _get_or_create_desc(root, term)
        _ensure_type(d, EX_NS + cls)
        _ensure_type(d, OWL_NS + "NamedIndividual")
        _add_comment(d, f"TEST_DATA feedback map_term -> {cls}")
        return f"ok:map_term:{term}:{cls}"

    return f"skip:unknown_action:{action}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply feedback patches to ontology")
    parser.add_argument("--ontology-file", required=True)
    parser.add_argument("--feedback-jsonl", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ontology_file = Path(args.ontology_file).resolve()
    feedback_file = Path(args.feedback_jsonl).resolve()

    if not ontology_file.exists():
        raise SystemExit(f"ontology file not found: {ontology_file}")
    if not feedback_file.exists():
        raise SystemExit(f"feedback file not found: {feedback_file}")

    tree = ET.parse(ontology_file)
    root = tree.getroot()
    patches = _load_jsonl(feedback_file)

    results = []
    for p in patches:
        results.append(_apply(root, p, args.dry_run))

    if not args.dry_run:
        ET.indent(root, space="  ")
        tree.write(ontology_file, encoding="UTF-8", xml_declaration=True)

    print(f"[patches] total={len(patches)} dry_run={args.dry_run}")
    for r in results:
        print(f"- {r}")
    if not args.dry_run:
        print(f"[saved] {ontology_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
