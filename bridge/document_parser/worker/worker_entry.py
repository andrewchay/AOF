#!/usr/bin/env python3
"""解析引擎隔离 subprocess 入口（在独立解释器执行）。

本文件**必须自包含**：只能依赖 Python 标准库 + 由隔离 venv 安装的文档解析库（docling 等），
**不得 import AOF / bridge 的任何模块**，否则隔离解释器会因缺 AOF 依赖而失败。

调用方式（由 subprocess_runner 发起）::

    <isolated-python> worker_entry.py --engine docling --path /abs/file.pdf [--lang zh] [--timeout 300]

输出：单行 JSON 到 stdout，结构见 ``build_result``。错误时 exit code != 0 且 stderr 带原因。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now().astimezone().isoformat()


def build_result(
    *,
    ok: bool,
    content: str = "",
    source_path: str = "",
    engine: str = "",
    error: str | None = None,
    pages: int | None = None,
    tables_count: int | None = None,
    languages: list[str] | None = None,
    tables: list[dict] | None = None,
    duration_s: float = 0.0,
) -> dict:
    return {
        "ok": ok,
        "content": content,
        "source_path": source_path,
        "engine": engine,
        "error": error,
        "pages": pages,
        "tables_count": tables_count,
        "languages": languages or [],
        "tables": tables or [],
        "duration_s": round(duration_s, 3),
        "parsed_at": _now_iso(),
    }


def _run_docling(path: Path, timeout: int, lang: str = "zh") -> dict:
    """Docling 2.x：DocumentConverter 输出 markdown + 表格结构。"""
    t0 = time.time()
    from docling.document_converter import DocumentConverter

    conv = DocumentConverter()
    result = conv.convert(path)  # timeout由外层 subprocess 控制
    doc = result.document

    markdown = doc.export_to_markdown()
    tables: list[dict] = []
    for t in doc.tables:
        try:
            md = t.export_to_markdown(doc=doc)
        except TypeError:  # 兼容旧签名
            md = t.export_to_markdown()
        try:
            html = t.export_to_html(doc=doc)
        except TypeError:
            html = t.export_to_html()
        tables.append({"markdown": md, "html": html})

    pages = len(doc.pages) if hasattr(doc, "pages") and doc.pages else None
    return build_result(
        ok=True,
        content=markdown,
        source_path=str(path),
        engine="docling",
        pages=pages,
        tables_count=len(tables),
        languages=["zh", "en"],  # docling 不直接给出语言标签，best-effort
        tables=tables,
        duration_s=time.time() - t0,
    )


def _run_direct(path: Path, timeout: int = 0, lang: str = "zh") -> dict:
    """md/txt 直读，不进引擎。"""
    t0 = time.time()
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except UnicodeDecodeError:
        content = Path(path).read_bytes().decode("latin-1", errors="replace")
    return build_result(
        ok=True,
        content=content,
        source_path=str(path),
        engine="direct",
        duration_s=time.time() - t0,
    )


def _run_mineru(path: Path, timeout: int, lang: str = "zh") -> dict:
    """MinerU 3.x：通过隔离 CLI（-p file -o out -b pipeline）解析为 markdown。

    MinerU 是 CLI/FastAPI 架构（非 Python API），此处用 subprocess 调同 venv 的 mineru 命令。
    输出落在 <out>/<name>/auto/*.md。
    """
    import shutil
    import subprocess
    import tempfile

    t0 = time.time()
    mineru_bin = _locate_mineru_bin()
    if not mineru_bin:
        return build_result(
            ok=False, source_path=str(path), engine="mineru",
            error="mineru CLI 不在隔离 venv 中（mineru_bin not found）",
        )

    work = Path(tempfile.mkdtemp(prefix="mineru_work_"))
    try:
        cmd = [
            mineru_bin,
            "-p", str(path),
            "-o", str(work),
            "-b", "pipeline",
            # MinerU 语言代码与 AOF 不同（zh → ch）
            "-l", _mineru_lang(lang),
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=os.environ.copy()
        )
        md_path = _find_mineru_markdown(work)
        if proc.returncode != 0 or md_path is None:
            return build_result(
                ok=False, source_path=str(path), engine="mineru",
                duration_s=round(time.time() - t0, 3),
                error=f"mineru exit={proc.returncode} {_tail(proc.stderr or proc.stdout, 300)}",
            )
        content = md_path.read_text(encoding="utf-8")
        return build_result(
            ok=True,
            content=content,
            source_path=str(path),
            engine="mineru",
            tables_count=content.count("<table"),
            duration_s=round(time.time() - t0, 3),
        )
    except subprocess.TimeoutExpired:
        return build_result(
            ok=False, source_path=str(path), engine="mineru",
            duration_s=round(time.time() - t0, 3), error=f"mineru timeout (>{timeout}s)",
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _locate_mineru_bin() -> str | None:
    # 1) 同 venv 的 mineru 可执行（用 sys.prefix 定位 venv 根，避免 symlink resolve 到 python 安装目录）
    cand = Path(sys.prefix) / "bin" / "mineru"
    if cand.exists():
        return str(cand)
    # 2) PATH 中的 mineru
    return shutil_which("mineru")


def _mineru_lang(lang: str) -> str:
    """把 AOF/通用语言代码映射到 MinerU CLI 支持的代码。"""
    mapping = {
        "zh": "ch",
        "zh-cn": "ch",
        "zh_cn": "ch",
        "en": "ch",  # MinerU 无纯英文；用 ch 兼顾混排
    }
    return mapping.get(lang.strip().lower(), "ch")


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def _find_mineru_markdown(work_dir: Path) -> Path | None:
    for p in sorted(work_dir.rglob("*.md")):
        return p
    return None


def _tail(text: str, n: int) -> str:
    return (text or "")[-n:]


ENGINES = {
    "docling": _run_docling,
    "mineru": _run_mineru,
    "direct": _run_direct,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, choices=list(ENGINES))
    ap.add_argument("--path", required=True)
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(
            json.dumps(
                build_result(
                    ok=False,
                    source_path=args.path,
                    engine=args.engine,
                    error=f"file not found: {path}",
                )
            ),
            flush=True,
        )
        return 5

    fn = ENGINES.get(args.engine)
    if fn is None:
        print(
            json.dumps(
                build_result(
                    ok=False,
                    source_path=args.path,
                    engine=args.engine,
                    error=f"unknown engine: {args.engine}",
                )
            ),
            flush=True,
        )
        return 6

    try:
        result = fn(path, args.timeout, args.lang)
    except Exception as e:  # noqa: BLE001 - worker 边界须捕获一切异常
        result = build_result(
            ok=False,
            source_path=args.path,
            engine=args.engine,
            error=f"{e.__class__.__name__}: {e}",
        )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
