#!/usr/bin/env python3
"""Generate skills update suggestions from feedback + mapping + regression."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_feedback(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except Exception:
            continue
    return out


def load_regression(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except Exception:
            continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build skills update suggestions")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--feedback-jsonl", default="")
    parser.add_argument("--regression-jsonl", default="")
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    feedback = load_feedback(Path(args.feedback_jsonl).resolve()) if args.feedback_jsonl else []
    regression = load_regression(Path(args.regression_jsonl).resolve()) if args.regression_jsonl else []
    out_md = Path(args.output_md).resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)

    map_terms = [r for r in feedback if str(r.get("action", "")) == "map_term"]
    add_classes = [r for r in feedback if str(r.get("action", "")) == "add_class"]

    lines = [
        f"# Skills 更新建议：{args.topic}",
        "",
        "## 1. 术语归一技能（Term Normalization）",
        "",
    ]
    if map_terms:
        for r in map_terms[:50]:
            term = r.get("term", "")
            cls = r.get("class", "")
            lines.append(f"- 新增规则：`{term}` -> `{cls}`")
    else:
        lines.append("- 暂无 map_term 反馈，保持现有术语规则。")

    lines += [
        "",
        "## 2. 口径约束技能（Metric Guardrails）",
        "",
    ]
    must_tokens = set()
    for r in regression:
        checks = r.get("checks", {}) if isinstance(r, dict) else {}
        for t in checks.get("must_contain", []) if isinstance(checks, dict) else []:
            must_tokens.add(str(t))
    if must_tokens:
        for t in sorted(must_tokens):
            lines.append(f"- 约束词：生成 SQL 时优先包含 `{t}`")
    else:
        lines.append("- 暂无回归 must_contain 规则。")

    lines += [
        "",
        "## 3. 反馈闭环执行策略",
        "",
        "- 新 feedback 进入后，先更新 OWL，再更新术语与约束技能。",
        "- 每次发布前必须通过回归样例库。",
        "- 对 high severity case 失败设置阻断。",
        "",
    ]

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print("[done] skills update suggestions")
    print(f"- skills_update_md: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
