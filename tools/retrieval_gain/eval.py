#!/usr/bin/env python3
"""检索增益评测：hit_rate@k / MRR 计算（纯函数，可单测、环境无关）。

给定 query 结果集（含 source 命中文档名）和 golden，计算：
- hit_rate@k：TOP-k 内是否命中 golden（文档粒度）
- MRR：第一个 golden 排名的倒数；未命中记 0
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_PROJECT_ROOT))


def _extract_source(item: dict, top_fields: tuple[str, ...] = ("source", "filename", "document")) -> str:
    """从检索结果项提取命中文档标识（用于与 golden 匹配）。"""
    for f in top_fields:
        v = item.get(f)
        if v:
            return str(v)
    md = item.get("metadata") or {}
    for f in top_fields:
        v = md.get(f)
        if v:
            return str(v)
    return ""


def _extract_text(item: dict) -> str:
    return str(item.get("text") or "")


def _match(result_source: str, golden: str) -> bool:
    rs = (result_source or "").lower()
    g = (golden or "").lower()
    return g in rs or rs in g


def _match_text(result_text: str, doc_title: str) -> bool:
    """根据结果文本是否包含 doc_title 判定命中文档（source 常为 rrf，靠 text 内容归属）。"""
    rt = (result_text or "").replace(" ", "").lower()
    dt = (doc_title or "").replace(" ", "").lower()
    return bool(dt) and dt in rt


def compute_metrics(
    results: dict[str, list[dict[str, Any]]],
    goldens: dict[str, str],
    k: int = 5,
    doc_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """对查询结果计算 hit_rate@k 与 MRR。

    Args:
        results: {query_id: [result items]，每项含 source/text 字段}
        goldens: {query_id: golden 文档名}
        doc_titles: {golden 文档名: doc_title 标识}（用于 text 归属匹配；缺省时退回 source 匹配）
        k: TOP-k

    Returns:
        {"queries": N, "hit_count": M, "hit_rate": X, "mrr": Y, "per_query": [...]}
    """
    per_query = []
    hits = 0
    mrrs = []
    for qkey, golden in goldens.items():
        items = results.get(qkey, [])[:k]
        rank = None
        dt = (doc_titles or {}).get(golden)
        for i, it in enumerate(items, start=1):
            matched = _match_text(_extract_text(it), dt) if dt else _match(_extract_source(it), golden)
            if matched:
                rank = i
                break
        hit = rank is not None
        rr = 1.0 / rank if rank else 0.0
        if hit:
            hits += 1
        mrrs.append(rr)
        per_query.append({"id": qkey, "hit": bool(hit), "rank": rank, "rr": round(rr, 4)})

    n = len(goldens)
    return {
        "queries": n,
        "k": k,
        "hit_count": hits,
        "hit_rate": round(hits / n, 4) if n else 0.0,
        "mrr": round(sum(mrrs) / n, 4) if n else 0.0,
        "per_query": per_query,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-json", required=True, help="检索结果 JSON：{query或id: [items]}")
    ap.add_argument("--queries-json", required=True, help="查询集：含 queries 数组（id/query/golden）")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    raw = json.loads(Path(args.results_json).read_text(encoding="utf-8"))
    # 兼容 run_pipeline 存的结构：{pipeline, metrics, results:{id:[...]}}
    if isinstance(raw, dict) and "results" in raw:
        raw = raw["results"]
    qdata = json.loads(Path(args.queries_json).read_text(encoding="utf-8"))

    # 归一化：结果 key 用 query id
    by_id: dict[str, list[dict]] = {}
    # 若结果 key 已是 query id
    for q in qdata["queries"]:
        qid = str(q["id"])
        src = raw.get(qid)
        if src is None:
            # 尝试按 query 文本匹配
            for rk, rv in raw.items():
                if rk == q["query"] or rk.strip() == q["query"].strip():
                    src = rv
                    break
        by_id[qid] = src or []

    goldens = {str(q["id"]): q["golden"] for q in qdata["queries"]}
    doc_titles = {q["golden"]: q.get("doc_title", "") for q in qdata["queries"]}
    metrics = compute_metrics(by_id, goldens, k=args.k, doc_titles=doc_titles)

    print(f"检索增益 - {metrics['queries']} 查询 @TOP{metrics['k']}")
    print(f"  hit_count={metrics['hit_count']}  hit_rate=@{args.k}={metrics['hit_rate']}  MRR={metrics['mrr']}")
    for p in metrics["per_query"]:
        mark = "HIT" if p["hit"] else "-- "
        print(f"  [{mark}] q{p['id']} rank={p['rank']} rr={p['rr']}")
    return 0


if __name__ == "__main__":
    __import__("sys").exit(main())
