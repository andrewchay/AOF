#!/usr/bin/env python3
"""Analyze ontology-factory alignment report and summarize recurring error patterns.

Based on AOF quality-control methodology.
See docs/internal/methodology/00-META-10_质量控制.md for theoretical foundation.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def _load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _category_bucket(term: str, category: str) -> str:
    t = term.lower()
    if category == "classes":
        if any(x in t for x in ["query", "sql"]):
            return "查询概念缺失"
        if any(x in t for x in ["metric", "count", "dau", "mau", "cnt"]):
            return "指标概念缺失"
        if any(x in t for x in ["table", "schema", "db"]):
            return "数据表概念缺失"
        return "通用类概念缺失"

    if "." in term and "_" in term:
        return "表名实例缺失"
    if any(x in t for x in ["date", "id", "tag", "type", "region"]):
        return "字段实例缺失"
    if any(x in t for x in ["cnt", "dau", "mau", "metric"]):
        return "指标实例缺失"
    if any(x in t for x in ["community", "platform"]):
        return "平台实例缺失"
    return "通用实例缺失"


def analyze(report: dict) -> dict:
    iters = report.get("iterations", [])
    by_bucket = defaultdict(int)
    by_term = defaultdict(int)
    round_stats = []

    for it in iters:
        unmatched = it.get("unmatched", [])
        round_stats.append(
            {
                "iteration": it.get("iteration"),
                "matched_count": it.get("matched_count", 0),
                "unmatched_count": it.get("unmatched_count", 0),
            }
        )
        for u in unmatched:
            term = str(u.get("term", ""))
            cat = str(u.get("category", ""))
            bucket = _category_bucket(term, cat)
            by_bucket[bucket] += 1
            by_term[(term, cat)] += 1

    top_buckets = sorted(by_bucket.items(), key=lambda x: x[1], reverse=True)
    top_terms = sorted(by_term.items(), key=lambda x: x[1], reverse=True)

    return {
        "topic": report.get("topic"),
        "status": report.get("status"),
        "round_stats": round_stats,
        "bucket_counts": [{"bucket": b, "count": c} for b, c in top_buckets],
        "top_terms": [
            {"term": term, "category": cat, "count": cnt}
            for (term, cat), cnt in top_terms
        ],
    }


def to_markdown(analysis: dict) -> str:
    lines = []
    lines.append(f"# TEST_DATA 对齐模式分析：{analysis.get('topic', 'unknown')}")
    lines.append("")
    lines.append(f"- 状态：`{analysis.get('status', 'unknown')}`")
    lines.append("")
    lines.append("## 轮次概览")
    lines.append("")
    for r in analysis.get("round_stats", []):
        lines.append(
            f"- Iter {r['iteration']}: matched={r['matched_count']}, unmatched={r['unmatched_count']}"
        )
    lines.append("")
    lines.append("## 错误模式分桶")
    lines.append("")
    for b in analysis.get("bucket_counts", []):
        lines.append(f"- {b['bucket']}: {b['count']}")
    lines.append("")
    lines.append("## 高频未匹配项")
    lines.append("")
    for t in analysis.get("top_terms", [])[:20]:
        lines.append(f"- `{t['term']}` ({t['category']}): {t['count']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze ontology factory alignment patterns")
    parser.add_argument("--report-json", required=True, help="Path to TEST_DATA alignment report json")
    parser.add_argument("--output-json", required=True, help="Output analysis json")
    parser.add_argument("--output-md", required=True, help="Output analysis markdown")
    args = parser.parse_args()

    report_path = Path(args.report_json).resolve()
    out_json = Path(args.output_json).resolve()
    out_md = Path(args.output_md).resolve()

    report = _load_report(report_path)
    analysis = analyze(report)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(to_markdown(analysis), encoding="utf-8")

    print(f"[ok] analysis_json={out_json}")
    print(f"[ok] analysis_md={out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
