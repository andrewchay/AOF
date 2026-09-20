#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""hk4e 权威图谱可视化仪表盘 → 单文件交互 HTML（零外部依赖、离线可用）。

数据源（均为只读）：
  - 权威 Kuzu 图库   data/hk4e_ontology/cognee_graph_kuzu   （30,289 节点 / 60,692 边）
  - 资源检索索引     data/aof_resource_index/resource_index.sqlite3（5,470 SemanticResource）

产出四个视图：
  概览     统计卡片 + 节点/边/资源/域分布条形图
  语义桥接  genshin# 语义层 + 131 个 Avatar 实例 + wieldsElement 等桥接边
  子图探索  关键词 → 种子 + 1/2 跳邻域（2,680 类/实例节点的裁剪图）
  域级总览 七大域聚合图（类数/实例数/断言边 + 跨域关系权重）

用法：
  python tools/visualize_hk4e_graph.py                # 默认输出 visualizations/hk4e_ontology_dashboard.html
  python tools/visualize_hk4e_graph.py --open         # 生成后用默认浏览器打开
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

import kuzu

DEFAULT_DB = "/Users/chaihao/LLM/AOF/data/hk4e_ontology/cognee_graph_kuzu"
DEFAULT_INDEX = "/Users/chaihao/LLM/AOF/data/aof_resource_index/resource_index.sqlite3"
DEFAULT_OUT = "/Users/chaihao/LLM/AOF/visualizations/hk4e_ontology_dashboard.html"
DEFAULT_TTL = (
    "/Users/chaihao/.gravitas/agent-workspaces/aof/workspace-files/"
    ".context/hk4e_meta/aof/hk4e-complete.ttl"
)
TEMPLATE = Path(__file__).resolve().parent / "templates" / "hk4e_dashboard.html"
APP_JS = Path(__file__).resolve().parent / "templates" / "hk4e_dashboard_app.js"

HK4E_MARK = "hk4e.mihoyo.com"
SEM_MARK = "genshin.mihoyo.com"
STRUCT_REL = {"hasDomain", "hasRange", "subClassOf", "instance_of"}


def _local(uri: str) -> str:
    return uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def load_graph(db_path: str) -> tuple[dict, list[tuple[str, str, str]]]:
    """kuzu 只读直连，拉全量节点/边。"""
    conn = kuzu.Connection(kuzu.Database(db_path, read_only=True))

    def _exec(query: str) -> Any:
        res = conn.execute(query)
        assert not isinstance(res, list)
        return res
    nodes: dict[str, dict] = {}
    for nid, name, ntype, props in _exec(
        "MATCH (n:Node) RETURN n.id, n.name, n.type, n.properties"
    ).get_all():
        p: dict = {}
        if props:
            try:
                p = json.loads(props)
            except Exception:
                p = {}
        nodes[nid] = {
            "name": name or _local(nid),
            "type": ntype,
            "desc": (p.get("description") or "")[:220],
            "facts": p.get("facts") or {},
        }
    edges = [
        (a, rel, b)
        for a, rel, b in _exec(
            "MATCH (x:Node)-[r:EDGE]->(y:Node) RETURN x.id, r.relationship_name, y.id"
        ).get_all()
    ]
    return nodes, edges


def load_resource_kinds(index_path: str) -> tuple[dict[str, int], int]:
    if not Path(index_path).exists():
        return {}, 0
    with sqlite3.connect(index_path) as s:
        kinds = dict(s.execute("SELECT kind, COUNT(*) FROM resources GROUP BY kind"))
    return kinds, sum(kinds.values())


def build_domains(nodes: dict, edges: list) -> tuple[list[dict], list[dict]]:
    """七大域聚合：subClassOf 传递闭包 → 域归属 → 类/实例/断言边计数 + 跨域关系。"""
    parents: dict[str, list[str]] = defaultdict(list)
    for a, rel, b in edges:
        if rel == "subClassOf":
            parents[a].append(b)

    domain_ids = [
        nid
        for nid, n in nodes.items()
        if n["type"] == "EntityClass" and HK4E_MARK in nid and n["name"].endswith("域")
    ]
    domain_set = set(domain_ids)

    def root_of(start: str) -> str | None:
        if not start:
            return None
        seen = {start}
        q = deque([start])
        while q:
            cur = q.popleft()
            if cur in domain_set:
                return cur
            for p in parents.get(cur, ()):
                if p not in seen:
                    seen.add(p)
                    q.append(p)
        return None

    inst_of = {a: b for a, rel, b in edges if rel == "instance_of"}
    root_cache: dict[str, str | None] = {}

    def root_for(nid: str) -> str | None:
        if nid in root_cache:
            return root_cache[nid]
        n = nodes[nid]
        r = root_of(nid) if n["type"] == "EntityClass" else root_of(inst_of.get(nid, ""))
        root_cache[nid] = r
        return r

    dom_classes: Counter = Counter()
    dom_instances: Counter = Counter()
    for nid, n in nodes.items():
        if HK4E_MARK not in nid or n["type"] not in ("EntityClass", "Instance"):
            continue
        r = root_for(nid)
        if r:
            (dom_classes if n["type"] == "EntityClass" else dom_instances)[r] += 1

    dom_assert: Counter = Counter()
    dom_pair: Counter = Counter()
    dom_top_rels: dict[str, Counter] = defaultdict(Counter)
    for a, rel, b in edges:
        if rel in STRUCT_REL:
            continue
        ra, rb = root_for(a), root_for(b)
        if ra and rb:
            dom_assert[ra] += 1
            if ra != rb:
                dom_pair[frozenset((ra, rb))] += 1
                dom_top_rels[ra][rel] += 1

    def dom_name(d: str) -> str:
        return nodes[d]["name"] if d in nodes else _local(d)

    domains = [
        {
            "name": dom_name(d),
            "classes": dom_classes.get(d, 0),
            "instances": dom_instances.get(d, 0),
            "relations": dom_assert.get(d, 0),
            "top_rels": dom_top_rels.get(d, Counter()).most_common(6),
        }
        for d in sorted(domain_ids, key=lambda x: -dom_classes.get(x, 0))
    ]
    domain_edges = [
        {"s": dom_name(next(iter(p))), "t": dom_name(next(iter(p - {next(iter(p))}))), "w": w}
        for p, w in dom_pair.most_common()
    ]
    return domains, domain_edges


def build_semantic(nodes: dict, edges: list) -> dict:
    """语义层子图：genshin# 节点 + 邻接的 hk4e 节点 + 全部实例 + 它们之间的所有边。"""
    seeds = {nid for nid in nodes if SEM_MARK in nid}
    frontier = set(seeds)
    for a, _rel, b in edges:
        if a in seeds:
            frontier.add(b)
        if b in seeds:
            frontier.add(a)
    for a, rel, b in edges:
        if rel == "instance_of":
            frontier.add(a)  # 实例
            frontier.add(b)
    sub_edges = [(a, rel, b) for a, rel, b in edges if a in frontier and b in frontier]
    out_nodes = [
        {"id": nid, "name": nodes[nid]["name"], "type": nodes[nid]["type"],
         "desc": nodes[nid]["desc"], "facts": nodes[nid]["facts"] if nodes[nid]["type"] == "Instance" else {}}
        for nid in sorted(frontier)
    ]
    return {"nodes": out_nodes, "edges": [[a, rel, b] for a, rel, b in sub_edges]}


def build_explorer(nodes: dict, edges: list) -> dict:
    """裁剪探索图：EntityClass/Instance/Ontology 节点 + subClassOf/instance_of/断言边（去 2.4 万属性节点噪音）。"""
    keep = {
        nid: n
        for nid, n in nodes.items()
        if n["type"] in ("EntityClass", "Instance", "Ontology")
    }
    kept = [(a, rel, b) for a, rel, b in edges if rel not in ("hasDomain", "hasRange") and a in keep and b in keep]
    id2i = {nid: i for i, nid in enumerate(keep)}
    rels = sorted({rel for _, rel, _ in kept})
    rel_idx = {r: i for i, r in enumerate(rels)}

    adj: list[list[list[int]]] = [[] for _ in keep]
    seen_pairs: set = set()
    for a, rel, b in kept:
        i, j, e = id2i[a], id2i[b], rel_idx[rel]
        key = (i, j, e)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        adj[i].append([j, e])
        adj[j].append([i, e])

    out_nodes = [
        {
            "id": nid,
            "name": n["name"],
            "type": n["type"],
            "local": _local(nid),
            "desc": n["desc"][:160],
            **({"facts": n["facts"]} if n["facts"] else {}),
        }
        for nid, n in keep.items()
    ]
    return {"nodes": out_nodes, "rels": rels, "adj": adj}


def main() -> int:
    ap = argparse.ArgumentParser(description="hk4e 图谱可视化仪表盘生成器")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--triples", type=int, default=131857, help="TTL 三元组数（展示用）")
    ap.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    args = ap.parse_args()

    t0 = time.time()
    print(f"[1/4] 读取 Kuzu（只读）: {args.db}")
    nodes, edges = load_graph(args.db)
    print(f"  nodes={len(nodes)} edges={len(edges)}")

    print("[2/4] 聚合统计 / 域闭包 / 子图")
    kinds, res_total = load_resource_kinds(args.index)
    node_types = dict(Counter(n["type"] for n in nodes.values()))
    rel_counter = Counter(rel for _, rel, _ in edges)
    edge_groups: list[list] = [[k, rel_counter[k]] for k in ("hasDomain", "hasRange", "subClassOf", "instance_of") if rel_counter.get(k)]
    assertions = [(r, c) for r, c in rel_counter.most_common() if r not in STRUCT_REL]
    edge_groups += [[r, c] for r, c in assertions[:6]]
    rest = sum(c for _, c in assertions[6:])
    if rest:
        edge_groups.append([f"其余 {len(assertions) - 6} 种断言关系", rest])
    domains, domain_edges = build_domains(nodes, edges)
    semantic = build_semantic(nodes, edges)
    explorer = build_explorer(nodes, edges)

    data = {
        "meta": {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "triples": args.triples,
            "nodes": len(nodes),
            "edges": len(edges),
            "db": args.db,
        },
        "stats": {
            "node_types": node_types,
            "edge_groups": edge_groups,
            "resource_kinds": kinds,
            "resource_total": res_total,
            "domains": domains,
            "domain_edges": domain_edges,
        },
        "semantic": semantic,
        "explorer": explorer,
    }

    print(f"[3/4] 组装 HTML: {args.out}")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (
        TEMPLATE.read_text(encoding="utf-8")
        .replace("__TRIPLES__", f"{args.triples:,}")
        .replace("__GENERATED__", data["meta"]["generated"])
        .replace("__DATA_JSON__", payload)
        .replace("/* __APP_JS__ */", APP_JS.read_text(encoding="utf-8"))
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"  size={out.stat().st_size / 1024:.0f}KB  "
          f"semantic={len(semantic['nodes'])}N/{len(semantic['edges'])}E  "
          f"explorer={len(explorer['nodes'])}N/{sum(len(a) for a in explorer['adj']) // 2}E")

    if args.open:
        import subprocess

        subprocess.run(["open", str(out)], check=False)
        print("[4/4] 已在默认浏览器打开")
    else:
        print(f"[4/4] 完成：open {out}")
    print(f"elapsed={time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
