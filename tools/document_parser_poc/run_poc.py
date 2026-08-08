#!/usr/bin/env python3
"""阶段 0 · 文档解析引擎 PoC 运行器。

对比 MinerU / Docling / Unstructured 三个引擎在真实企业文档上的解析质量。
本机（Apple Silicon）无 NVIDIA GPU，仅 CPU 推理，只验证解析质量。

用法：
    在有文档的 data/poc/samples/ 下：
    python run_poc.py            # 全引擎对比
    python run_poc.py --engines docling,mineru   # 指定引擎
    python run_poc.py --list     # 只看样例清单
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "poc" / "samples"
RUNS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "poc" / "runs"

# 本机可 CPU 解析的 PDF/Office 类型，统一走引擎
PARSABLE_EXTS = {".pdf", ".docx", ".pptx", ".xlsx"}
PLAIN_EXTS = {".md", ".txt", ".markdown"}


@dataclass
class ParsedResult:
    engine: str
    source: str
    ok: bool
    error: str | None = None
    duration_s: float = 0.0
    content: str | None = None
    tables_count: int | None = None
    languages: list[str] = field(default_factory=list)
    sample_paragraphs: list[str] = field(default_factory=list)


def _import(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _import2(python: str | None, module: str) -> bool:
    """在指定 python 解释器上探测模块可用性（用于隔离 venv 的引擎）。"""
    if not python:
        return False
    code = f"import importlib.util; print(importlib.util.find_spec('{module}') is not None)"
    import subprocess

    try:
        r = subprocess.run(
            [python, "-c", code], capture_output=True, text=True, timeout=30
        )
        return r.stdout.strip() == "True"
    except Exception:
        return False


def _mineru_bin() -> str | None:
    import os
    import shutil

    # 1) 环境变量显式指定
    env = os.environ.get("MINERU_BIN")
    if env:
        return env
    # 2) 常见隔离 venv 路径
    for cand in [
        "/tmp/aof_mineru_venv/bin/mineru",
        str(Path.home() / ".mineru" / "bin" / "mineru"),
    ]:
        if Path(cand).exists():
            return cand
    # 3) PATH 中的 mineru
    return shutil.which("mineru")


def _engines_available() -> dict[str, bool]:
    return {
        "mineru": _mineru_bin() is not None,
        "docling": _import("docling"),
        "unstructured": _import("unstructured"),
    }


def collect_samples() -> list[Path]:
    if not SAMPLES_DIR.exists():
        return []
    files = [p for p in SAMPLES_DIR.rglob("*") if p.is_file()]
    parsable = [p for p in files if p.suffix.lower() in PARSABLE_EXTS]
    plain = [p for p in files if p.suffix.lower() in PLAIN_EXTS]
    return sorted(parsable) + sorted(plain)


def run_docling(path: Path) -> ParsedResult:
    from docling.document_converter import DocumentConverter

    t0 = time.time()
    try:
        result = DocumentConverter().convert(path)
        md = result.document.export_to_markdown()
        tables = result.document.tables or []
        return ParsedResult(
            engine="docling",
            source=str(path),
            ok=True,
            duration_s=round(time.time() - t0, 2),
            content=md,
            tables_count=len(tables),
            sample_paragraphs=_sample(md, 5),
        )
    except Exception as e:  # pragma: no cover
        return ParsedResult(
            engine="docling",
            source=str(path),
            ok=False,
            error=f"{e.__class__.__name__}: {e}",
            duration_s=round(time.time() - t0, 2),
        )


def run_unstructured(path: Path) -> ParsedResult:
    from unstructured.partition.auto import partition

    t0 = time.time()
    try:
        elements = partition(filename=str(path))
        md = _elements_to_markdown(elements)
        return ParsedResult(
            engine="unstructured",
            source=str(path),
            ok=True,
            duration_s=round(time.time() - t0, 2),
            content=md,
            tables_count=sum(1 for e in elements if "Table" in str(type(e).__name__)),
            sample_paragraphs=_sample(md, 5),
        )
    except Exception as e:  # pragma: no cover
        return ParsedResult(
            engine="unstructured",
            source=str(path),
            ok=False,
            error=f"{e.__class__.__name__}: {e}",
            duration_s=round(time.time() - t0, 2),
        )


def run_mineru(path: Path) -> ParsedResult:
    import shutil
    import subprocess
    import tempfile

    mineru_bin = _mineru_bin()
    t0 = time.time()
    if not mineru_bin:
        return ParsedResult(
            engine="mineru",
            source=str(path),
            ok=False,
            error="mineru CLI not found",
            duration_s=round(time.time() - t0, 2),
        )
    work = Path(tempfile.mkdtemp(prefix="mineru_poc_"))
    try:
        cmd = [mineru_bin, "-p", str(path), "-o", str(work), "-b", "pipeline"]
        env = {"PATH": os.environ.get("PATH", "")}
        if str(mineru_bin).startswith("/tmp/aof_mineru_venv"):
            env["HOME"] = os.environ.get("HOME", "")
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, **env},
        )
        md_path = _find_mineru_markdown(work)
        if r.returncode != 0 or not md_path:
            err = (r.stderr or r.stdout or "")[-400:]
            return ParsedResult(
                engine="mineru",
                source=str(path),
                ok=False,
                error=f"exit={r.returncode} {err}",
                duration_s=round(time.time() - t0, 2),
            )
        content = md_path.read_text(encoding="utf-8")
        return ParsedResult(
            engine="mineru",
            source=str(path),
            ok=True,
            duration_s=round(time.time() - t0, 2),
            content=content,
            tables_count=content.count("<table>") + content.count("<table |"),
            sample_paragraphs=_sample(content, 5),
        )
    except Exception as e:  # pragma: no cover
        return ParsedResult(
            engine="mineru",
            source=str(path),
            ok=False,
            error=f"{e.__class__.__name__}: {e}",
            duration_s=round(time.time() - t0, 2),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _find_mineru_markdown(work_dir: Path) -> Path | None:
    for p in sorted(work_dir.rglob("*.md")):
        return p
    return None


def run_plain(path: Path) -> ParsedResult:
    """md/txt 直读，作为 baseline。"""
    t0 = time.time()
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return ParsedResult(
            engine="direct",
            source=str(path),
            ok=True,
            duration_s=round(time.time() - t0, 2),
            content=content,
            sample_paragraphs=_sample(content, 5),
        )
    except Exception as e:  # pragma: no cover
        return ParsedResult(
            engine="direct",
            source=str(path),
            ok=False,
            error=f"{e.__class__.__name__}: {e}",
            duration_s=round(time.time() - t0, 2),
        )


def _sample(text: str, n: int) -> list[str]:
    if not text:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    # 均匀采样：头、中、尾
    if len(lines) <= n:
        return lines
    idx = sorted(
        {
            0,
            len(lines) // 2,
            len(lines) - 1,
            *(range(0, len(lines), max(1, len(lines) // n)))[: n - 2],
        }
    )
    return [lines[i] for i in idx if i < len(lines)][:n]


def _elements_to_markdown(elements) -> str:
    out = []
    for el in elements:
        text = getattr(el, "text", None) or ""
        tname = type(el).__name__.lower()
        if "table" in tname:
            out.append(f"```table\n{text}\n```")
        elif "title" in tname or "header" in tname:
            out.append(f"## {text}") if text else None
        else:
            if text.strip():
                out.append(text)
    return "\n\n".join(out)


ENGINES = {
    "docling": run_docling,
    "unstructured": run_unstructured,
    "mineru": run_mineru,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="docling,mineru,unstructured")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--samples-dir", default=str(SAMPLES_DIR))
    args = ap.parse_args()

    samples = collect_samples()
    if args.list:
        print(f"样例清单（{len(samples)} 份）:")
        for p in samples:
            print(f"  {p.relative_to(SAMPLES_DIR)}")
        return 0

    if not samples:
        print(f"[warn] 样例目录为空：{SAMPLES_DIR}")
        print("       请将真实企业文档放入 data/poc/samples/ 后重跑。")
        return 0

    avail = _engines_available()
    print("引擎可用性:", {k: "OK" if v else "未安装" for k, v in avail.items()})

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, list] = {"meta": {"samples_dir": str(SAMPLES_DIR), "engines": {}}}
    report["meta"]["engines"] = {k: bool(v) for k, v in avail.items()}

    total = 0
    for path in samples:
        ext = path.suffix.lower()
        # plain 文件直读为 baseline，不跑引擎
        if ext in PLAIN_EXTS:
            res = run_plain(path)
            report.setdefault("results", []).append(asdict(res))
            _write_result(res)
            total += 1
            _print_row(res)
            continue
        for ename in args.engines.split(","):
            ename = ename.strip()
            if ename not in ENGINES:
                continue
            if not avail[ename]:
                res = ParsedResult(
                    engine=ename,
                    source=str(path),
                    ok=False,
                    error="engine not installed",
                )
                report.setdefault("results", []).append(asdict(res))
                _write_result(res)
                total += 1
                _print_row(res)
                continue
            res = ENGINES[ename](path)
            report.setdefault("results", []).append(asdict(res))
            _write_result(res)
            total += 1
            _print_row(res)

    out = RUNS_DIR / "poc_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成 {total} 条解析结果 → {out}")
    return 0


def _result_file(res: ParsedResult, ext: str = ".md") -> Path:
    src = Path(res.source)
    stem = f"{src.stem}__{res.engine}"
    return RUNS_DIR / f"{stem}{ext}"


def _write_result(res: ParsedResult) -> None:
    meta = {k: v for k, v in asdict(res).items() if k != "content"}
    meta_file = _result_file(res, ".json")
    meta_file.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if res.content:
        (_result_file(res)).write_text(res.content, encoding="utf-8")


def _print_row(res: ParsedResult) -> None:
    status = "OK " if res.ok else "ERR"
    print(
        f"  [{status}] {res.engine:12s} {Path(res.source).name:40s} "
        f"{res.duration_s:6.1f}s"
        f"{'  tables=' + str(res.tables_count) if res.tables_count is not None else ''}"
        f"{'  ' + res.error[:80] if not res.ok else ''}"
    )


if __name__ == "__main__":
    sys.exit(main())
