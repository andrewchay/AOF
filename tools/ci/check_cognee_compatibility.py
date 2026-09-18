# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""Verify the installed Cognee distribution exposes every API used by AOF."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE_FILE = ROOT / "config/capabilities/integration-profiles.json"

REQUIRED_API: dict[str, tuple[str, ...]] = {
    "cognee": ("add", "cognify", "search"),
    "cognee.modules.search.types": ("SearchType",),
    "cognee.modules.users.methods": ("get_default_user",),
    "cognee.modules.data.methods": (
        "delete_data",
        "get_authorized_dataset",
        "get_authorized_existing_datasets",
        "get_dataset_data",
        "has_dataset_data",
    ),
    "cognee.modules.memify": ("memify",),
    "cognee.modules.pipelines.operations.get_pipeline_status": (
        "get_pipeline_status",
    ),
    "cognee.modules.ontology.rdf_xml.RDFLibOntologyResolver": (
        "RDFLibOntologyResolver",
    ),
    "cognee.modules.ontology.matching_strategies": ("FuzzyMatchingStrategy",),
    "cognee.infrastructure.databases.relational.config": ("get_relational_config",),
    "cognee.infrastructure.databases.relational": ("get_relational_engine",),
    "cognee.infrastructure.databases.graph.neo4j_driver.adapter": ("Neo4jAdapter",),
    "cognee.api.v1.visualize.visualize": ("visualize_graph",),
}


def expected_version() -> str:
    document = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    capabilities = document["profiles"]["production"]["capabilities"]
    cognee = next(item for item in capabilities if item["id"] == "cognee")
    return str(cognee["probe"]["version"])


def check() -> list[str]:
    failures: list[str] = []
    installed = importlib.metadata.version("cognee")
    expected = expected_version()
    if installed != expected:
        failures.append(f"cognee version {installed} does not match {expected}")
    for module_name, symbols in REQUIRED_API.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # Dependency import failures are compatibility failures.
            failures.append(f"{module_name} import failed: {type(exc).__name__}: {exc}")
            continue
        for symbol in symbols:
            if not hasattr(module, symbol):
                failures.append(f"{module_name}.{symbol} is unavailable")
    return failures


def main() -> int:
    failures = check()
    print(
        json.dumps(
            {
                "schema_version": "aof.cognee-compatibility/v1",
                "expected_version": expected_version(),
                "compatible": not failures,
                "failures": failures,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
