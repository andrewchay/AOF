#!/usr/bin/env python3
"""document_parser 解析质量回归评估（阶段 3）。

对基线样例集跑 parse_document，断言质量规则（内容非空 / 表格数 / 引擎类型 / 无 fallback），
防止升级引擎/依赖后解析质量回退。退出码：0=通过，1=有任一失败，2=运行错误。

用法：
    python run_eval.py                    # 跑 samples_dir，对照 baseline.json
    python run_eval.py --baseline x.json  # 指定基线文件
    python run_eval.py --samples-dir ...  # 覆盖样例目录
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保能 import AOF 的 bridge（脚本位于 tools/parser_regression/）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def load_baseline(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def evaluate(sample_files: list[Path], rules: dict, parse_fn) -> tuple[list[dict], list[str]]:
    """对样例跑解析并断言规则。返回 (results, fail_reasons)。

    parse_fn(path) -> ParseResult（可注入真实或 mock）。
    """
    results = []
    for f in sample_files:
        try:
            r = parse_fn(f)
        except Exception as e:  # pragma: no cover
            results.append({"file": f.name, "engine": "error", "error": str(e)})
            continue
        doc = r.doc
        results.append(
            {
                "file": f.name,
                "engine": doc.engine,
                "use_raw_path": r.use_raw_path,
                "tables": doc.tables_count,
                "chars": len(doc.content or ""),
            }
        )

    fail_reasons = []
    if rules.get("content_nonempty", True):
        for rec in results:
            if rec["engine"] == "error":
                fail_reasons.append(f"{rec['file']}: 解析异常")
            elif rec["chars"] < rules.get("min_content_chars", 30):
                fail_reasons.append(
                    f"{rec['file']}: 内容过短 ({rec['chars']}<{rules.get('min_content_chars')})"
                )

    if not rules.get("fallback_allowed"):
        for rec in results:
            if rec.get("engine") == "fallback":
                fail_reasons.append(f"{rec['file']}: 不应 fallback")

    min_tables = rules.get("min_tables", {})
    for name, expected in min_tables.items():
        rec = next((x for x in results if x["file"] == name), None)
        if rec is None:
            fail_reasons.append(f"{name}: 基线未找到")
        elif (rec.get("tables") or 0) < expected:
            fail_reasons.append(f"{name}: 表格数 {rec.get('tables')}<{expected}")

    with_tables = [r for r in results if (r.get("tables") or 0) > 0]
    ratio = len(with_tables) / len(results) if results else 0
    if ratio < rules.get("docs_with_tables_ratio_min", 0.75):
        fail_reasons.append(
            f"含表文档占比 {ratio:.2f} < {rules.get('docs_with_tables_ratio_min')}"
        )
    return results, fail_reasons


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=str(Path(__file__).parent / "baseline.json"))
    ap.add_argument("--samples-dir", default=None)
    args = ap.parse_args()

    base = load_baseline(Path(args.baseline))
    base_dir = Path(args.baseline).resolve().parent
    samples_dir = base_dir / (args.samples_dir or base.get("samples_dir", "samples"))
    if not samples_dir.is_dir():
        print(f"[error] 样例目录不存在: {samples_dir}")
        return 2

    from bridge.document_parser import ParserConfig, parse_document

    cfg = ParserConfig(enabled=True, cache_enabled=False)
    rules = base.get("rules", {})

    files = sorted(p for p in samples_dir.rglob("*") if p.is_file())
    if not files:
        print("[error] 样例集为空")
        return 2

    # 核心评估（也供 pytest 复用）：跑解析 + 断言规则
    results, fail_reasons = evaluate(files, rules, lambda f: parse_document(f, config=cfg))

    ratio = sum(1 for r in results if (r.get("tables") or 0) > 0) / len(results) if results else 0

    # ---- 输出报告 ----
    print(f"回归集: {base.get('name')} | 文档 {len(results)} 份 | Docling")
    print(f"{'file':40s} {'engine':10s} {'tables':>7s} {'chars':>7s}")
    for rec in sorted(results, key=lambda x: x["file"]):
        print(f"{rec['file']:40s} {rec['engine']:10s} {str(rec.get('tables',0)):>7s} {str(rec.get('chars',0)):>7s}")
    print(f"\n含表文档占比: {ratio:.2f}")

    if fail_reasons:
        print(f"\n[FAIL] {len(fail_reasons)} 项未达标:")
        for r in fail_reasons:
            print(f"  - {r}")
        return 1
    print("\n[PASS] 全部基线规则通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
