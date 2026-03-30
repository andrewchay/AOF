#!/usr/bin/env python3
"""Build regression case library from docs + feedback + mapping catalog."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

TEXT_SUFFIXES = {".md", ".txt", ".sql"}


def slugify(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "topic"


def load_text(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="ignore")
    parts: list[str] = []
    for p in sorted(path.rglob("*")):
        if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
            parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n\n".join(parts)


def extract_sql_cases(text: str, topic: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    # collect comments as potential question hints
    lines = text.splitlines()
    comment_map: dict[int, str] = {}
    for i, line in enumerate(lines, start=1):
        s = line.strip()
        if s.startswith("--"):
            comment_map[i] = s[2:].strip()

    sql_blocks = list(re.finditer(r"(?is)select\s+.*?;", text))
    for idx, m in enumerate(sql_blocks, start=1):
        sql = re.sub(r"\s+", " ", m.group(0)).strip()
        start_pos = text[: m.start()].count("\n") + 1

        hint = ""
        for ln in range(start_pos, max(start_pos - 8, 0), -1):
            if ln in comment_map:
                hint = comment_map[ln]
                break
        question = hint or f"{topic} SQL case {idx}"

        must_contain = []
        for token in ["logdate", "date_sub", "count(distinct", "group by", "where", "join", "regexp_like"]:
            if token in sql.lower():
                must_contain.append(token)

        cases.append(
            {
                "id": f"case_{idx:03d}",
                "question": question,
                "context": {"topic": topic},
                "expected_sql": sql,
                "checks": {
                    "must_contain": sorted(set(must_contain)),
                    "must_not_contain": [],
                },
                "expected_entities": [],
                "severity": "medium",
                "source": "docs_sql",
            }
        )
    return cases


def extract_feedback_cases(feedback_jsonl: Path | None, base_idx: int, topic: str) -> list[dict[str, Any]]:
    if not feedback_jsonl or not feedback_jsonl.exists():
        return []

    out: list[dict[str, Any]] = []
    i = base_idx
    for line in feedback_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:
            continue

        action = str(row.get("action", "")).strip()
        term = str(row.get("term", row.get("name", ""))).strip()
        if not action or not term:
            continue

        i += 1
        out.append(
            {
                "id": f"case_{i:03d}",
                "question": f"反馈修正规则: {action}:{term}",
                "context": {"topic": topic, "feedback_action": action},
                "expected_sql": "",
                "checks": {
                    "must_contain": [],
                    "must_not_contain": [],
                },
                "expected_entities": [term],
                "severity": "high",
                "source": "feedback",
            }
        )
    return out


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_summary(rows: list[dict[str, Any]], path: Path, topic: str) -> None:
    source_count: dict[str, int] = {}
    for r in rows:
        source = str(r.get("source", "unknown"))
        source_count[source] = source_count.get(source, 0) + 1

    lines = [
        f"# 回归样例库摘要：{topic}",
        "",
        f"- 样例总数：`{len(rows)}`",
        "",
        "## 来源分布",
        "",
    ]
    for k, v in sorted(source_count.items()):
        lines.append(f"- {k}: `{v}`")

    lines += [
        "",
        "## 使用建议",
        "",
        "1. 每次线上问题都补一条高严重度 case。",
        "2. 每次口径调整都更新对应 case 的 checks。",
        "3. 发布前跑全量回归，失败即阻断。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build regression library from docs+feedback")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--docs-path", required=True)
    parser.add_argument("--feedback-jsonl", default="")
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    topic = slugify(args.topic)
    docs_path = Path(args.docs_path).resolve()
    if not docs_path.exists():
        raise SystemExit(f"docs path not found: {docs_path}")

    feedback = Path(args.feedback_jsonl).resolve() if args.feedback_jsonl else None

    text = load_text(docs_path)
    cases = extract_sql_cases(text, topic)
    cases += extract_feedback_cases(feedback, len(cases), topic)

    out_jsonl = Path(args.output_jsonl).resolve()
    out_md = Path(args.output_md).resolve()
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)

    write_jsonl(cases, out_jsonl)
    write_summary(cases, out_md, topic)

    print("[done] regression library")
    print(f"- regression_jsonl: {out_jsonl}")
    print(f"- regression_summary: {out_md}")
    print(f"- cases: {len(cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
