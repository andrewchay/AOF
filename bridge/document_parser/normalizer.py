# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""归一化层：把各引擎输出变成统一输出模型。

设计稿 §5.2 定义统一输出模型：``format: markdown`` 的 content + 结构化 metadata。
不同引擎（docling / mineru / direct / fallback）可能输出轻微差异，这里统一：

1. 确保 content 有统一的顶层 Markdown 结构（去掉文档末尾多余空白、规范化换行）。
2. 计算/固化 metadata（mime, pages, tables_count, languages, engine, content_hash）。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from bridge.document_parser.cache import blake2b_file
from bridge.document_parser.engine_base import ParsedDoc


def normalize(doc: ParsedDoc, *, source: Path | None = None) -> ParsedDoc:
    """归一化 ParsedDoc 到统一模型（幂等）。"""
    content = (doc.content or "").strip() + (
        "\n" if doc.content and (doc.content or "").strip() else ""
    )
    src = Path(doc.source_path or (str(source) if source else ""))
    mime = doc.mime_type or _guess_mime(src)
    content_hash = (
        doc.content_hash or blake2b_file(src) if (src and src.exists()) else None
    )

    return ParsedDoc(
        content=content,
        source_path=doc.source_path,
        mime_type=mime,
        pages=doc.pages,
        tables_count=doc.tables_count,
        languages=doc.languages or [],
        engine=doc.engine,
        content_hash=content_hash,
        parsed_at=doc.parsed_at,
        structured=doc.structured or {},
    )


def _guess_mime(path: Path) -> str | None:
    mime, _ = mimetypes.guess_type(str(path))
    return mime


def direct_content(path: Path) -> str:
    """md/txt 直读文本（跳过解析引擎）。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except UnicodeDecodeError:
        return path.read_bytes().decode("latin-1", errors="replace")
