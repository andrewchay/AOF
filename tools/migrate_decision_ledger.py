#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W02.01 — Migrate a JSONL decision ledger into the SQLite repository.

Usage:
    python tools/migrate_decision_ledger.py <input.jsonl> [output.sqlite]

The script validates chain integrity before import, quarantines corrupt or
tenant-less records, and prints a migration manifest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.persistence.decision_ledger_repository import (
    SQLiteDecisionLedgerRepository,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate JSONL decision ledger to SQLite")
    parser.add_argument("input", type=Path, help="Path to legacy JSONL ledger")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=None,
        help="Path to SQLite database (default: <input>.sqlite)",
    )
    args = parser.parse_args()

    input_path: Path = args.input
    output_path: Path = args.output or input_path.with_suffix(".sqlite")

    if not input_path.exists():
        print(json.dumps({"error": f"input not found: {input_path}"}, indent=2))
        return 1

    repo = SQLiteDecisionLedgerRepository(output_path)
    manifest = repo.migrate_from_jsonl(input_path)

    print(json.dumps(manifest, indent=2, ensure_ascii=False))

    if manifest["quarantined"] > 0:
        print(
            f"\n⚠️  {manifest['quarantined']} record(s) quarantined. "
            f"Inspect table 'decision_ledger_quarantine' in {output_path}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
