#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""AOF SemanticResource 确定性检索索引（SQLite FTS5 trigram + 子串兜底）。

对 resources_full.json（5,470 个 SemanticResource）建立零外部依赖的检索索引：
- 展示名（中文名）、实体名、描述、标签、属性清单、来源表、关系说明全部入索引
- FTS5 trigram 支持中文 >=3 字子串；短查询走全表 LIKE 兜底（5k 行毫秒级）
- 检索完全不依赖 LLM / embedding API，离线可用

用法：
  python tools/build_resource_index.py build                 # 构建索引
  python tools/build_resource_index.py search "圣遗物" -k 10  # 检索
  python tools/build_resource_index.py search "深渊 甘雨"     # 多词 AND
  python tools/build_resource_index.py stats                 # 统计
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

DEFAULT_RESOURCES = str(
    Path(__file__).resolve().parents[1] / "data" / "semantic_assets" / "resources_full.json"
)
DEFAULT_DB = "/Users/chaihao/LLM/AOF/data/aof_resource_index/resource_index.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    rowid_alias INTEGER PRIMARY KEY,
    resource_id TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    description TEXT NOT NULL,
    tags TEXT NOT NULL,
    search_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kind ON resources(kind);
CREATE INDEX IF NOT EXISTS idx_domain ON resources(domain);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    resource_id UNINDEXED,
    text,
    tokenize='trigram'
);
"""


def compose_search_text(r: dict) -> str:
    """把资源的关键语义面拼成一段可检索文本。"""
    spec = r.get("spec") or {}
    parts: list[str] = [
        r.get("display_name") or "",
        r.get("name") or "",
        r.get("kind") or "",
        r.get("domain") or "",
        " ".join(r.get("tags") or []),
        r.get("description") or "",
    ]
    kind = r.get("kind")
    if kind == "ObjectType":
        parts.append(spec.get("entity") or "")
        parts.append(spec.get("cn") or "")
        if spec.get("pk"):
            parts.append(f"主键 {spec.get('pk')} {spec.get('pk_type') or ''}")
        attrs = spec.get("attrs") or []
        if attrs:
            parts.append("属性: " + " ".join(attrs))
        tables = spec.get("source_tables") or []
        if tables:
            parts.append("来源表: " + " ".join(tables))
    elif kind == "RelationType":
        parts.append(f"{spec.get('from')} --{spec.get('type')}--> {spec.get('to')}")
        if spec.get("card"):
            parts.append(f"基数 {spec.get('card')}")
        via = spec.get("via") or []
        if via:
            parts.append("关联字段: " + " ".join(via))
    elif kind == "Playbook":
        if spec.get("title"):
            parts.append(spec["title"])
        tq = spec.get("trigger_questions") or []
        if tq:
            parts.append("场景问题: " + " ".join(tq))
        metrics = spec.get("metrics") or []
        if metrics:
            parts.append("口径主题: " + " ".join(metrics))
        tables = spec.get("tables") or []
        if tables:
            names = [t.get("full_name", "") for t in tables if isinstance(t, dict)]
            aliases = [t.get("alias", "") for t in tables if isinstance(t, dict)]
            parts.append("来源表: " + " ".join(n for n in names if n))
            if any(aliases):
                parts.append("表别名: " + " ".join(a for a in aliases if a))
        caps = spec.get("capabilities") or {}
        if caps:
            parts.append("能力状态: " + " ".join(f"{k}={v}" for k, v in caps.items()))
        scenes = spec.get("scenes") or []
        if scenes:
            parts.append(" ".join(f"{s.get('slug', '')} {s.get('title', '')}" for s in scenes if isinstance(s, dict)))
    elif kind == "Metric":
        if spec.get("metric"):
            parts.append(spec["metric"])
        if spec.get("topic"):
            parts.append(f"主题: {spec['topic']}")
        if spec.get("scene_title"):
            parts.append(f"场景: {spec['scene_title']}")
        rl = spec.get("red_lines") or []
        if rl:
            parts.append("红线: " + " ".join(rl))
        keys = spec.get("metric_keys") or []
        if keys:
            parts.append("指标族: " + " ".join(keys))
        if spec.get("family"):
            parts.append(spec["family"])
        tables = spec.get("resolved_tables") or []
        if tables:
            parts.append(" ".join(tables))
    else:
        for ev in r.get("evidence") or []:
            if ev.get("source_uri"):
                parts.append(ev["source_uri"])
    text = " \n ".join(p for p in parts if p)
    return re.sub(r"\s+", " ", text).strip()


def build(resources_path: str, db_path: str) -> None:
    with open(resources_path, encoding="utf-8") as f:
        data = json.load(f)
    resources = data["resources"]

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    if Path(db_path).exists():
        Path(db_path).unlink()

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    rows = []
    fts_rows = []
    for r in resources:
        text = compose_search_text(r)
        rows.append((
            r["resource_id"], r.get("kind") or "", r.get("name") or "",
            r.get("display_name") or "", r.get("domain") or "",
            r.get("description") or "", json.dumps(r.get("tags") or [], ensure_ascii=False),
            text,
        ))
        fts_rows.append((r["resource_id"], text))

    conn.executemany(
        "INSERT INTO resources(rowid_alias, resource_id, kind, name, display_name, domain, description, tags, search_text) "
        "VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.executemany("INSERT INTO fts(resource_id, text) VALUES (?, ?)", fts_rows)
    conn.commit()

    kinds = Counter(r.get("kind") for r in resources)
    print(f"[build] resources={len(resources)} kinds={dict(kinds)}")
    print(f"[build] db={db_path} size={Path(db_path).stat().st_size/1024:.0f}KB")
    conn.close()


def _fts_query(conn: sqlite3.Connection, query: str, k: int) -> list[tuple[str, float]]:
    """FTS5 trigram 检索；多词 AND。query 任一 token <3 字符时 trigram 不可用 → 返回空。"""
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    usable = [t for t in tokens if len(t) >= 3]
    if not usable:
        return []
    match_expr = " AND ".join(f'"{t.replace(chr(34), chr(39)*2)}"' for t in usable)
    try:
        cur = conn.execute(
            "SELECT resource_id, bm25(fts) AS score FROM fts WHERE fts MATCH ? ORDER BY score LIMIT ?",
            (match_expr, k * 3),
        )
        return [(rid, float(score)) for rid, score in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def _substring_scan(conn: sqlite3.Connection, query: str, k: int) -> list[tuple[str, float]]:
    """子串兜底打分：display_name/name 命中权重最高，其次 description/tags，最后全文。"""
    q = query.strip().lower()
    if not q:
        return []
    tokens = [t for t in re.split(r"\s+", q) if t]
    scores: dict[str, float] = {}
    cur = conn.execute(
        "SELECT resource_id, display_name, name, description, tags, search_text FROM resources"
    )
    for rid, dn, nm, desc, tags, text in cur.fetchall():
        hay_dn = (dn or "").lower()
        hay_nm = (nm or "").lower()
        hay_desc = (desc or "").lower()
        hay_tags = (tags or "").lower()
        hay_text = (text or "").lower()
        hit_all = True
        score = 0.0
        for t in tokens:
            if t in hay_dn:
                score += 12.0
            elif t in hay_nm:
                score += 8.0
            elif t in hay_tags:
                score += 4.0
            elif t in hay_desc:
                score += 3.0
            elif t in hay_text:
                score += 1.0
            else:
                hit_all = False
                break
        if hit_all and score > 0:
            scores[rid] = score
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[: k * 3]


def _or_fallback(conn: sqlite3.Connection, query: str, k: int) -> tuple[list[tuple[str, float]], list[str]]:
    """AND 无命中时的 OR 兜底：逐 token 子串打分合并（0.6 折减），返回 (hits, 命中的 tokens)。"""
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    scores: dict[str, float] = {}
    matched: list[str] = []
    for t in tokens:
        hits = _substring_scan(conn, t, k)
        if hits:
            matched.append(t)
        for rid, s in hits:
            scores[rid] = scores.get(rid, 0.0) + s * 0.6
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[: k * 3], matched


def search(db_path: str, query: str, k: int, kind: str | None = None) -> tuple[list[dict], float, bool]:
    """返回 (results, elapsed_ms, partial)。partial=True 表示 AND 无命中、由 OR 兜底给出部分匹配。"""
    conn = sqlite3.connect(db_path)
    t0 = time.time()
    fts_hits = _fts_query(conn, query, k)
    sub_hits = _substring_scan(conn, query, k)

    merged: dict[str, float] = {}
    for rid, score in fts_hits:
        # bm25 越小越好，转正分
        merged[rid] = merged.get(rid, 0.0) + max(0.0, -score) + 1.0
    for rid, score in sub_hits:
        merged[rid] = merged.get(rid, 0.0) + score * 1.5

    partial = False
    if not merged and len(re.split(r"\s+", query.strip())) > 1:
        or_hits, _matched_tokens = _or_fallback(conn, query, k)
        if or_hits:
            merged = dict(or_hits)
            partial = True

    ranked = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:k]
    results = []
    for rid, score in ranked:
        row = conn.execute(
            "SELECT resource_id, kind, name, display_name, domain, description, tags FROM resources WHERE resource_id=?",
            (rid,),
        ).fetchone()
        if row is None:
            continue
        if kind and row[1] != kind:
            continue
        results.append({
            "resource_id": row[0],
            "kind": row[1],
            "name": row[2],
            "display_name": row[3],
            "domain": row[4],
            "description": row[5],
            "tags": json.loads(row[6] or "[]"),
            "score": round(score, 3),
        })
    elapsed_ms = (time.time() - t0) * 1000
    conn.close()
    return results, elapsed_ms, partial  # type: ignore[return-value]


def stats(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    kinds = conn.execute("SELECT kind, COUNT(*) FROM resources GROUP BY kind ORDER BY 2 DESC").fetchall()
    domains = conn.execute("SELECT domain, COUNT(*) FROM resources GROUP BY domain ORDER BY 2 DESC").fetchall()
    print(f"resources={total}")
    print("kinds:")
    for k, c in kinds:
        print(f"  {k}: {c}")
    print("domains:")
    for k, c in domains:
        print(f"  {k}: {c}")
    conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="AOF 资源检索索引")
    ap.add_argument("command", choices=["build", "search", "stats"])
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--resources", default=DEFAULT_RESOURCES)
    ap.add_argument("-k", type=int, default=10)
    ap.add_argument("--kind", default=None, help="按 kind 过滤（ObjectType/RelationType/...）")
    args = ap.parse_args()

    if args.command == "build":
        build(args.resources, args.db)
        return 0
    if args.command == "stats":
        stats(args.db)
        return 0

    if not Path(args.db).exists():
        print(f"[error] 索引不存在，请先 build: {args.db}")
        return 1
    results, elapsed_ms, partial = search(args.db, args.query, args.k, args.kind)  # type: ignore[misc]
    flag = " [OR 兜底：部分匹配]" if partial else ""
    print(f"[search] query={args.query!r} hits={len(results)} elapsed={elapsed_ms:.1f}ms{flag}")
    for i, r in enumerate(results, 1):
        print(f"\n#{i} [{r['kind']}] {r['display_name']}  (score={r['score']})")
        print(f"   {r['resource_id']}")
        desc = r["description"]
        print(f"   {desc[:180]}{'…' if len(desc) > 180 else ''}")
        if r["tags"]:
            print(f"   tags: {' '.join(r['tags'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
