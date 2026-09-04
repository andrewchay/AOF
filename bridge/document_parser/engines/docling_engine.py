"""Docling 引擎（首选主引擎）。

通过隔离解释器（subprocess_runner）执行 Docling 解析，避免污染 AOF 主 venv。
Docling 在阶段 0 PoC 中实测对中英文 PDF/Office/扫描件均稳定优，是中英文企业文档的默认选择。
"""

from __future__ import annotations

from pathlib import Path

from bridge.document_parser.engine_base import ParsedDoc
from bridge.document_parser.subprocess_runner import run_engine_worker


def available() -> bool:
    """探测 Docling 隔离解释器是否就绪。"""
    from bridge.document_parser.subprocess_runner import _resolve_interpreter

    return _resolve_interpreter("docling") is not None


def parse(path: Path, *, lang: str = "zh", timeout: int = 300) -> ParsedDoc:
    """用隔离 Docling 解释器把 ``path`` 解析为统一 ParsedDoc。"""
    return run_engine_worker("docling", path, lang=lang, timeout=timeout)


class DoclingEngine:
    """可配置的 Docling 引擎对象（供路由层复用同一实例与参数）。"""

    name = "docling"

    def __init__(self, *, lang: str = "zh", timeout: int = 300):
        self.lang = lang
        self.timeout = timeout

    def available(self) -> bool:
        return available()

    def parse(self, path: Path) -> ParsedDoc:
        return parse(path, lang=self.lang, timeout=self.timeout)
