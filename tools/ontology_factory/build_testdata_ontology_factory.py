#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Build TEST_DATA ontology from input SQL/docs and iteratively align via AOF pipeline."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
EX_NS = "http://example.org/ontology#"
OWL_NS = "http://www.w3.org/2002/07/owl#"

ET.register_namespace("ex", EX_NS)
ET.register_namespace("rdf", RDF_NS)
ET.register_namespace("rdfs", RDFS_NS)

RDF = "{" + RDF_NS + "}"
RDFS = "{" + RDFS_NS + "}"


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "topic"


def concept_name(term: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9_]+", "_", term.strip())
    t = re.sub(r"_+", "_", t).strip("_")
    if not t:
        return "Concept"
    if t.lower() == "platform":
        return "Platform"
    if t.lower() == "query":
        return "Query"
    if t.lower() == "process":
        return "Process"
    return "".join(x.capitalize() for x in t.split("_"))


def individual_name(term: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9_\.]+", "_", term.strip().lower())
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "item"


def run(cmd: list[str], env: dict[str, str] | None = None) -> str:
    p = subprocess.run(cmd, env=env, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(
            f"command failed ({p.returncode}): {' '.join(cmd)}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}"
        )
    return p.stdout


def apply_feedback_patches(project_root: Path, ontology_file: Path, feedback_jsonl: Path) -> None:
    patcher = project_root / "tools" / "ontology_factory" / "apply_feedback_patches.py"
    py = project_root / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path("python3")
    out = run(
        [
            str(py),
            str(patcher),
            "--ontology-file",
            str(ontology_file),
            "--feedback-jsonl",
            str(feedback_jsonl),
        ]
    )
    print(out.strip())


def export_feedback_candidates(project_root: Path, report_json: Path) -> tuple[Path, Path] | None:
    exporter = project_root / "tools" / "ontology_factory" / "export_feedback_candidates.py"
    if not exporter.exists():
        return None

    py = project_root / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path("python3")

    stem = report_json.stem.replace("alignment_report", "feedback_candidates")
    out_jsonl = report_json.with_name(f"{stem}.jsonl")
    out_md = report_json.with_name(f"{stem}.md")

    out = run(
        [
            str(py),
            str(exporter),
            "--report-json",
            str(report_json),
            "--output-jsonl",
            str(out_jsonl),
            "--output-md",
            str(out_md),
        ]
    )
    print(out.strip())
    return out_jsonl, out_md


def parse_sql_semantics(normalized_jsonl: Path) -> dict[str, Any]:
    queries: list[dict[str, Any]] = []
    tables: set[str] = set()
    metrics: set[str] = set()
    filter_fields: set[str] = set()

    from_join_pattern = re.compile(r"\b(?:FROM|JOIN)\s+([`\"\[]?[\w\.]+[`\"\]]?)", re.IGNORECASE)
    alias_pattern = re.compile(r"\bAS\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
    field_pattern = re.compile(
        r"([a-zA-Z_][\w\.]*)\s*(?:=|!=|<>|>=|<=|>|<|\bIN\b|\bNOT\s+IN\b|\bLIKE\b|\bBETWEEN\b|\bRLIKE\b|\bREGEXP_LIKE\b)",
        re.IGNORECASE,
    )

    with normalized_jsonl.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            raw = row.get("raw", {})
            source_kind = row.get("source_kind", "")
            
            # 处理混合文档
            if source_kind == "mixed":
                # 混合文档已在 normalized JSONL 中包含必要的 _kind 标记
                # 这里我们只提取 SQL 相关的代码块
                kind = raw.get("_kind", "")
                if kind in ("code", "code_block") and raw.get("_lang") == "sql":
                    query_sql = str(raw.get("_query_sql", ""))
                else:
                    continue  # 跳过非 SQL 代码块
            else:
                query_sql = str(raw.get("_query_sql", ""))
                if not query_sql and source_kind == "sql":
                    query_sql = str(row.get("text", ""))

            local_tables: set[str] = set()
            if raw.get("_table"):
                local_tables.add(str(raw["_table"]).strip("`\"[]"))

            for m in from_join_pattern.finditer(query_sql):
                local_tables.add(m.group(1).strip("`\"[]"))

            local_metrics: set[str] = set(alias_pattern.findall(query_sql))
            local_fields: set[str] = set()
            for m in field_pattern.finditer(query_sql):
                token = m.group(1)
                field = token.split(".")[-1]
                if field.upper() in {"SELECT", "WHERE", "FROM", "JOIN", "AND", "OR"}:
                    continue
                local_fields.add(field)

            if query_sql:
                qname = f"sql_query_{len(queries) + 1}"
                queries.append(
                    {
                        "name": qname,
                        "sql": query_sql,
                        "tables": sorted(local_tables),
                        "metrics": sorted(local_metrics),
                        "fields": sorted(local_fields),
                    }
                )

            tables.update(local_tables)
            metrics.update(local_metrics)
            filter_fields.update(local_fields)

    result: dict[str, Any] = {
        "queries": queries,
        "tables": sorted(tables),
        "metrics": sorted(metrics),
        "filter_fields": sorted(filter_fields),
    }
    
    # 添加混合文档语义（如果有）
    # Note: _parse_mixed_record 会填充这些集合（定义在下方）
    
    return result


def _parse_mixed_record(
    raw: dict[str, Any], 
    index: int,
    code_snippets: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    concepts: set[str],
    code_annotation_pairs: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    tables: set[str],
    metrics: set[str],
    filter_fields: set[str],
) -> None:
    """解析混合文档记录。"""
    rec_kind = raw.get("_kind", "unknown")
    
    if rec_kind == "code":
        # 代码块
        lang = raw.get("_lang", "unknown")
        code = raw.get("_query_sql", "")
        annotation = raw.get("_context_annotation", "")
        
        snippet = {
            "index": index,
            "lang": lang,
            "code": code[:200],  # 摘要
            "has_context": bool(annotation),
        }
        code_snippets.append(snippet)
        
        # 如果是 SQL，继续提取表和字段
        if lang == "sql" and code:
            from_join_pattern = re.compile(r"\b(?:FROM|JOIN)\s+([`\"\[]?[\w\.]+[`\"\[]?)", re.IGNORECASE)
            alias_pattern = re.compile(r"\bAS\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
            
            local_tables = set()
            for m in from_join_pattern.finditer(code):
                local_tables.add(m.group(1).strip("`\"[]"))
            
            tables.update(local_tables)
            metrics.update(alias_pattern.findall(code))
            
            if code:
                queries.append({
                    "name": f"sql_query_{len(queries) + 1}",
                    "sql": code,
                    "tables": sorted(local_tables),
                    "source": "mixed_document",
                })
        
        # 记录代码-注释对
        if annotation:
            code_annotation_pairs.append({
                "code_index": index,
                "code_lang": lang,
                "annotation_summary": annotation[:100],
            })
    
    elif rec_kind in ("annotation", "text"):
        # 文本/标注
        annot_type = raw.get("_annotation_type", "text")
        text = raw.get("_text", "")
        rec_concepts = raw.get("_concepts", [])
        
        annotations.append({
            "index": index,
            "type": annot_type,
            "text_summary": text[:200],
            "concepts": rec_concepts,
        })
        
        concepts.update(rec_concepts)
    
    elif rec_kind == "annotated_code":
        # 代码+标注对
        code = raw.get("_code", "")
        annotation = raw.get("_annotation", "")
        tags = raw.get("_tags", [])
        rec_concepts = raw.get("_concepts", [])
        
        code_snippets.append({
            "index": index,
            "type": "annotated",
            "code_summary": code[:200],
            "tags": tags,
        })
        
        annotations.append({
            "index": index,
            "type": "code_annotation",
            "annotation": annotation[:200],
        })
        
        concepts.update(rec_concepts)
        concepts.update(tags)
        
        code_annotation_pairs.append({
            "code_index": index,
            "annotation_index": index,
            "tags": tags,
        })


def make_description(about: str) -> ET.Element:
    return ET.Element(RDF + "Description", {RDF + "about": f"{EX_NS}{about}"})


def add_type(desc: ET.Element, type_uri: str) -> None:
    ET.SubElement(desc, RDF + "type", {RDF + "resource": type_uri})


def add_obj(desc: ET.Element, pred: str, obj_name: str) -> None:
    ET.SubElement(desc, "{" + EX_NS + "}" + pred, {RDF + "resource": f"{EX_NS}{obj_name}"})


def add_subclass(desc: ET.Element, parent_name: str) -> None:
    ET.SubElement(desc, RDFS + "subClassOf", {RDF + "resource": f"{EX_NS}{parent_name}"})


def ensure_named_individual(root: ET.Element, name: str, class_name: str, comment: str | None = None) -> None:
    if has_about(root, name):
        return
    d = make_description(name)
    add_type(d, f"{EX_NS}{class_name}")
    add_type(d, f"{OWL_NS}NamedIndividual")
    if comment:
        c = ET.SubElement(d, RDFS + "comment")
        c.text = comment
    root.append(d)


def has_about(root: ET.Element, name: str) -> bool:
    target = f"{EX_NS}{name}"
    for d in root.findall(RDF + "Description"):
        if d.attrib.get(RDF + "about") == target:
            return True
    return False


def build_initial_ontology(topic: str, semantics: dict[str, Any], out_path: Path) -> None:
    root = ET.Element(RDF + "RDF")
    root.append(ET.Comment(" TEST_DATA: Auto-generated minimal ontology for topic SQL/doc alignment "))

    # Classes
    for cls in [
        "DatabaseTable",
        "SQLQuery",
        "Metric",
        "FilterCondition",
        "CommunityPlatform",
        "Platform",
        "Query",
        "Process",
    ]:
        d = make_description(cls)
        add_type(d, f"{OWL_NS}Class")
        root.append(d)

    # subclass relations
    d_cp = make_description("CommunityPlatform")
    add_subclass(d_cp, "Platform")
    root.append(d_cp)
    d_sq = make_description("SQLQuery")
    add_subclass(d_sq, "Query")
    root.append(d_sq)

    # Object properties
    props = [
        ("usesTable", "SQLQuery", "DatabaseTable"),
        ("definesMetric", "SQLQuery", "Metric"),
        ("usesFilterCondition", "SQLQuery", "FilterCondition"),
    ]
    for name, domain, rng in props:
        d = make_description(name)
        add_type(d, f"{OWL_NS}ObjectProperty")
        ET.SubElement(d, RDFS + "domain", {RDF + "resource": f"{EX_NS}{domain}"})
        ET.SubElement(d, RDFS + "range", {RDF + "resource": f"{EX_NS}{rng}"})
        root.append(d)

    # topic/platform individuals
    platform_name = individual_name(topic)
    ensure_named_individual(root, platform_name, "CommunityPlatform")

    # table individuals
    for t in semantics["tables"]:
        ensure_named_individual(root, t, "DatabaseTable")

    # metric individuals
    for m in semantics["metrics"]:
        ensure_named_individual(root, m, "Metric")

    # filter field individuals
    for f in semantics["filter_fields"]:
        ensure_named_individual(root, f, "FilterCondition")

    # query individuals + relations
    for q in semantics["queries"]:
        qd = make_description(q["name"])
        add_type(qd, f"{EX_NS}SQLQuery")
        add_type(qd, f"{OWL_NS}NamedIndividual")
        for t in q["tables"]:
            add_obj(qd, "usesTable", t)
        for m in q["metrics"]:
            add_obj(qd, "definesMetric", m)
        for f in q["fields"]:
            add_obj(qd, "usesFilterCondition", f)
        root.append(qd)

    ET.indent(root, space="  ")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="UTF-8", xml_declaration=True)


def parse_alignment_from_log(log_path: Path) -> dict[str, Any]:
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    unmatched = re.findall(r"No close match found for '(.+?)' in category '(.+?)'", text)
    matched = re.findall(r"match was found for found for '(.+?)' node", text)
    return {
        "log_file": str(log_path),
        "unmatched_count": len(unmatched),
        "matched_count": len(matched),
        "unmatched": [{"term": t, "category": c} for t, c in unmatched],
    }


def newest_cognee_log(cognee_root: Path, started_at: float) -> Path:
    logs_dir = cognee_root / "logs"
    cands = [p for p in logs_dir.glob("*.log") if p.stat().st_mtime >= started_at - 1]
    if not cands:
        cands = list(logs_dir.glob("*.log"))
    if not cands:
        raise RuntimeError(f"No cognee logs found under {logs_dir}")
    return max(cands, key=lambda p: p.stat().st_mtime)


def infer_individual_type(term: str) -> str:
    t = term.lower()
    if "." in term and "_" in term:
        return "DatabaseTable"
    if any(x in t for x in ["date", "id", "tag", "type", "region", "create_"]):
        return "FilterCondition"
    if any(x in t for x in ["cnt", "dau", "mau", "metric"]):
        return "Metric"
    if "query" in t:
        return "SQLQuery"
    if "community" in t or "platform" in t:
        return "CommunityPlatform"
    return "Process"


def augment_ontology(ontology_path: Path, unmatched: list[dict[str, str]]) -> list[str]:
    tree = ET.parse(ontology_path)
    root = tree.getroot()
    added: list[str] = []

    for item in unmatched:
        term = item["term"]
        cat = item["category"]
        if cat == "classes":
            cname = concept_name(term)
            if has_about(root, cname):
                continue
            d = make_description(cname)
            add_type(d, f"{OWL_NS}Class")
            c = ET.SubElement(d, RDFS + "comment")
            c.text = f"TEST_DATA auto-added from unmatched class term: {term}"
            root.append(d)
            added.append(f"class:{cname}")
        else:
            iname = individual_name(term)
            if has_about(root, iname):
                continue
            cls = infer_individual_type(term)
            ensure_named_individual(
                root,
                iname,
                cls,
                comment=f"TEST_DATA auto-added from unmatched individual term: {term}",
            )
            added.append(f"individual:{iname}:{cls}")

    ET.indent(root, space="  ")
    tree.write(ontology_path, encoding="UTF-8", xml_declaration=True)
    return added


def write_spec(
    spec_path: Path,
    project_root: Path,
    dataset: str,
    ontology_file: Path,
    matching_cutoff: float,
    cognee_root: Path | None,
) -> None:
    spec = {
        "project_root": str(project_root),
        "dataset": dataset,
        "runtime": {
            "run_in_background": False,
            "incremental_loading": True,
            "data_per_batch": 20,
            "retries": 0,
            "backoff_seconds": 1.0,
        },
        "ontology": {
            "file": str(ontology_file),
            "matching_cutoff": matching_cutoff,
        },
        "cognee": {
            "root": str(cognee_root) if cognee_root else "",
        },
    }
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="TEST_DATA Ontology Factory (auto build + iterative alignment)")
    parser.add_argument("--input", required=True, help="Input SQL/CSV/JSON/Doc file")
    parser.add_argument("--topic", default="", help="Topic name; default uses input stem")
    default_project_root = str(Path(os.environ.get("AOF_ROOT", str(Path(__file__).resolve().parents[2]))).resolve())
    parser.add_argument("--project-root", default=default_project_root)
    parser.add_argument("--cognee-root", default=os.environ.get("COGNEE_ROOT", ""))
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--matching-cutoff", type=float, default=0.8)
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--skip-align", action="store_true", help="Only build ontology, skip iterative alignment")
    parser.add_argument("--llm-api-key", default="", help="Optional; falls back to env LLM_API_KEY")
    parser.add_argument(
        "--feedback-jsonl",
        default="",
        help="Optional user feedback patches in JSONL (action-based), applied before each alignment iteration",
    )

    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise SystemExit(f"input not found: {input_path}")

    topic = args.topic.strip() or input_path.stem
    topic_slug = slugify(topic)
    now = datetime.now().strftime("%Y%m%d_%H%M%S")

    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    report_dir = logs_dir / "ontology_factory"
    report_dir.mkdir(parents=True, exist_ok=True)

    normalized_txt = logs_dir / f"TEST_DATA_normalized_{topic_slug}_{now}.txt"
    normalized_jsonl = logs_dir / f"TEST_DATA_normalized_{topic_slug}_{now}.jsonl"
    ontology_file = project_root / "ontologies" / f"TEST_DATA_{topic_slug}_ontology.owl"

    normalize_script = project_root / "tools" / "data_adapter" / "normalize_for_aof.py"
    py = project_root / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path("python3")

    normalize_cmd = [
        str(py),
        str(normalize_script),
        "--input",
        str(input_path),
        "--output-txt",
        str(normalized_txt),
        "--output-jsonl",
        str(normalized_jsonl),
    ]
    if args.max_records > 0:
        normalize_cmd += ["--max-records", str(args.max_records)]

    print("[step] normalize input")
    print(run(normalize_cmd).strip())

    semantics = parse_sql_semantics(normalized_jsonl)
    print(
        f"[step] parsed semantics: queries={len(semantics['queries'])}, tables={len(semantics['tables'])}, metrics={len(semantics['metrics'])}, fields={len(semantics['filter_fields'])}"
    )

    build_initial_ontology(topic, semantics, ontology_file)
    print(f"[step] ontology generated: {ontology_file}")

    report: dict[str, Any] = {
        "topic": topic,
        "topic_slug": topic_slug,
        "input": str(input_path),
        "normalized_txt": str(normalized_txt),
        "normalized_jsonl": str(normalized_jsonl),
        "ontology_file": str(ontology_file),
        "iterations": [],
    }

    if args.skip_align:
        report["status"] = "ontology_built_only"
    else:
        api_key = args.llm_api_key or os.environ.get("LLM_API_KEY", "")
        if not api_key:
            raise SystemExit("LLM_API_KEY is required for alignment runs (pass --llm-api-key or env)")

        run_script = project_root / "tools" / "run_deepseek_pipeline.sh"
        cognee_root = Path(args.cognee_root).resolve() if args.cognee_root else None

        for i in range(1, args.max_iterations + 1):
            if args.feedback_jsonl:
                fb = Path(args.feedback_jsonl).resolve()
                if fb.exists():
                    print(f"[step] apply feedback patches before iteration {i}: {fb}")
                    apply_feedback_patches(project_root, ontology_file, fb)

            tag = f"test_factory_{topic_slug}_{now}_iter{i}"
            dataset = f"test_{topic_slug}_{now}_iter{i}"
            spec_path = Path("/tmp") / f"aof_spec.{topic_slug}.{now}.iter{i}.json"
            write_spec(spec_path, project_root, dataset, ontology_file, args.matching_cutoff, cognee_root)

            env = os.environ.copy()
            env["LLM_API_KEY"] = api_key

            print(f"[step] alignment iteration {i}: dataset={dataset}")
            started_at = time.time()
            _ = run([str(run_script), str(spec_path), str(normalized_txt), tag], env=env)

            if not cognee_root:
                raise RuntimeError(
                    "cognee_root is required for alignment log parsing. Set --cognee-root or COGNEE_ROOT."
                )
            log_file = newest_cognee_log(cognee_root, started_at)
            stats = parse_alignment_from_log(log_file)

            iter_info: dict[str, Any] = {
                "iteration": i,
                "dataset": dataset,
                "spec": str(spec_path),
                "aof_add_result": str(project_root / "logs" / f"aof_add_result.{tag}.json"),
                "aof_run_result": str(project_root / "logs" / f"aof_run_result.{tag}.json"),
                **stats,
                "added_from_unmatched": [],
            }
            report["iterations"].append(iter_info)

            print(
                f"[iter {i}] matched={stats['matched_count']} unmatched={stats['unmatched_count']} log={log_file.name}"
            )

            if stats["unmatched_count"] == 0:
                report["status"] = "aligned"
                break

            added = augment_ontology(ontology_file, stats["unmatched"])
            iter_info["added_from_unmatched"] = added
            print(f"[iter {i}] ontology augmented: +{len(added)} concepts")

        if "status" not in report:
            report["status"] = "max_iterations_reached"

    report_json = report_dir / f"TEST_DATA_alignment_report_{topic_slug}_{now}.json"
    report_md = report_dir / f"TEST_DATA_alignment_report_{topic_slug}_{now}.md"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# TEST_DATA 对齐报告：{topic}",
        "",
        f"- 输入: `{input_path}`",
        f"- 规范化TXT: `{normalized_txt}`",
        f"- 本体文件: `{ontology_file}`",
        f"- 状态: `{report.get('status','unknown')}`",
        "",
        "## 迭代结果",
        "",
    ]
    if report["iterations"]:
        for it in report["iterations"]:
            md_lines += [
                f"### Iter {it['iteration']}",
                f"- dataset: `{it['dataset']}`",
                f"- matched: `{it['matched_count']}`",
                f"- unmatched: `{it['unmatched_count']}`",
                f"- log: `{it['log_file']}`",
            ]
            if it.get("unmatched"):
                md_lines.append("- unmatched_terms:")
                for u in it["unmatched"]:
                    md_lines.append(f"  - `{u['term']}` ({u['category']})")
            if it.get("added_from_unmatched"):
                md_lines.append("- auto_added:")
                for a in it["added_from_unmatched"]:
                    md_lines.append(f"  - `{a}`")
            md_lines.append("")
    else:
        md_lines.append("- 未执行对齐迭代（skip-align）")

    report_md.write_text("\n".join(md_lines), encoding="utf-8")

    # Optional: pattern summary based on AOF methodology
    analyzer = project_root / "tools" / "ontology_factory" / "analyze_alignment_patterns.py"
    if analyzer.exists() and report.get("iterations"):
        py = project_root / ".venv" / "bin" / "python"
        if not py.exists():
            py = Path("python3")
        analysis_json = report_dir / f"TEST_DATA_alignment_patterns_{topic_slug}_{now}.json"
        analysis_md = report_dir / f"TEST_DATA_alignment_patterns_{topic_slug}_{now}.md"
        _ = run(
            [
                str(py),
                str(analyzer),
                "--report-json",
                str(report_json),
                "--output-json",
                str(analysis_json),
                "--output-md",
                str(analysis_md),
            ]
        )
        print(f"- pattern_json: {analysis_json}")
        print(f"- pattern_md: {analysis_md}")

    feedback_paths = export_feedback_candidates(project_root, report_json)
    if feedback_paths:
        fb_jsonl, fb_md = feedback_paths
        print(f"- feedback_jsonl: {fb_jsonl}")
        print(f"- feedback_md: {fb_md}")

    print("[done]")
    print(f"- ontology: {ontology_file}")
    print(f"- report_json: {report_json}")
    print(f"- report_md: {report_md}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
