"""document_parser 配置：引擎路由策略、开关、运行时参数。

读取优先级：显式参数 > 环境变量（``AOF_PARSER_*``）> 内置默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from bridge.document_parser.engine_base import PARSABLE_EXTS, PLAIN_EXTS


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


@dataclass
class ParserConfig:
    """解析层运行配置。

    ``engine`` 选定主解析引擎：
    - ``docling``（默认）：中英文/Office/扫描件稳定优，快。
    - ``mineru``（高配开关）：复杂表格/版式/图片保留更强，但慢、需隔离 mineru venv。
    """

    # 是否启用解析层（整体开关）。关闭时所有文档走 fallback 原样给 cognee.add。
    enabled: bool = True
    # 主引擎：docling / mineru
    engine: str = "docling"
    # 慢速引擎最长执行秒数
    timeout: int = 300
    # 默认语言（影响 OCR）
    lang: str = "zh"
    # 是否启用解析结果缓存
    cache_enabled: bool = True
    # 缓存目录（None=用默认）
    cache_dir: str | os.PathLike | None = None
    # 路由到主引擎的扩展名集合（PDF/Office）
    engine_exts: set[str] = field(default_factory=lambda: set(PARSABLE_EXTS))
    # 直读的扩展名集合（md/txt）
    direct_exts: set[str] = field(default_factory=lambda: set(PLAIN_EXTS))
    # 解析失败时的 fallback 行为：True=原样交给 cognee.add；False=报错
    fallback_on_error: bool = True

    @classmethod
    def from_env(cls) -> "ParserConfig":
        engine = os.environ.get("AOF_PARSER_ENGINE", "docling").strip().lower()
        if engine not in {"docling", "mineru"}:
            engine = "docling"
        # MinerU 更慢，提高默认超时
        base_timeout = _env_int("AOF_PARSER_TIMEOUT", 300)
        timeout = base_timeout
        if engine == "mineru" and "AOF_PARSER_TIMEOUT" not in os.environ:
            timeout = 900
        return cls(
            enabled=_env_bool("AOF_PARSER_ENABLED", True),
            engine=engine,
            timeout=timeout,
            lang=os.environ.get("AOF_PARSER_LANG", "zh"),
            cache_enabled=_env_bool("AOF_PARSER_CACHE", True),
            cache_dir=os.environ.get("AOF_PARSER_CACHE_DIR") or None,
            fallback_on_error=_env_bool("AOF_PARSER_FALLBACK", True),
        )

    def route(self, path: Path) -> str:
        """按扩展名返回路由目标：``docling``/``mineru``/``direct``/``unsupported``/``fallback``。"""
        ext = path.suffix.lower()
        if ext in self.dirparsing_exts():  # markdown 直读
            return "direct"
        if ext in self.engine_exts:
            return self.engine
        if ext in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".webp",
            ".csv",
            ".json",
            ".yaml",
            ".yml",
        }:
            # 图片/结构化数据：交 cognee 内置（fallback）
            return "unsupported"
        return "fallback"

    def dirparsing_exts(self) -> set[str]:
        return self.direct_exts
