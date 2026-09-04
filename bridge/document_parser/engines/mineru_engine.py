"""MinerU 引擎（高精度增强开关）。

设计稿中 MinerU 为「复杂表格/版式/图片保留」的高配选项（阶段 0 PoC 结论）。
与 Docling 一样走隔离 subprocess（worker_entry 内通过 CLI 调 mineru，pipeline backend），
主 venv 零新增依赖。默认不启用（docling 为主）；通过配置/环境变量选择。

MinerU 依赖隔离：``AOF_MINERU_PYTHON`` 指向装了 mineru(pipeline) 的 Python，否则
用默认候选（/tmp/aof_mineru_venv/bin/python 或 ~/.aof/mineru-venv）。
"""

from __future__ import annotations

from pathlib import Path

from bridge.document_parser.engine_base import ParsedDoc
from bridge.document_parser.subprocess_runner import run_engine_worker


def available() -> bool:
    """探测 MinerU 隔离解释器是否就绪。"""
    from bridge.document_parser.subprocess_runner import _resolve_interpreter

    return _resolve_interpreter("mineru") is not None


def parse(path: Path, *, lang: str = "zh", timeout: int = 600) -> ParsedDoc:
    """用隔离 MinerU (pipeline) 把 ``path`` 解析为统一 ParsedDoc。

    MinerU 较 Docling 更慢（OCR/版面重建），默认给更长超时。
    """
    return run_engine_worker("mineru", path, lang=lang, timeout=timeout)


class MinerUEngine:
    """可配置的 MinerU 高精度引擎对象。"""

    name = "mineru"

    def __init__(self, *, lang: str = "zh", timeout: int = 600):
        self.lang = lang
        self.timeout = timeout

    def available(self) -> bool:
        return available()

    def parse(self, path: Path) -> ParsedDoc:
        return parse(path, lang=self.lang, timeout=self.timeout)
