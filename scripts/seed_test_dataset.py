#!/usr/bin/env python3
"""Seed a small test dataset for AOF integration validation.

This script:
1. Sets up Ollama as both LLM and embedding provider
2. Ingests 4 markdown documents into a fresh dataset
3. Runs cognify to extract entities/relations/embeddings
4. Verifies the results with hybrid_search, graph_analytics, graph_doctor, and markdown export

Usage:
    cd /Users/chaihao/LLM/AOF
    source .venv/bin/activate
    ENABLE_BACKEND_ACCESS_CONTROL=false python3 scripts/seed_test_dataset.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Configure Ollama before any Cognee import
# ---------------------------------------------------------------------------
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
os.environ["COGNEE_SKIP_CONNECTION_TEST"] = "true"
os.environ["LLM_API_KEY"] = "ollama"
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["LLM_MODEL"] = "qwen2.5:0.5b"
os.environ["LLM_ENDPOINT"] = "http://localhost:11434/v1"

os.environ["EMBEDDING_PROVIDER"] = "fastembed"
os.environ["EMBEDDING_MODEL"] = "BAAI/bge-small-en-v1.5"
os.environ["EMBEDDING_DIMENSIONS"] = "384"
os.environ["HUGGINGFACE_TOKENIZER"] = "sentence-transformers/all-MiniLM-L6-v2"

DATASET_NAME = f"aof_seed_test_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
SEED_DOCS_DIR = PROJECT_ROOT / "test_data" / "seed_docs"

SPEC = {
    "project_root": str(PROJECT_ROOT),
    "knowledge_repo": str(SEED_DOCS_DIR),
    "dataset": DATASET_NAME,
    "cognee": {"root": "/Users/chaihao/LLM/cognee"},
    "runtime": {
        "run_in_background": False,
        "incremental_loading": True,
        "data_per_batch": 20,
        "retries": 1,
        "backoff_seconds": 1.0,
    },
}


async def step_ingest() -> dict:
    print(f"\n[1/5] Ingesting documents into dataset: {DATASET_NAME}")
    from bridge.cognee_add_runner import run_add_from_spec
    from bridge.preflight import ensure_preflight_for_add

    data_path = str(SEED_DOCS_DIR)
    ensure_preflight_for_add(SPEC, data_paths=[data_path], require_api_key=False)

    result = await run_add_from_spec(SPEC, data=data_path)
    print("✅ Ingest complete")
    return result


async def step_cognify() -> dict:
    print(f"\n[2/5] Running cognify (LLM={os.environ['LLM_MODEL']}, Embed={os.environ['EMBEDDING_MODEL']})")
    from bridge.cognee_runner import run_cognify_from_spec
    from bridge.preflight import ensure_preflight_for_cognify

    ensure_preflight_for_cognify(SPEC, require_api_key=False)

    result = await run_cognify_from_spec(SPEC)
    print("✅ Cognify complete")
    return result


async def step_hybrid_search() -> None:
    print("\n[3/5] Verifying with hybrid_search")
    from bridge.hybrid_search import AOFHybridSearch

    engine = AOFHybridSearch()
    queries = [
        "Sarah Chen",
        "Acme AI funding",
        "Garry Tan",
    ]
    for q in queries:
        result = await engine.hybrid_search(
            query=q,
            dataset_name=DATASET_NAME,
            limit=5,
            expansion=False,
        )
        print(f"  Query: '{q}' -> {len(result.results)} results (kw={result.keyword_count}, vec={result.vector_count}, {result.execution_time_ms}ms)")
        for r in result.results[:2]:
            print(f"    - [{r.source}] {r.slug}: {r.chunk_text[:60]}...")


async def step_graph_analytics() -> None:
    print("\n[4/5] Running graph analytics")
    from bridge.graph_analytics import GraphAnalytics

    analyzer = GraphAnalytics(dataset_name=DATASET_NAME)
    stats = await analyzer.compute_statistics()
    print(f"  nodes={stats.node_count}, edges={stats.edge_count}, density={stats.density:.6f}")

    if stats.node_count > 0:
        top_nodes = await analyzer.pagerank(top_k=10)
        print("  Top nodes by PageRank:")
        for n in top_nodes[:5]:
            print(f"    - {n.label or n.node_id}: {n.pagerank:.4f}")

        communities = await analyzer.detect_communities()
        print(f"  Communities detected: {len(communities)}")


async def step_graph_doctor() -> None:
    print("\n[4.5/5] Running graph doctor")
    from bridge.graph_doctor import GraphDoctor

    doctor = GraphDoctor(dataset_name=DATASET_NAME)
    report = await doctor.check_and_print()
    assert report.score >= 0


async def step_export() -> None:
    print("\n[5/5] Exporting to markdown")
    from exporters.markdown_exporter import MarkdownExporter

    out_dir = PROJECT_ROOT / "test_data" / f"export_{DATASET_NAME}"
    exporter = MarkdownExporter()
    result = await exporter.export(
        dataset_name=DATASET_NAME,
        output_dir=out_dir,
        dry_run=False,
    )
    print(f"  Exported {result.pages_exported} pages to {out_dir} (mode={result.mode})")
    for p in result.files_created[:5]:
        print(f"    - {p}")

    # Cleanup export dir
    if out_dir.exists():
        shutil.rmtree(out_dir)
        print(f"  Cleaned up {out_dir}")


async def main() -> int:
    print("=" * 60)
    print("AOF Test Dataset Seeder")
    print("=" * 60)
    print(f"Dataset name: {DATASET_NAME}")
    print(f"Seed docs:    {SEED_DOCS_DIR}")

    if not SEED_DOCS_DIR.exists():
        print(f"❌ Seed docs directory not found: {SEED_DOCS_DIR}")
        return 1

    try:
        await step_ingest()
        await step_cognify()
        await step_hybrid_search()
        await step_graph_analytics()
        await step_graph_doctor()
        await step_export()
        print("\n🎉 All validation steps passed!")
        return 0
    except Exception as e:
        print(f"\n❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
