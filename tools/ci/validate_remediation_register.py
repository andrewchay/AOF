#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""Validate the remediation register as a trustworthy release input.

The default mode validates structure and truthful progress bookkeeping while
allowing implementation work to remain open.  ``--require-complete`` is the
product-release gate: every item must satisfy the independent closure rules.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ALLOWED_STATUSES = {"not_started", "in_progress", "mitigated", "closed"}
COUNT_KEYS = ("closed", "in_progress", "mitigated", "not_started")


class DuplicateKeyError(ValueError):
    """Raised when JSON contains a duplicate object key."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(path: Path, *, require_complete: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        document = json.loads(path.read_text(), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError, DuplicateKeyError) as exc:
        return [f"cannot load register: {exc}"]

    if not isinstance(document, dict):
        return ["register root must be a JSON object"]
    if document.get("schema_version") != "aof.remediation-plan/v1":
        errors.append("schema_version must be 'aof.remediation-plan/v1'")

    findings = document.get("finding_ids")
    packages = document.get("work_packages")
    items = document.get("items")
    if not isinstance(findings, list) or not all(_nonempty(x) for x in findings):
        errors.append("finding_ids must be a non-empty string list")
        findings = []
    if len(findings) != len(set(findings)):
        errors.append("finding_ids contains duplicates")
    if not isinstance(packages, list):
        errors.append("work_packages must be a list")
        packages = []
    if not isinstance(items, list):
        errors.append("items must be a list")
        items = []

    package_ids = [p.get("id") for p in packages if isinstance(p, dict)]
    if len(package_ids) != len(set(package_ids)):
        errors.append("work package IDs must be unique")
    package_set = {x for x in package_ids if isinstance(x, str)}
    for package in packages:
        if not isinstance(package, dict):
            errors.append("every work package must be an object")
            continue
        package_id = package.get("id", "<missing>")
        if not _nonempty(package.get("title")) or not _nonempty(package.get("owner_role")):
            errors.append(f"{package_id}: title and owner_role are required")
        dependencies = package.get("depends_on")
        if not isinstance(dependencies, list) or any(x not in package_set for x in dependencies):
            errors.append(f"{package_id}: depends_on references an unknown package")
        if package_id in (dependencies or []):
            errors.append(f"{package_id}: package cannot depend on itself")
        package_findings = package.get("finding_ids")
        if not isinstance(package_findings, list) or any(x not in findings for x in package_findings):
            errors.append(f"{package_id}: finding_ids references an unknown finding")

    item_ids = [i.get("id") for i in items if isinstance(i, dict)]
    if len(item_ids) != len(set(item_ids)):
        errors.append("item IDs must be unique")
    covered_findings: set[str] = set()
    statuses: Counter[str] = Counter()
    repository_root = path.parents[3] if len(path.parents) > 3 else None
    for item in items:
        if not isinstance(item, dict):
            errors.append("every item must be an object")
            continue
        item_id = item.get("id", "<missing>")
        package_id = item.get("work_package")
        status = item.get("status")
        if package_id not in package_set:
            errors.append(f"{item_id}: unknown work_package {package_id!r}")
        if isinstance(item_id, str) and isinstance(package_id, str) and not item_id.startswith(f"{package_id}."):
            errors.append(f"{item_id}: ID does not belong to {package_id}")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{item_id}: invalid status {status!r}")
            continue
        statuses[status] += 1
        for field in ("title", "owner_role", "acceptance"):
            if not _nonempty(item.get(field)):
                errors.append(f"{item_id}: {field} is required")
        item_findings = item.get("finding_ids")
        if not isinstance(item_findings, list) or any(x not in findings for x in item_findings):
            errors.append(f"{item_id}: finding_ids references an unknown finding")
        else:
            covered_findings.update(item_findings)
        dependencies = item.get("depends_on_packages")
        if not isinstance(dependencies, list) or any(x not in package_set for x in dependencies):
            errors.append(f"{item_id}: depends_on_packages references an unknown package")
        tests = item.get("tests")
        evidence = item.get("evidence")
        if not isinstance(tests, list) or not all(_nonempty(x) for x in tests):
            errors.append(f"{item_id}: tests must be a string list")
            tests = []
        elif repository_root is not None:
            for test_ref in tests:
                match = re.match(r"(tests/[^\s:()]+\.py)", test_ref)
                if match and not (repository_root / match.group(1)).is_file():
                    errors.append(f"{item_id}: test file does not exist: {match.group(1)}")
        if not isinstance(evidence, list) or not all(_nonempty(x) for x in evidence):
            errors.append(f"{item_id}: evidence must be a string list")
            evidence = []
        if status == "not_started" and (_nonempty(item.get("implementation_commit")) or tests):
            errors.append(f"{item_id}: not_started but has implementation evidence")
        if status in {"in_progress", "mitigated"} and not (
            _nonempty(item.get("implementation_commit")) or tests or evidence
        ):
            errors.append(f"{item_id}: {status} requires implementation evidence")
        if status == "closed":
            owner = item.get("owner_person")
            reviewer = item.get("reviewer")
            if not _nonempty(owner):
                errors.append(f"{item_id}: closed item requires owner_person")
            if not _nonempty(reviewer) or str(reviewer).strip().lower().startswith("self"):
                errors.append(f"{item_id}: closed item requires an independent reviewer")
            elif owner == reviewer:
                errors.append(f"{item_id}: reviewer must differ from owner_person")
            for field in ("implementation_commit", "closed_at"):
                if not _nonempty(item.get(field)):
                    errors.append(f"{item_id}: closed item requires {field}")
            if not tests:
                errors.append(f"{item_id}: closed item requires tests")
            if not evidence:
                errors.append(f"{item_id}: closed item requires evidence")

    missing_findings = set(findings) - covered_findings
    if missing_findings:
        errors.append(f"findings have no remediation item: {sorted(missing_findings)}")

    assessment = document.get("assessment")
    if not isinstance(assessment, dict):
        errors.append("assessment object is required")
    else:
        if not _nonempty(assessment.get("assessed_commit")):
            errors.append("assessment.assessed_commit is required")
        if not _nonempty(assessment.get("assessed_at")):
            errors.append("assessment.assessed_at is required")
        recorded = assessment.get("status_counts")
        expected = {key: statuses[key] for key in COUNT_KEYS}
        if recorded != expected:
            errors.append(f"assessment.status_counts is stale: recorded={recorded!r}, actual={expected!r}")

    if require_complete and any(item.get("status") != "closed" for item in items if isinstance(item, dict)):
        errors.append("--require-complete requires every item to be closed")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the AOF remediation register")
    parser.add_argument("register", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    errors = validate(args.register, require_complete=args.require_complete)
    if errors:
        print("remediation register: INVALID")
        for error in errors:
            print(f"  FAIL {error}")
        return 1
    document = json.loads(args.register.read_text())
    counts = Counter(item["status"] for item in document["items"])
    print(
        "remediation register: valid "
        f"({len(document['items'])} items; "
        + ", ".join(f"{key}={counts[key]}" for key in COUNT_KEYS)
        + ")"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
