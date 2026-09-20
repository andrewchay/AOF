#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""权威 OWL/TTL 直写 Cognee 图存储（Kuzu）—— 零 LLM 调用。

背景：cognify 全量 LLM 抽取对 3,285 个 schema chunk 不可行（数小时 + 不稳定）。
本项目已有经精修校验的权威本体（hk4e-complete.ttl，~13 万三元组），
本脚本用 rdflib 确定性解析后，经 cognee 的 KuzuAdapter.add_nodes/add_edges
直接物化为图，供 bridge.graph_retrieval / MarkdownExporter 复用。

节点类型：
  Ontology / EntityClass / Instance / ObjectProperty / DatatypeProperty
边：
  subClassOf / hasDomain / hasRange / instance_of / <ObjectProperty 本地名>

用法：
  python tools/ingest_authoritative_graph.py                # 默认路径
  python tools/ingest_authoritative_graph.py --rebuild      # 重建库
  python tools/ingest_authoritative_graph.py --stats-only   # 只看统计
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
OWL_CLASS = "http://www.w3.org/2002/07/owl#Class"
OWL_NAMED_INDIVIDUAL = "http://www.w3.org/2002/07/owl#NamedIndividual"
OWL_OBJECT_PROPERTY = "http://www.w3.org/2002/07/owl#ObjectProperty"
OWL_DATATYPE_PROPERTY = "http://www.w3.org/2002/07/owl#DatatypeProperty"
OWL_ONTOLOGY = "http://www.w3.org/2002/07/owl#Ontology"
OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDFS_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"
RDFS_SUBCLASSOF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain"
RDFS_RANGE = "http://www.w3.org/2000/01/rdf-schema#range"

DEFAULT_TTL = str(
    Path(__file__).resolve().parents[1] / "data" / "semantic_assets" / "hk4e-complete.ttl"
)
DEFAULT_DB_DIR = "/Users/chaihao/LLM/AOF/data/hk4e_ontology"
DEFAULT_COGNEE_ROOT = "/Users/chaihao/LLM/cognee"
KUZU_FILENAME = "cognee_graph_kuzu"

NODE_BATCH = 512
EDGE_BATCH = 512


class GraphNode(BaseModel):
    """KuzuAdapter.add_nodes 只要求 model_dump() 提供 id/name/type，其余入 properties JSON。"""

    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    type: str
    description: str = ""


def _local(uri: str) -> str:
    return uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or uri


def _pick_label(labels: list[tuple[str | None, str]]) -> str | None:
    """优先 @zh → 无语言 → @en。"""
    zh = next((v for lang, v in labels if lang == "zh"), None)
    plain = next((v for lang, v in labels if lang is None), None)
    en = next((v for lang, v in labels if lang == "en"), None)
    return zh or plain or en


def parse_ttl(ttl_path: Path) -> dict[str, Any]:
    """rdflib 解析 TTL → nodes/edges/统计。完全确定性，无网络无 LLM。"""
    from rdflib import Graph, Literal
    from rdflib.namespace import OWL, RDF, RDFS

    g = Graph()
    g.parse(ttl_path.as_posix(), format="turtle")

    type_map: dict[str, set[str]] = {}
    labels: dict[str, list[tuple[str | None, str]]] = {}
    comments: dict[str, str] = {}

    edges: set[tuple[str, str, str]] = set()
    edge_props: dict[tuple[str, str, str], dict[str, Any]] = {}

    obj_prop_uris: set[str] = set()

    for s, p, o in g:
        s_str = str(s) if not isinstance(s, type(None)) else ""
        if isinstance(s, type(None)):
            continue
        p_str = str(p)
        o_str = str(o)

        if p_str == str(RDF.type):
            o_val = str(o)
            if o_val == str(OWL.Class):
                type_map.setdefault(s_str, set()).add("EntityClass")
            elif o_val == str(OWL.NamedIndividual):
                type_map.setdefault(s_str, set()).add("Instance")
            elif o_val == str(OWL.ObjectProperty):
                type_map.setdefault(s_str, set()).add("ObjectProperty")
                obj_prop_uris.add(s_str)
            elif o_val == str(OWL.DatatypeProperty):
                type_map.setdefault(s_str, set()).add("DatatypeProperty")
            elif o_val == str(OWL.Ontology):
                type_map.setdefault(s_str, set()).add("Ontology")
        elif p_str == str(RDFS.label):
            lang = getattr(o, "language", None)
            labels.setdefault(s_str, []).append((lang or None, str(o)))
        elif p_str == str(RDFS.comment):
            comments.setdefault(s_str, str(o))
        elif p_str == str(RDFS.subClassOf):
            edges.add((s_str, o_str, "subClassOf"))
        elif p_str == str(RDFS.domain):
            edges.add((s_str, o_str, "hasDomain"))
        elif p_str == str(RDFS.range):
            edges.add((s_str, o_str, "hasRange"))

    # 实例识别：非 OWL 元类型的 rdf:type 断言（如 Avatar_10000002 a Avatar）
    for s, p, o in g.triples((None, RDF.type, None)):
        o_str = str(o)
        if o_str in (str(OWL.Class), str(OWL.NamedIndividual), str(OWL.ObjectProperty),
                     str(OWL.DatatypeProperty), str(OWL.Ontology), str(OWL.Thing)):
            continue
        if not _is_http(o_str):
            continue
        s_str2 = str(s)
        type_map.setdefault(s_str2, set()).add("Instance")
        edges.add((s_str2, o_str, "instance_of"))

    # 实例上的数据属性事实（元素/星级/avatar_id 等）折叠进节点属性
    instance_uris = {u for u, kinds in type_map.items() if "Instance" in kinds}
    facts: dict[str, dict[str, list[str]]] = {}
    for s, p, o in g:
        s_str = str(s)
        if s_str not in instance_uris:
            continue
        p_str = str(p)
        if p_str in (str(RDF.type), str(RDFS.label), str(RDFS.comment)):
            continue
        key = _local(p_str)
        if isinstance(o, Literal):
            facts.setdefault(s_str, {}).setdefault(key, []).append(str(o))
        elif _is_http(str(o)):
            facts.setdefault(s_str, {}).setdefault(key, []).append(_local(str(o)))

    # ObjectProperty 域值断言边：domain -[prop 本地名]-> range
    prop_domain: dict[str, str] = {}
    prop_range: dict[str, str] = {}
    for s, p, o in g.triples((None, RDFS.domain, None)):
        prop_domain[str(s)] = str(o)
    for s, p, o in g.triples((None, RDFS.range, None)):
        prop_range[str(s)] = str(o)

    n_prop_edges = 0
    for pu in obj_prop_uris:
        d = prop_domain.get(pu)
        r = prop_range.get(pu)
        if d and r and _is_http(d) and _is_http(r):
            rel = _local(pu)
            key = (d, r, rel)
            if key not in edges:
                edges.add(key)
                edge_props[key] = {"property_uri": pu, "label": _pick_label(labels.get(pu, [])) or rel}
                n_prop_edges += 1

    # 构建节点
    nodes: dict[str, GraphNode] = {}
    for uri in type_map:
        kinds = type_map[uri]
        ntype = "Instance" if "Instance" in kinds else (
            "ObjectProperty" if "ObjectProperty" in kinds else (
                "DatatypeProperty" if "DatatypeProperty" in kinds else (
                    "Ontology" if "Ontology" in kinds else "EntityClass"
                )
            )
        )
        label = _pick_label(labels.get(uri, [])) or _local(uri)
        extra: dict[str, Any] = {}
        desc = comments.get(uri, "")
        if ntype == "Instance":
            fv = facts.get(uri, {})
            flat = {k: ",".join(v) for k, v in fv.items()}
            extra["facts"] = flat
            if not desc and flat:
                desc = "; ".join(f"{k}={v}" for k, v in sorted(flat.items()))[:600]
        nodes[uri] = GraphNode(
            id=uri,
            name=label,
            type=ntype,
            description=desc,
            **extra,
        )

    # 补齐作为边目标但未声明的 URI（如 owl:Thing、跨命名空间引用）
    for a, b, _rel in edges:
        for uri in (a, b):
            if _is_http(uri) and uri not in nodes:
                nodes[uri] = GraphNode(id=uri, name=_local(uri), type="EntityClass", description="")

    type_counts = Counter(n.type for n in nodes.values())
    rel_counts = Counter(rel for _, _, rel in edges)

    return {
        "nodes": list(nodes.values()),
        "edges": list(edges),
        "edge_props": edge_props,
        "stats": {
            "triples": len(g),
            "nodes": len(nodes),
            "edges": len(edges),
            "node_types": dict(type_counts),
            "edge_types": dict(rel_counts.most_common()),
            "object_property_assertion_edges": n_prop_edges,
        },
    }


def _is_uri(x: Any) -> bool:
    return hasattr(x, "n3") and str(x).startswith("http")


def _is_http(s: str) -> bool:
    return isinstance(s, str) and s.startswith("http")


async def write_to_kuzu(db_path: str, nodes: list[GraphNode], edges: list[tuple[str, str, str]],
                        edge_props: dict[tuple[str, str, str], dict[str, Any]],
                        cognee_root: str) -> dict[str, Any]:
    """经 cognee KuzuAdapter 直写。"""
    if cognee_root not in sys.path:
        sys.path.insert(0, cognee_root)
    from cognee.infrastructure.databases.graph.kuzu.adapter import KuzuAdapter  # type: ignore

    adapter = KuzuAdapter(db_path=db_path)

    for i in range(0, len(nodes), NODE_BATCH):
        await adapter.add_nodes(nodes[i : i + NODE_BATCH])

    edge_tuples: list[tuple[str, str, str, dict[str, Any]]] = []
    for (a, b, rel) in edges:
        edge_tuples.append((a, b, rel, edge_props.get((a, b, rel), {})))
    for i in range(0, len(edge_tuples), EDGE_BATCH):
        await adapter.add_edges(edge_tuples[i : i + EDGE_BATCH])

    node_count = (await adapter.query("MATCH (n:Node) RETURN COUNT(n)"))[0][0]
    edge_count = (await adapter.query("MATCH ()-[r:EDGE]->() RETURN COUNT(r)"))[0][0]
    return {"kuzu_nodes": int(node_count), "kuzu_edges": int(edge_count)}


def main() -> int:
    ap = argparse.ArgumentParser(description="权威 TTL → Cognee Kuzu 直写（0 LLM 调用）")
    ap.add_argument("--ttl", default=DEFAULT_TTL)
    ap.add_argument("--db-dir", default=DEFAULT_DB_DIR)
    ap.add_argument("--cognee-root", default=DEFAULT_COGNEE_ROOT)
    ap.add_argument("--rebuild", action="store_true", help="删除已有 Kuzu 库后重建")
    ap.add_argument("--stats-only", action="store_true", help="仅解析并打印统计，不写库")
    args = ap.parse_args()

    ttl_path = Path(args.ttl)
    if not ttl_path.exists():
        print(f"[error] TTL 不存在: {ttl_path}")
        return 1

    print(f"[1/3] 解析 TTL: {ttl_path}")
    parsed = parse_ttl(ttl_path)
    st = parsed["stats"]
    print(f"  triples={st['triples']}  nodes={st['nodes']}  edges={st['edges']}")
    print(f"  node_types={st['node_types']}")
    print(f"  edge_types(top)={dict(list(st['edge_types'].items())[:8])}")

    if args.stats_only:
        return 0

    db_dir = Path(args.db_dir)
    db_path = db_dir.as_posix().rstrip("/") + "/" + KUZU_FILENAME
    if args.rebuild and db_dir.exists():
        shutil.rmtree(db_dir)
        print(f"[rebuild] 已删除 {db_dir}")
    db_dir.mkdir(parents=True, exist_ok=True)

    print(f"[2/3] 直写 Kuzu: {db_path}")
    result = asyncio.run(
        write_to_kuzu(db_path, parsed["nodes"], parsed["edges"], parsed["edge_props"], args.cognee_root)
    )
    print(f"  kuzu_nodes={result['kuzu_nodes']}  kuzu_edges={result['kuzu_edges']}")

    print("[3/3] 完成。检索侧请用同一库路径，例如：")
    print(f"  export GRAPH_FILE_PATH={db_dir}")
    print("  export GRAPH_DATABASE_PROVIDER=kuzu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
