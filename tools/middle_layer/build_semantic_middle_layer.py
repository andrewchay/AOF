#!/usr/bin/env python3
"""Build semantic middle layer artifacts from docs + metadata + feedback.

Upstream expected input:
- docs (required)
- metadata (optional but recommended)
- feedback (optional)

Outputs:
- ontology artifacts (owl/report/pattern/feedback candidates)
- mapping library
- regression library
- manifest json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


DEFAULT_AOF_ROOT = Path(__file__).resolve().parents[2]
AOF_ROOT = Path(os.environ.get("AOF_ROOT", str(DEFAULT_AOF_ROOT))).resolve()


def slugify(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "topic"


def run(cmd: list[str], env: dict[str, str] | None = None) -> str:
    p = subprocess.run(cmd, text=True, capture_output=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(
            f"command failed ({p.returncode}): {' '.join(cmd)}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}"
        )
    return p.stdout


def collect_sql_files(docs_path: Path) -> list[Path]:
    if docs_path.is_file() and docs_path.suffix.lower() == ".sql":
        return [docs_path]
    if docs_path.is_dir():
        return sorted([p for p in docs_path.rglob("*.sql") if p.is_file()])
    return []


def collect_text_files(docs_path: Path) -> list[Path]:
    suffixes = {".md", ".txt", ".sql", ".json", ".csv"}
    if docs_path.is_file():
        return [docs_path]
    return sorted([p for p in docs_path.rglob("*") if p.is_file() and p.suffix.lower() in suffixes])


def build_ontology_input_file(docs_path: Path, topic_slug: str) -> Path:
    sql_files = collect_sql_files(docs_path)
    if sql_files:
        out = Path(tempfile.gettempdir()) / f"aof_middle_layer_{topic_slug}.sql"
        parts = []
        for i, f in enumerate(sql_files, start=1):
            txt = f.read_text(encoding="utf-8", errors="ignore")
            parts.append(f"-- SOURCE_SQL_{i}: {f}\n{txt}\n")
        out.write_text("\n".join(parts), encoding="utf-8")
        return out

    txt_files = collect_text_files(docs_path)
    out = Path(tempfile.gettempdir()) / f"aof_middle_layer_{topic_slug}.txt"
    parts = []
    for i, f in enumerate(txt_files, start=1):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        parts.append(f"# SOURCE_TEXT_{i}: {f}\n{txt}\n")
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def parse_path_from_stdout(stdout: str, prefix: str) -> str:
    for line in stdout.splitlines():
        if line.strip().startswith(prefix):
            return line.split(prefix, 1)[1].strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build semantic middle layer from docs+metadata+feedback")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--docs-path", required=True)
    parser.add_argument("--metadata-json", default="")
    parser.add_argument("--feedback-jsonl", default="")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--skip-align", action="store_true")
    parser.add_argument("--output-root", default="")
    args = parser.parse_args()

    docs_path = Path(args.docs_path).resolve()
    if not docs_path.exists():
        raise SystemExit(f"docs path not found: {docs_path}")

    topic_slug = slugify(args.topic)
    now = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_root = Path(args.output_root).resolve() if args.output_root else AOF_ROOT / "data" / "middle_layer" / topic_slug
    mapping_dir = output_root / "mapping"
    regression_dir = output_root / "regression"
    artifacts_dir = output_root / "artifacts"
    output_root.mkdir(parents=True, exist_ok=True)
    mapping_dir.mkdir(parents=True, exist_ok=True)
    regression_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    py = AOF_ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path("python3")

    # 1) ontology + alignment loop
    ontology_input = build_ontology_input_file(docs_path, topic_slug)
    ontology_cmd = [
        str(py),
        str(AOF_ROOT / "tools" / "ontology_factory" / "build_testdata_ontology_factory.py"),
        "--input",
        str(ontology_input),
        "--topic",
        topic_slug,
        "--max-iterations",
        str(args.max_iterations),
    ]
    if os.environ.get("COGNEE_ROOT"):
        ontology_cmd += ["--cognee-root", os.environ["COGNEE_ROOT"]]
    if args.feedback_jsonl:
        ontology_cmd += ["--feedback-jsonl", str(Path(args.feedback_jsonl).resolve())]
    if args.skip_align:
        ontology_cmd += ["--skip-align"]

    ontology_stdout = run(ontology_cmd)
    report_json = parse_path_from_stdout(ontology_stdout, "- report_json:")
    report_md = parse_path_from_stdout(ontology_stdout, "- report_md:")
    ontology_file = parse_path_from_stdout(ontology_stdout, "- ontology:")
    feedback_candidates = parse_path_from_stdout(ontology_stdout, "- feedback_jsonl:")

    # 2) mapping library
    mapping_stdout = run(
        [
            str(py),
            str(AOF_ROOT / "tools" / "middle_layer" / "build_mapping_library.py"),
            "--topic",
            topic_slug,
            "--docs-path",
            str(docs_path),
            "--output-dir",
            str(mapping_dir),
            *( ["--metadata-json", str(Path(args.metadata_json).resolve())] if args.metadata_json else [] ),
            *( ["--feedback-jsonl", str(Path(args.feedback_jsonl).resolve())] if args.feedback_jsonl else [] ),
        ]
    )

    # 3) regression library
    regression_jsonl = regression_dir / f"regression_cases_{topic_slug}_{now}.jsonl"
    regression_md = regression_dir / f"regression_summary_{topic_slug}_{now}.md"
    regress_cmd = [
        str(py),
        str(AOF_ROOT / "tools" / "middle_layer" / "build_regression_library.py"),
        "--topic",
        topic_slug,
        "--docs-path",
        str(docs_path),
        "--output-jsonl",
        str(regression_jsonl),
        "--output-md",
        str(regression_md),
    ]
    if args.feedback_jsonl:
        regress_cmd += ["--feedback-jsonl", str(Path(args.feedback_jsonl).resolve())]
    regression_stdout = run(regress_cmd)

    # 4) skills update suggestions
    skills_md = artifacts_dir / f"skills_update_suggestions_{topic_slug}_{now}.md"
    skill_cmd = [
        str(py),
        str(AOF_ROOT / "tools" / "middle_layer" / "build_skill_updates.py"),
        "--topic",
        topic_slug,
        "--regression-jsonl",
        str(regression_jsonl),
        "--output-md",
        str(skills_md),
    ]
    if args.feedback_jsonl:
        skill_cmd += ["--feedback-jsonl", str(Path(args.feedback_jsonl).resolve())]
    skill_stdout = run(skill_cmd)

    # 5) manifest
    manifest = {
        "topic": topic_slug,
        "created_at": now,
        "inputs": {
            "docs_path": str(docs_path),
            "metadata_json": str(Path(args.metadata_json).resolve()) if args.metadata_json else "",
            "feedback_jsonl": str(Path(args.feedback_jsonl).resolve()) if args.feedback_jsonl else "",
        },
        "artifacts": {
            "ontology_file": ontology_file,
            "alignment_report_json": report_json,
            "alignment_report_md": report_md,
            "feedback_candidates_jsonl": feedback_candidates,
            "mapping_dir": str(mapping_dir),
            "regression_jsonl": str(regression_jsonl),
            "regression_summary_md": str(regression_md),
            "skills_update_md": str(skills_md),
        },
        "logs": {
            "ontology_stdout": ontology_stdout,
            "mapping_stdout": mapping_stdout,
            "regression_stdout": regression_stdout,
            "skills_stdout": skill_stdout,
        },
    }
    manifest_file = artifacts_dir / f"middle_layer_manifest_{topic_slug}_{now}.json"
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[done] semantic middle layer")
    print(f"- topic: {topic_slug}")
    print(f"- manifest: {manifest_file}")
    print(f"- ontology: {ontology_file}")
    print(f"- report_json: {report_json}")
    print(f"- feedback_candidates: {feedback_candidates}")
    print(f"- mapping_dir: {mapping_dir}")
    print(f"- regression_jsonl: {regression_jsonl}")
    print(f"- regression_summary: {regression_md}")
    print(f"- skills_update_md: {skills_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
