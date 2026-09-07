#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Export editable feedback candidates from ontology alignment report.

This bridges the "user evaluation" step in the loop:
- Reads TEST_DATA alignment report JSON.
- Extracts unresolved unmatched terms (latest iteration with unmatched > 0).
- Writes feedback JSONL template entries for manual confirmation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _latest_unmatched_iteration(report: dict[str, Any]) -> dict[str, Any] | None:
    iters = report.get("iterations", [])
    unresolved = [it for it in iters if int(it.get("unmatched_count", 0)) > 0]
    if not unresolved:
        return None
    return unresolved[-1]


def export_candidates(report_json: Path, output_jsonl: Path, output_md: Path, max_terms: int) -> tuple[int, str]:
    report = json.loads(report_json.read_text(encoding="utf-8"))
    topic = report.get("topic_slug") or report.get("topic") or "topic"

    it = _latest_unmatched_iteration(report)
    if not it:
        output_jsonl.write_text("", encoding="utf-8")
        output_md.write_text(
            "\n".join(
                [
                    f"# TEST_DATA 用户评价清单：{topic}",
                    "",
                    "- 当前对齐状态：`aligned`（无待处理 unmatched 项）",
                    "- `feedback.jsonl` 已生成空文件（可手动补充业务修正）",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return 0, "aligned"

    unmatched = it.get("unmatched", [])[:max_terms]
    lines = []
    rows = []
    for i, item in enumerate(unmatched, start=1):
        term = str(item.get("term", "")).strip()
        category = str(item.get("category", "unknown")).strip()
        if not term:
            continue

        rec = {
            "action": "map_term",
            "term": term,
            "class": "TODO_CLASS",
            "note": f"iter={it.get('iteration')} category={category}; choose map_term/add_class/add_individual as needed",
        }
        lines.append(json.dumps(rec, ensure_ascii=False))
        rows.append((i, term, category, "map_term -> TODO_CLASS"))

    output_jsonl.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    md = [
        f"# TEST_DATA 用户评价清单：{topic}",
        "",
        f"- 来源迭代：`Iter {it.get('iteration')}`",
        f"- unmatched 数：`{it.get('unmatched_count', 0)}`",
        f"- 已导出草稿：`{output_jsonl}`",
        "",
        "## 待评审项",
        "",
        "| # | term | category | 建议草稿 |",
        "|---|---|---|---|",
    ]
    for idx, term, category, draft in rows:
        md.append(f"| {idx} | `{term}` | `{category}` | `{draft}` |")

    md += [
        "",
        "## 使用方式",
        "",
        "1. 编辑 JSONL 中每一行，把 `TODO_CLASS` 改成你确认的类。",
        "2. 如需新增类/关系，改成 `add_class`/`add_relation`/`add_individual` 动作。",
        "3. 下次运行 ontology factory 时传 `--feedback-jsonl` 即可自动注入。",
        "",
    ]
    output_md.write_text("\n".join(md), encoding="utf-8")
    return len(lines), "need_feedback"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export feedback candidates from alignment report")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--output-jsonl", default="")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--max-terms", type=int, default=200)
    args = parser.parse_args()

    report_json = Path(args.report_json).resolve()
    if not report_json.exists():
        raise SystemExit(f"report json not found: {report_json}")

    stem = report_json.stem.replace("alignment_report", "feedback_candidates")
    out_jsonl = Path(args.output_jsonl).resolve() if args.output_jsonl else report_json.with_name(f"{stem}.jsonl")
    out_md = Path(args.output_md).resolve() if args.output_md else report_json.with_name(f"{stem}.md")

    count, status = export_candidates(report_json, out_jsonl, out_md, args.max_terms)
    print(f"[status] {status}")
    print(f"[candidates] {count}")
    print(f"[jsonl] {out_jsonl}")
    print(f"[md] {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
