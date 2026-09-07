#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Normalize DB exports (CSV/JSON/SQL) into AOF-ingestable text/JSONL."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(r) for r in reader]


def load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)

    if isinstance(obj, list):
        out: list[dict[str, Any]] = []
        for i, item in enumerate(obj):
            if isinstance(item, dict):
                out.append(item)
            else:
                out.append({"value": item, "_index": i})
        return out

    if isinstance(obj, dict):
        if "data" in obj and isinstance(obj["data"], list):
            return [x if isinstance(x, dict) else {"value": x} for x in obj["data"]]
        return [obj]

    return [{"value": obj}]


def load_sql(path: Path) -> list[dict[str, Any]]:
    """Lightweight SQL export parser.

    Current support focuses on INSERT statements and emits one record per statement.
    This is intentionally conservative for template use.
    """
    text = path.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []

    insert_pattern = re.compile(
        r"INSERT\s+INTO\s+([`\"\[]?[\w\.]+[`\"\]]?)\s*(\([^;]*?\))?\s*VALUES\s*(.*?);",
        flags=re.IGNORECASE | re.DOTALL,
    )

    for m in insert_pattern.finditer(text):
        table = m.group(1)
        columns = (m.group(2) or "").strip()
        values_blob = (m.group(3) or "").strip()
        records.append(
            {
                "_kind": "insert",
                "_table": table,
                "_columns": columns,
                "_values_sql": values_blob,
            }
        )

    # Fallback for analytic SQL snippets: keep each SELECT statement as a record.
    if not records:
        stmt_chunks = [s.strip() for s in text.split(";") if s.strip()]
        for chunk in stmt_chunks:
            if re.search(r"\bSELECT\b", chunk, flags=re.IGNORECASE):
                # Try to infer primary table from FROM clause.
                fm = re.search(
                    r"\bFROM\s+([`\"\[]?[\w\.]+[`\"\]]?)",
                    chunk,
                    flags=re.IGNORECASE,
                )
                table = fm.group(1) if fm else "sql_query"
                records.append(
                    {
                        "_kind": "select",
                        "_table": table,
                        "_query_sql": chunk + ";",
                    }
                )

    return records


def _format_record_text(source: str, table: str | None, row: dict[str, Any], idx: int) -> str:
    pairs = []
    for k, v in row.items():
        if k.startswith("_"):
            continue
        val = _safe_str(v).strip()
        if val:
            pairs.append(f"{k}={val}")

    meta = [f"source={source}", f"index={idx}"]
    if table:
        meta.append(f"table={table}")

    head = "[" + ", ".join(meta) + "]"
    body = "; ".join(pairs) if pairs else _safe_str(row)
    return f"{head} {body}".strip()


def _format_mixed_record(row: dict[str, Any], idx: int) -> str:
    """格式化混合文档记录为文本。"""
    rec_kind = row.get("_kind", "unknown")
    lines = []

    if rec_kind == "code":
        # 代码块
        lang = row.get("_lang", "unknown")
        code = row.get("_query_sql", "")
        annotation = row.get("_context_annotation", "")

        lines.append(f"[{rec_kind}] lang={lang}")
        if annotation:
            lines.append(f"[context] {annotation[:200]}")
        lines.append("[code]")
        lines.append(code)

    elif rec_kind in ("annotation", "text"):
        # 文本/标注
        annot_type = row.get("_annotation_type", "text")
        concepts = row.get("_concepts", [])
        text = row.get("_text", "")

        lines.append(f"[{rec_kind}] type={annot_type}")
        if concepts:
            lines.append(f"[concepts] {', '.join(concepts[:10])}")
        lines.append("[text]")
        lines.append(text[:500])  # 限制长度

    elif rec_kind == "annotated_code":
        # 代码+标注对
        code = row.get("_code", "")
        annotation = row.get("_annotation", "")
        tags = row.get("_tags", [])

        lines.append(f"[{rec_kind}]")
        if tags:
            lines.append(f"[tags] {', '.join(tags)}")
        if annotation:
            lines.append(f"[annotation] {annotation}")
        lines.append("[code]")
        lines.append(code)

    else:
        # 默认格式
        lines.append(f"[{rec_kind}]")
        text = row.get("text", "")
        if text:
            lines.append(text[:500])

    return "\n".join(lines)


def detect_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".csv"}:
        return "csv"
    if ext in {".json", ".jsonl"}:
        # 检查是否是 Jupyter Notebook
        content = path.read_text(encoding="utf-8", errors="ignore")[:500]
        if '"cells"' in content and '"metadata"' in content:
            return "mixed"
        return "json"
    if ext in {".sql"}:
        return "sql"
    # 混合文档类型
    if ext in {".ipynb", ".md", ".markdown", ".annot", ".xml", ".yaml", ".yml"}:
        return "mixed"
    # 代码文件（带注释提取）
    if ext in {".py", ".js", ".ts", ".java", ".go", ".rs"}:
        return "mixed"
    # 尝试检测是否为混合格式
    content = path.read_text(encoding="utf-8", errors="ignore")[:1000]
    if "```" in content or "<snippet" in content:
        return "mixed"
    # 尝试检测 YAML 格式
    if _looks_like_yaml(content):
        return "mixed"
    raise ValueError(f"无法自动识别输入类型: {path}")


def _looks_like_yaml(content: str) -> bool:
    """启发式检测是否为 YAML 格式。"""
    lines = content.strip().split("\n")[:30]
    yaml_indicators = 0
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        # 键值对 (key: value)
        if __import__('re').match(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*:", stripped):
            yaml_indicators += 1
        # 列表项 (- item)
        elif stripped.startswith("- ") and ":" in stripped[:50]:
            yaml_indicators += 1
        # 文档分隔符
        elif stripped == "---":
            yaml_indicators += 2
    return yaml_indicators >= 3


def load_mixed(path: Path) -> list[dict[str, Any]]:
    """加载混合文档（代码+文本标注）。"""
    from mixed_document_parser import MixedDocumentParser

    parser = MixedDocumentParser(path)
    records = parser.parse()

    # 转换为标准格式
    result = []
    for i, rec in enumerate(records, start=1):
        rec_type = rec.get("type", "unknown")

        if rec_type == "code":
            # 代码块 - 提取代码内容
            result.append({
                "_kind": "code",
                "_table": rec.get("_table") or rec.get("lang", "unknown"),
                "_lang": rec.get("lang", "unknown"),
                "_query_sql": rec.get("text", ""),
                "_context_annotation": rec.get("context_annotation", ""),
                "_source_index": i,
                **rec,  # 保留原始字段
            })
        elif rec_type in ("annotation", "text"):
            # 文本/标注 - 提取概念
            result.append({
                "_kind": "annotation",
                "_table": "documentation",
                "_annotation_type": rec.get("annotation_type", "text"),
                "_concepts": rec.get("extracted_concepts", []),
                "_text": rec.get("text", ""),
                "_source_index": i,
                **rec,
            })
        elif rec_type == "code_with_annotation":
            # 代码+标注对
            result.append({
                "_kind": "annotated_code",
                "_table": "annotated",
                "_code": rec.get("code", ""),
                "_annotation": rec.get("annotation", ""),
                "_tags": rec.get("tags", []),
                "_concepts": rec.get("extracted_concepts", []),
                "_source_index": i,
                **rec,
            })
        else:
            # 其他类型
            result.append({
                "_kind": rec_type,
                "_source_index": i,
                **rec,
            })

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize DB export or mixed documents for AOF",
        epilog="""
支持格式:
  - CSV/JSON/SQL: 结构化数据
  - Jupyter (.ipynb): 代码 + Markdown 注释
  - Markdown (.md): 代码块 + 文本说明
  - XML/annot: <snippet code="..." annotation="..."/>
  - 代码文件 (.py, .js, etc.): 代码 + 注释提取
        """
    )
    parser.add_argument("--input", required=True, help="Path to input file")
    parser.add_argument("--kind", choices=["auto", "csv", "json", "sql", "mixed"], default="auto")
    parser.add_argument("--output-txt", required=True, help="Output text file for aof_add --data-path")
    parser.add_argument("--output-jsonl", help="Optional output jsonl for traceability")
    parser.add_argument("--table", default="", help="Optional logical table name override")
    parser.add_argument("--max-records", type=int, default=0, help="Limit records (0 means no limit)")

    args = parser.parse_args()
    in_path = Path(args.input).resolve()
    out_txt = Path(args.output_txt).resolve()
    out_jsonl = Path(args.output_jsonl).resolve() if args.output_jsonl else None

    if not in_path.exists():
        raise SystemExit(f"输入文件不存在: {in_path}")

    kind = detect_kind(in_path) if args.kind == "auto" else args.kind

    if kind == "csv":
        raw_rows = load_csv(in_path)
    elif kind == "json":
        raw_rows = load_json(in_path)
    elif kind == "mixed":
        raw_rows = load_mixed(in_path)
    else:
        raw_rows = load_sql(in_path)

    if args.max_records > 0:
        raw_rows = raw_rows[: args.max_records]

    rows: list[dict[str, Any]] = []
    for i, r in enumerate(raw_rows, start=1):
        table = args.table or r.get("_table") or in_path.stem

        # 混合文档的特殊文本格式化
        if kind == "mixed":
            text = _format_mixed_record(r, i)
        else:
            text = _format_record_text(kind, table, r, i)

        rows.append(
            {
                "id": f"{table}-{i}",
                "source_kind": kind,
                "table": table,
                "text": text,
                "raw": r,
            }
        )

    out_txt.parent.mkdir(parents=True, exist_ok=True)
    with out_txt.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(r["text"] + "\n")

    if out_jsonl:
        out_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with out_jsonl.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[ok] normalized={len(rows)}")
    print(f"[txt] {out_txt}")
    if out_jsonl:
        print(f"[jsonl] {out_jsonl}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
