# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""共用摄取辅助：把「文件路径或普通数据」统一为待 add 数据。

各摄取入口（cognee_add_runner / batch / incremental / s3 / url）在调用 ``cognee.add`` 前
复用本函数，把解析层决策集中在一处，避免各入口复制解析代码（设计稿 §5.1）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from bridge.document_parser import ParserConfig, parse_document

logger = logging.getLogger(__name__)


def maybe_parse_local_file(
    data: Any, config: ParserConfig | None = None
) -> tuple[Any, dict]:
    """若 data 是存在的本地文件路径，则走解析层归一化；否则原样返回。

    Returns:
        (待 cognee.add 的数据, 解析上下文 dict)。ctx 含 parser_engine 与 use_raw_path：
        - parser_engine="docling"/"direct"：data 已换成干净 markdown，可直接 add
        - parser_engine="fallback"：data 为原路径，add 路径即可（链路不中断）
        - parser_engine="none"：data 非本地文件，原样 add
    """
    if isinstance(data, str) and Path(data).is_file():
        try:
            result = parse_document(data, config=config)
            if result.use_raw_path:
                return data, {"parser_engine": "fallback", "use_raw_path": True}
            return result.doc.content, {
                "parser_engine": result.doc.engine,
                "use_raw_path": False,
            }
        except Exception:  # pragma: no cover - 解析层异常绝不阻断摄取
            logger.warning("document_parser: 解析异常，回退原路径 data=%s", data)
            return data, {"parser_engine": "fallback"}
    return data, {"parser_engine": "none"}


def log_parse_context(ctx: dict, *, caller: str, filename: str) -> None:
    """记录解析上下文到日志（可观测）。"""
    engine = ctx.get("parser_engine")
    if engine and engine != "none":
        logger.info(
            "document_parser[%s]: %s engine=%s use_raw_path=%s",
            caller,
            filename,
            engine,
            ctx.get("use_raw_path"),
        )
