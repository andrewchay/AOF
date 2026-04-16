#!/usr/bin/env python3
"""Detect and sanitize suspicious external URIs in OWL ontologies."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
OWL_NS = "http://www.w3.org/2002/07/owl#"

RDF_ABOUT = f"{{{RDF_NS}}}about"
RDF_RESOURCE = f"{{{RDF_NS}}}resource"

ALLOWED_PREFIXES_DEFAULT = (
    "http://www.w3.org/",
    "https://www.w3.org/",
    "http://purl.org/",
    "https://purl.org/",
    "http://example.org/ontology#",
    "https://example.org/ontology#",
    "urn:",
)

ALLOWED_DOMAINS_DEFAULT = {
    "www.w3.org",
    "w3.org",
    "purl.org",
    "example.org",
}

OWL_ENTITY_TAGS = {
    f"{{{OWL_NS}}}Class",
    f"{{{OWL_NS}}}ObjectProperty",
    f"{{{OWL_NS}}}DatatypeProperty",
    f"{{{OWL_NS}}}NamedIndividual",
    f"{{{OWL_NS}}}AnnotationProperty",
    f"{{{OWL_NS}}}FunctionalProperty",
}


def _is_http_uri(uri: str) -> bool:
    parsed = urlparse(uri)
    return parsed.scheme in {"http", "https"}


def _is_allowed_uri(
    uri: str,
    allowed_domains: set[str],
    allowed_prefixes: tuple[str, ...],
) -> bool:
    if uri.startswith(allowed_prefixes):
        return True
    if not _is_http_uri(uri):
        return True
    host = (urlparse(uri).hostname or "").lower()
    return host in allowed_domains


def find_external_entity_uris(
    owl_path: Path,
    allowed_domains: set[str] | None = None,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    """
    Find suspicious URIs used as ontology entities.

    Only checks entity-defining locations (about/resource on OWL entity tags).
    """
    allowed_domains = set(allowed_domains or ALLOWED_DOMAINS_DEFAULT)
    allowed_prefixes = tuple(allowed_prefixes or ALLOWED_PREFIXES_DEFAULT)
    findings: list[dict[str, str]] = []

    tree = ET.parse(owl_path)
    root = tree.getroot()

    for elem in root.iter():
        if elem.tag in OWL_ENTITY_TAGS:
            iri = elem.attrib.get(RDF_ABOUT, "")
            if iri and not _is_allowed_uri(iri, allowed_domains, allowed_prefixes):
                findings.append(
                    {
                        "iri": iri,
                        "host": (urlparse(iri).hostname or ""),
                        "element": elem.tag,
                        "attribute": "rdf:about",
                    }
                )

            iri_res = elem.attrib.get(RDF_RESOURCE, "")
            if iri_res and not _is_allowed_uri(iri_res, allowed_domains, allowed_prefixes):
                findings.append(
                    {
                        "iri": iri_res,
                        "host": (urlparse(iri_res).hostname or ""),
                        "element": elem.tag,
                        "attribute": "rdf:resource",
                    }
                )

    return findings


def _slugify(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "entity"


def sanitize_ontology_uris(
    owl_path: Path,
    base_namespace: str = "http://example.org/ontology#",
    allowed_domains: set[str] | None = None,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> dict[str, str]:
    """Rewrite suspicious entity URIs into local base namespace."""
    findings = find_external_entity_uris(
        owl_path=owl_path,
        allowed_domains=allowed_domains,
        allowed_prefixes=allowed_prefixes,
    )
    if not findings:
        return {}

    tree = ET.parse(owl_path)
    root = tree.getroot()

    mapping: dict[str, str] = {}
    used_targets: set[str] = set()

    for item in findings:
        old = item["iri"]
        if old in mapping:
            continue
        parsed = urlparse(old)
        seed = parsed.fragment or Path(parsed.path).name or "entity"
        slug = _slugify(seed)
        target = f"{base_namespace}{slug}"
        i = 2
        while target in used_targets:
            target = f"{base_namespace}{slug}_{i}"
            i += 1
        mapping[old] = target
        used_targets.add(target)

    for elem in root.iter():
        for attr in (RDF_ABOUT, RDF_RESOURCE):
            value = elem.attrib.get(attr)
            if value and value in mapping:
                elem.attrib[attr] = mapping[value]

    tree.write(owl_path, encoding="UTF-8", xml_declaration=True)
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect/sanitize suspicious OWL entity URIs")
    parser.add_argument("--owl-file", help="single OWL file")
    parser.add_argument("--owl-dir", help="scan all *.owl under directory")
    parser.add_argument("--allow-domain", action="append", default=[])
    parser.add_argument("--allow-prefix", action="append", default=[])
    parser.add_argument("--rewrite", action="store_true", help="rewrite suspicious URIs in-place")
    parser.add_argument("--base-namespace", default="http://example.org/ontology#")
    parser.add_argument("--report-json", default="")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when suspicious URIs exist")
    args = parser.parse_args()

    files: list[Path] = []
    if args.owl_file:
        files.append(Path(args.owl_file).resolve())
    if args.owl_dir:
        files.extend(sorted(Path(args.owl_dir).resolve().glob("*.owl")))
    if not files:
        raise SystemExit("Please provide --owl-file or --owl-dir")

    allow_domains = set(ALLOWED_DOMAINS_DEFAULT)
    allow_domains.update(d.lower() for d in args.allow_domain)

    allow_prefixes = list(ALLOWED_PREFIXES_DEFAULT)
    allow_prefixes.extend(args.allow_prefix)
    allow_prefixes_tuple = tuple(allow_prefixes)

    report: dict[str, dict[str, object]] = {}
    total = 0
    for fp in files:
        if not fp.exists():
            report[str(fp)] = {"error": "file not found", "findings": []}
            continue
        findings = find_external_entity_uris(
            fp,
            allowed_domains=allow_domains,
            allowed_prefixes=allow_prefixes_tuple,
        )
        total += len(findings)
        mapping = {}
        if args.rewrite and findings:
            mapping = sanitize_ontology_uris(
                fp,
                base_namespace=args.base_namespace,
                allowed_domains=allow_domains,
                allowed_prefixes=allow_prefixes_tuple,
            )
        report[str(fp)] = {
            "findings": findings,
            "rewritten": mapping,
            "count": len(findings),
        }
        print(f"[scan] {fp.name}: suspicious={len(findings)} rewritten={len(mapping)}")

    if args.report_json:
        out = Path(args.report_json).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[report] {out}")

    if args.strict and total > 0:
        print(f"[block] suspicious URIs found: {total}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

