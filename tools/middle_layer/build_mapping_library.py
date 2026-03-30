#!/usr/bin/env python3
"""Build business mapping library from docs + metadata + feedback.

Outputs:
- term_mapping.yaml
- metric_catalog.yaml
- dimension_mapping.yaml
- sql_pattern_library.yaml
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEXT_SUFFIXES = {".md", ".txt", ".sql", ".json", ".csv"}


@dataclass
class Metric:
    metric_id: str
    cn_name: str
    agg: str
    measure: str
    base_table: str
    required_filters: list[str]


def slugify(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "topic"


def norm_name(text: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower())
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "item"


def load_texts(path: Path) -> list[tuple[Path, str]]:
    files: list[Path] = []
    if path.is_file():
        files = [path]
    else:
        files = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES]

    out: list[tuple[Path, str]] = []
    for f in sorted(files):
        try:
            out.append((f, f.read_text(encoding="utf-8", errors="ignore")))
        except Exception:
            continue
    return out


def extract_sql_metrics(text: str) -> list[Metric]:
    metrics: list[Metric] = []
    sql_blocks = re.findall(r"(?is)select\s+.*?;", text)
    table_re = re.compile(r"\bfrom\s+([`\"\[]?[\w\.]+[`\"\]]?)", re.IGNORECASE)
    alias_re = re.compile(r"\bas\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
    filter_re = re.compile(r"\bwhere\b(.*?)(?:\bgroup\s+by\b|\border\s+by\b|;)", re.IGNORECASE | re.DOTALL)

    for sql in sql_blocks:
        table = "unknown_table"
        tm = table_re.search(sql)
        if tm:
            table = tm.group(1).strip("`\"[]")

        aliases = alias_re.findall(sql)
        filters: list[str] = []
        fm = filter_re.search(sql)
        if fm:
            raw = re.sub(r"\s+", " ", fm.group(1)).strip()
            filters = [x.strip() for x in raw.split("AND") if x.strip()][:8]

        for a in aliases:
            aid = norm_name(a)
            metrics.append(
                Metric(
                    metric_id=aid,
                    cn_name=a,
                    agg="count",
                    measure="*",
                    base_table=table,
                    required_filters=filters,
                )
            )

    # de-dup by metric_id
    uniq: dict[str, Metric] = {}
    for m in metrics:
        uniq[m.metric_id] = m
    return list(uniq.values())


def load_metadata(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    obj = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(obj, dict) and isinstance(obj.get("tables"), list):
        return obj["tables"]
    if isinstance(obj, list):
        return obj
    return []


def load_feedback_terms(path: Path | None) -> list[str]:
    if not path or not path.exists():
        return []
    terms: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:
            continue
        term = str(row.get("term", "")).strip()
        if term:
            terms.append(term)
    return sorted(set(terms))


def dump_yaml_list(rows: list[dict[str, Any]]) -> str:
    def fmt(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if v is None:
            return "null"
        if isinstance(v, (int, float)):
            return str(v)
        s = str(v)
        if s == "" or any(ch in s for ch in [":", "#", "'", '"', "[", "]", "{", "}", "\n"]):
            return json.dumps(s, ensure_ascii=False)
        return s

    lines: list[str] = []
    for row in rows:
        lines.append("-")
        for k, v in row.items():
            if isinstance(v, list):
                lines.append(f"  {k}:")
                for item in v:
                    lines.append(f"  - {fmt(item)}")
            else:
                lines.append(f"  {k}: {fmt(v)}")
    return "\n".join(lines) + ("\n" if lines else "")


def build_mapping_library(topic: str, docs_path: Path, metadata_json: Path | None, feedback_jsonl: Path | None, output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    texts = load_texts(docs_path)
    merged_text = "\n\n".join(t for _, t in texts)

    metrics = extract_sql_metrics(merged_text)
    tables = load_metadata(metadata_json)
    feedback_terms = load_feedback_terms(feedback_jsonl)

    # term_mapping
    term_rows: list[dict[str, Any]] = []
    for m in metrics:
        term_rows.append(
            {
                "term": m.cn_name,
                "canonical_metric": m.metric_id,
                "aliases": [],
                "grain": "day",
                "domain": topic,
            }
        )
    for t in feedback_terms:
        term_rows.append(
            {
                "term": t,
                "canonical_entity": norm_name(t),
                "aliases": [],
                "domain": topic,
            }
        )

    # de-dup by term
    uniq_term: dict[str, dict[str, Any]] = {}
    for r in term_rows:
        uniq_term[str(r.get("term", ""))] = r
    term_rows = list(uniq_term.values())

    metric_rows = [
        {
            "metric_id": m.metric_id,
            "cn_name": m.cn_name,
            "agg": m.agg,
            "measure": m.measure,
            "base_table": m.base_table,
            "required_filters": m.required_filters,
        }
        for m in metrics
    ]

    dim_rows: list[dict[str, Any]] = []
    for t in tables:
        tname = str(t.get("name", t.get("table", ""))).strip()
        cols = t.get("columns", [])
        if isinstance(cols, list):
            for c in cols:
                if isinstance(c, dict):
                    cname = str(c.get("name", c.get("column", ""))).strip()
                    ctype = str(c.get("type", c.get("data_type", "string"))).strip() or "string"
                else:
                    cname = str(c).strip()
                    ctype = "string"
                if not cname:
                    continue
                dim_rows.append(
                    {
                        "business_dim": cname,
                        "physical_field": f"{tname}.{cname}" if tname else cname,
                        "type": ctype,
                    }
                )

    # sql pattern templates (generic)
    pattern_rows: list[dict[str, Any]] = [
        {
            "pattern_id": "metric_by_date_v1",
            "intent": "按日期统计指标",
            "sql_template": "SELECT ${date_field}, ${agg_expr} FROM ${table} WHERE ${filters} GROUP BY ${date_field}",
            "required_slots": ["date_field", "agg_expr", "table", "filters"],
        },
        {
            "pattern_id": "window_range_metric_v1",
            "intent": "时间窗口指标统计",
            "sql_template": "SELECT ${agg_expr} FROM ${table} WHERE ${date_field} BETWEEN ${start_date} AND ${end_date} AND ${filters}",
            "required_slots": ["agg_expr", "table", "date_field", "start_date", "end_date", "filters"],
        },
    ]

    term_file = output_dir / "term_mapping.yaml"
    metric_file = output_dir / "metric_catalog.yaml"
    dim_file = output_dir / "dimension_mapping.yaml"
    pattern_file = output_dir / "sql_pattern_library.yaml"

    term_file.write_text(dump_yaml_list(term_rows), encoding="utf-8")
    metric_file.write_text(dump_yaml_list(metric_rows), encoding="utf-8")
    dim_file.write_text(dump_yaml_list(dim_rows), encoding="utf-8")
    pattern_file.write_text(dump_yaml_list(pattern_rows), encoding="utf-8")

    return {
        "term_mapping": str(term_file),
        "metric_catalog": str(metric_file),
        "dimension_mapping": str(dim_file),
        "sql_pattern_library": str(pattern_file),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build mapping library from docs/metadata/feedback")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--docs-path", required=True)
    parser.add_argument("--metadata-json", default="")
    parser.add_argument("--feedback-jsonl", default="")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    topic_slug = slugify(args.topic)
    docs_path = Path(args.docs_path).resolve()
    if not docs_path.exists():
        raise SystemExit(f"docs path not found: {docs_path}")

    metadata_json = Path(args.metadata_json).resolve() if args.metadata_json else None
    feedback_jsonl = Path(args.feedback_jsonl).resolve() if args.feedback_jsonl else None
    output_dir = Path(args.output_dir).resolve()

    out = build_mapping_library(topic_slug, docs_path, metadata_json, feedback_jsonl, output_dir)
    print("[done] mapping library")
    for k, v in out.items():
        print(f"- {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
