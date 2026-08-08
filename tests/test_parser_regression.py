"""parser_regression 回归评估逻辑测试。

验证 evaluate() 的断言规则（内容非空 / 表格数 / 无 fallback / 含表占比）。
用注入的 mock parse_fn，不触发真实 docling，保证快速、环境无关。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 加入 AOF 项目根与 tools/parser_regression 到 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in [str(_PROJECT_ROOT), str(_PROJECT_ROOT / "tools" / "parser_regression")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from bridge.document_parser.core import ParseResult  # noqa: E402
from bridge.document_parser.engine_base import ParsedDoc  # noqa: E402

# noinspection PyUnresolvedReferences
from run_eval import evaluate  # noqa: E402

pytestmark = [pytest.mark.unit]


def _res(file: str, *, engine="docling", tables=1, content="正文内容足够长", use_raw=False):
    return ParseResult(
        ParsedDoc(content=content, source_path=file, engine=engine, tables_count=tables),
        use_raw_path=use_raw,
    )


def _make_files(tmp_path: Path, names: list[str]) -> list[Path]:
    return [tmp_path / n for n in names]


class TestEvaluate:
    def test_pass_all_rules(self, tmp_path):
        rules = {
            "content_nonempty": True, "min_content_chars": 10,
            "min_tables": {"a.pdf": 1}, "docs_with_tables_ratio_min": 0.75,
            "fallback_allowed": False,
        }
        files = [tmp_path / "a.pdf", tmp_path / "b.docx"]

        def parse_fn(f):
            return _res(f.name, tables=2, content="x" * 50)

        results, fails = evaluate(files, rules, parse_fn)
        assert fails == []

    def test_fail_fallback_not_allowed(self, tmp_path):
        rules = {"content_nonempty": True, "fallback_allowed": False}
        files = [tmp_path / "a.pdf"]

        def parse_fn(f):
            return _res(f.name, engine="fallback", content="", use_raw=True)

        _, fails = evaluate(files, rules, parse_fn)
        assert any("不应 fallback" in x for x in fails)

    def test_fail_short_content(self, tmp_path):
        rules = {"content_nonempty": True, "min_content_chars": 100}
        files = [tmp_path / "a.pdf"]

        def parse_fn(f):
            return _res(f.name, content="短")

        _, fails = evaluate(files, rules, parse_fn)
        assert any("内容过短" in x for x in fails)

    def test_fail_min_tables(self, tmp_path):
        rules = {"min_tables": {"a.pdf": 3}, "fallback_allowed": False}
        files = [tmp_path / "a.pdf"]

        def parse_fn(f):
            return _res(f.name, tables=1)

        _, fails = evaluate(files, rules, parse_fn)
        assert any("表格数" in x and "<3" in x for x in fails)

    def test_fail_table_ratio(self, tmp_path):
        rules = {"docs_with_tables_ratio_min": 0.8, "fallback_allowed": False}
        files = [tmp_path / "a.pdf", tmp_path / "b.pdf"]

        def parse_fn(f):
            tables = 2 if f.name == "a.pdf" else 0
            return _res(f.name, tables=tables)

        _, fails = evaluate(files, rules, parse_fn)
        assert any("含表文档占比" in x for x in fails)

    def test_engine_error_recorded(self, tmp_path):
        rules = {"content_nonempty": True, "fallback_allowed": False}
        files = [tmp_path / "a.pdf"]

        def parse_fn(f):
            raise RuntimeError("boom")

        results, fails = evaluate(files, rules, parse_fn)
        assert results[0]["engine"] == "error"
        assert any("解析异常" in x for x in fails)
