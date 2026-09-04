"""统一解析入口 ``parse_document()`` + 类型路由 + 缓存 + 降级。

这是 AOF 摄取链路解析层的唯一入口（设计稿 §1 目标、§5）。所有入口（cognee_add_runner、
4 条业务入口）在 ``cognee.add`` 前调用它，把复杂格式归一化为 ``ParsedDoc``。

路由语义（``ParsedDoc.engine``）:
- ``docling``: 解析成功，``content`` 是干净 Markdown，可直接喂 ``cognee.add(data=content)``。
- ``direct``: md/txt 直读，``content`` 即原文。
- ``fallback``: 解析层未启用 / 类型不支持 / 引擎失败。此时 ``content`` 为空占位，
  调用方应改用 **原文件路径** 直接 ``cognee.add(path)``（保证链路永不中断）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from bridge.document_parser.cache import ParseCache, blake2b_file
from bridge.document_parser.config import ParserConfig
from bridge.document_parser.engine_base import ParsedDoc
from bridge.document_parser.engines import docling_engine, mineru_engine
from bridge.document_parser.normalizer import normalize

logger = logging.getLogger(__name__)

# 引擎模块注册：route 返回值 → 引擎模块
_ENGINE_MODULES = {
    "docling": docling_engine,
    "mineru": mineru_engine,
}


@dataclass
class ParseResult:
    """parse_document 的返回：解析产物 + 是否需要 fallback。"""

    doc: ParsedDoc
    # True 表示应把原文件交给 cognee.add（解析层未产出可用内容）
    use_raw_path: bool = False
    cached: bool = False


def parse_document(
    path: str | Path,
    *,
    config: ParserConfig | None = None,
    cache: ParseCache | None = None,
) -> ParseResult:
    """解析单个文件到统一 ParsedDoc。

    Args:
        path: 文件绝对路径。
        config: 解析配置；None 则用环境变量构造。实例化一次可复用（内部会创建默认 cache）。
        cache: 缓存实例；None 且 config.cache_enabled 时按默认目录创建。

    Returns:
        ParseResult：字段 ``use_raw_path`` 指示是否应对原路径直接 cognee.add。
    """
    cfg = config or ParserConfig.from_env()
    p = Path(path)

    # --- 整体开关 ---
    if not cfg.enabled:
        return ParseResult(_fallback(p, reason="parser disabled"), use_raw_path=True)

    # --- 未找到 / 目录 ---
    if not p.exists() or not p.is_file():
        return ParseResult(_fallback(p, reason="not a file"), use_raw_path=True)

    route = cfg.route(p)

    # --- md / txt 直读 ---
    if route == "direct":
        return ParseResult(_direct(p, cfg), use_raw_path=False)

    # --- 不支持类型（图片/结构化数据…）：交 cognee 内置 ---
    if route == "unsupported":
        return ParseResult(
            _fallback(p, reason=f"unsupported type {p.suffix}"), use_raw_path=True
        )

    # --- 主引擎解析（docling / mineru，含缓存）---
    if route in _ENGINE_MODULES:
        return _parse_with_engine(route, p, cfg, cache)

    # --- 其他扩展名兜底 ---
    return ParseResult(
        _fallback(p, reason=f"no parser for {p.suffix}"), use_raw_path=True
    )


def _parse_with_engine(
    engine: str, path: Path, cfg: ParserConfig, cache: ParseCache | None
) -> ParseResult:
    mod = _ENGINE_MODULES.get(engine)
    if mod is None:  # pragma: no cover
        return ParseResult(_fallback(path, reason=f"unknown engine {engine}"), use_raw_path=True)

    # 1) 内容指纹（缓存键用）
    try:
        fingerprint = blake2b_file(path)
    except OSError as e:
        logger.warning("document_parser: 无法读取文件指纹 %s: %s", path, e)
        return ParseResult(_fallback(path, reason="unreadable"), use_raw_path=True)

    # 2) 缓存命中
    if cfg.cache_enabled:
        c = cache or ParseCache(cfg.cache_dir)
        key = c.build_key(
            file_path=path, fingerprint=fingerprint, engine=engine, lang=cfg.lang
        )
        hit = c.get(key)
        if hit is not None:
            logger.debug("document_parser: cache hit (%s) %s", engine, path.name)
            return ParseResult(
                _from_dict(hit, path, engine=engine), use_raw_path=False, cached=True
            )

    # 3) 真正解析
    try:
        parsed = mod.parse(path, lang=cfg.lang, timeout=cfg.timeout)
    except Exception as e:  # noqa: BLE001 - 引擎失败须降级，不抛出
        logger.warning(
            "document_parser: %s 失败 %s (%s); fallback 到原路径", engine, path.name, e
        )
        return ParseResult(_fallback(path, reason=str(e)), use_raw_path=True)

    if not parsed.content and parsed.engine == "fallback":
        return ParseResult(parsed, use_raw_path=True)

    norm = normalize(parsed)
    norm.engine = engine

    # 4) 写缓存
    if cfg.cache_enabled:
        try:
            c.set(key, _to_dict(norm))
        except Exception as e:  # noqa: BLE001
            logger.debug("document_parser: 缓存写入失败: %s", e)

    return ParseResult(norm, use_raw_path=False)


def _direct(path: Path, cfg: ParserConfig) -> ParsedDoc:
    from bridge.document_parser.normalizer import direct_content

    content = direct_content(path)
    return normalize(
        ParsedDoc(
            content=content,
            source_path=str(path),
            mime_type="text/markdown"
            if path.suffix.lower() in {".md", ".markdown"}
            else None,
            engine="direct",
        )
    )


def _fallback(path: Path, *, reason: str = "") -> ParsedDoc:
    return ParsedDoc(
        content="",
        source_path=str(path),
        engine="fallback",
        parsed_at=None,
        structured={"fallback_reason": reason},
    )


def _to_dict(doc: ParsedDoc) -> dict:
    return doc.to_dict()


def _from_dict(data: dict, path: Path, engine: str) -> ParsedDoc:
    from bridge.document_parser.engine_base import ParsedDoc as PD

    return PD(
        content=data.get("content", ""),
        source_path=data.get("source_path", str(path)),
        mime_type=data.get("mime_type"),
        pages=data.get("pages"),
        tables_count=data.get("tables_count"),
        languages=data.get("languages") or [],
        engine=data.get("engine") or engine,
        content_hash=data.get("content_hash"),
        parsed_at=data.get("parsed_at"),
        structured=data.get("structured") or {},
    )
