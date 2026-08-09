#!/usr/bin/env python3
"""检索增益对比：baseline（直接 cognee.add）vs enhanced（add parse_document 产物）。

流程：
1. 对 tools/retrieval_gain/dataset 下 6 份领域 PDF 建档两个 cognee 数据集：
   - baseline：cognee.add(原 PDF)
   - enhanced：cognee.add(parse_document() 输出的 clean markdown)
2. 对每管道各跑 20 查询（queries.json），收集桥源命中文档。
3. 用 eval.compute_metrics 算 hit_rate@k / MRR，输出对比报告。

前提：cognee 已配置 embedding/LLM 端点（.env，见 README）。

用法：
    python run_pipeline.py --spec aof_spec.example.json --only baseline|enhanced|all
    输出写 tools/retrieval_gain/runs/{baseline,enhanced}_{ts}.json + 报告
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.retrieval_gain import eval as rg_eval  # noqa: E402

DATASET_DIR = Path(__file__).resolve().parent / "dataset"
QUERIES_JSON = Path(__file__).resolve().parent / "queries.json"
RUNS_DIR = Path(__file__).resolve().parent / "runs"


def _load_queries() -> list[dict]:
    return json.loads(QUERIES_JSON.read_text(encoding="utf-8"))["queries"]


def _spec_dataset_name(spec: dict) -> str:
    return spec.get("dataset") or spec.get("dataset_name") or "retail_kg"


def _import_cognee(root: str | None = None):
    import sys as _s
    from pathlib import Path as _P

    if root:
        r = str(_P(root).resolve())
        if r not in _s.path:
            _s.path.insert(0, r)
    import cognee

    return cognee


async def build_baseline_dataset(cognee, pdf_files: list[Path], dataset_name: str) -> None:
    from cognee.api.v1.add.add import add as cognee_add

    await cognee_add([str(f) for f in pdf_files], dataset_name=dataset_name)
    await cognee.cognify(dataset_name=dataset_name)
    print(f"[baseline] 建档完成 dataset={dataset_name} files={len(pdf_files)}")


async def build_enhanced_dataset(cognee, pdf_files: list[Path], dataset_name: str) -> None:
    from cognee.api.v1.add.add import add as cognee_add

    from bridge.document_parser import ParserConfig, parse_document

    config = ParserConfig(enabled=True, cache_enabled=False)
    docs: list[str] = []
    for f in pdf_files:
        r = parse_document(f, config=config)
        if r.use_raw_path:
            docs.append(str(f))  # fallback：原文件
            print(f"[enhanced] {f.name}: fallback → 原文件")
        else:
            docs.append(r.doc.content)  # clean markdown
            print(f"[enhanced] {f.name}: 解析层 markdown ({len(r.doc.content)} 字符)")
    await cognee_add(docs, dataset_name=dataset_name)
    await cognee.cognify(dataset_name=dataset_name)
    print(f"[enhanced] 建档完成 dataset={dataset_name} docs={len(docs)}")


async def run_retrieval(dataset_name: str, queries: list[dict], cognee_root: str | None) -> dict[str, Any]:
    from exporters.rag_service import rag_retrieve

    results: dict[str, list[dict]] = {}
    for q in queries:
        rr = await rag_retrieve(
            query=q["query"], dataset_name=dataset_name, limit=8, cognee_root=cognee_root
        )
        # 归一化结果为 eval 可消费的形态（保留 source）
        items = [
            {"source": it.get("source"), "text": it.get("text"), "type": it.get("type")}
            for it in rr.results
        ]
        results[str(q["id"])] = items
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipelines", default="all", choices=["all", "baseline", "enhanced"])
    ap.add_argument("--cognee-root", default="/Users/chaihao/LLM/cognee")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--dataset", default=None)
    args = ap.parse_args()

    pdfs = sorted(DATASET_DIR.glob("*.pdf"))
    queries = _load_queries()
    goldens = {str(q["id"]): q["golden"] for q in queries}
    pub_dataset = args.dataset or "retail_kg_parser_gain"

    cognee = _import_cognee(args.cognee_root)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")

    def run_one(pipeline: str, query_results: dict) -> None:
        doc_titles = {q["golden"]: q.get("doc_title", "") for q in queries}
        metrics = rg_eval.compute_metrics(query_results, goldens, k=args.k, doc_titles=doc_titles)
        out = RUNS_DIR / f"{pipeline}_{ts}.json"
        out.write_text(
            json.dumps({"pipeline": pipeline, "metrics": metrics, "results": query_results},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n===== {pipeline.upper()} =====")
        print(f"  hit_rate@{args.k}={metrics['hit_rate']}  MRR={metrics['mrr']}")
        print(f"  结果写入 {out}")

    async def _run_all():
        if args.pipelines in ("all", "baseline"):
            await build_baseline_dataset(cognee, pdfs, f"{pub_dataset}_baseline")
            base_res = await run_retrieval(f"{pub_dataset}_baseline", queries, args.cognee_root)
            run_one("baseline", base_res)
        if args.pipelines in ("all", "enhanced"):
            await build_enhanced_dataset(cognee, pdfs, f"{pub_dataset}_enhanced")
            enh_res = await run_retrieval(f"{pub_dataset}_enhanced", queries, args.cognee_root)
            run_one("enhanced", enh_res)

    asyncio.run(_run_all())

    if args.pipelines == "all":
        print("\n===== 结果汇总 =====")
        print("（分别见 baseline_*.json / enhanced_*.json）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
