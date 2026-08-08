"""AOF 企业文档解析层（document_parser）。

阶段 0 PoC 定稿：以 Docling 为首选主引擎（隔离 subprocess 调用），MinerU 为高精度开关，
Unstructured 为兜底；统一输出 ParsedDoc（干净 Markdown + 结构化元数据），供下游
add/cognify/RAG 消费。接入方式为「公共 parse_document + 逐入口接入」（设计稿 §5.1）。
"""

from __future__ import annotations

from bridge.document_parser.cache import ParseCache, blake2b_file
from bridge.document_parser.config import ParserConfig
from bridge.document_parser.core import ParseResult, parse_document
from bridge.document_parser.engine_base import ParsedDoc

__all__ = [
    "ParseCache",
    "ParseResult",
    "ParserConfig",
    "ParsedDoc",
    "blake2b_file",
    "parse_document",
]
