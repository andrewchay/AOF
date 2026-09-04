"""文档解析引擎抽象基类与统一输出模型。

阶段 0 PoC 结论：各引擎（Docling / MinerU / Unstructured）依赖不能共享 venv
（transformers 4.x vs 5.x 冲突），因此所有引擎均通过 **subprocess 调用隔离解释器**执行，
进程内只做编排与归一化。本文件定义：

- ``ParsedDoc``：统一输出模型（干净 Markdown + 结构化元数据），下游 add/cognify/RAG 只消费它。
- ``Engine``：引擎抽象基类，子类封装「如何在本机解析一份文件并产生 ParsedDoc」。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# 本机可以交给解析引擎处理的扩展名（PDF/Office）
PARSABLE_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".ppt", ".doc"}
# 本来就是干净文本、跳过解析层直接读取的扩展名
PLAIN_EXTS = {".md", ".markdown", ".txt", ".text", ".rst"}
# 不支持的文件类型（走 fallback：原样交给 cognee.add）
UNSUPPORTED_EXTS = {
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
}


@dataclass
class ParsedDoc:
    """统一解析输出模型。

    Attributes:
        content: 归一化后的干净 Markdown（可直接作为 ``cognee.add(data)`` 的 data）。
        source_path: 源文件绝对路径。
        mime_type: 由后缀推断的 MIME（best-effort）。
        pages: 页数（PDF/PPTX 等有页概念格式），未知为 None。
        tables_count: 识别出的表格数（best-effort）。
        languages: 检测语言（best-effort）。
        engine: 实际使用的引擎，取值 document 引擎名或 ``direct`` / ``fallback``。
        content_hash: Blake2b 内容指纹（用于缓存键）。
        parsed_at: ISO 时间戳。
        structured: 可选的表格结构化数据（阶段 2 预留，默认空）。
    """

    content: str
    source_path: str
    mime_type: str | None = None
    pages: int | None = None
    tables_count: int | None = None
    languages: list[str] = field(default_factory=list)
    engine: str = "unknown"
    content_hash: str | None = None
    parsed_at: str | None = None
    structured: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class Engine(Protocol):
    """引擎协议。实现 ``name`` 属性与 ``parse(path) -> ParsedDoc``。"""

    name: str

    def parse(self, path: Path) -> ParsedDoc: ...

    def available(self) -> bool: ...


class EngineUnavailableError(RuntimeError):
    """引擎不可用（依赖缺失 / 隔离解释器不存在）。"""
