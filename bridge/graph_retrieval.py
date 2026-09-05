#!/usr/bin/env python3
"""图谱路径召回模块 - AOF 多路召回中的图谱路.

与纯文本（keyword / vector）召回互补，本模块从知识图谱出发：
1. 从查询中提取实体关键词
2. 在图谱节点中匹配种子实体
3. 沿关系边做 1-2 跳邻居扩展，生成"实体 --关系--> 实体"的路径上下文
4. 输出带溯源（provenance）的图谱路径结果

使用示例:
    from bridge.graph_retrieval import graph_path_retrieval
    hits = await graph_path_retrieval(
        query="Ganyu 与 Qixing 的关系",
        dataset_name="genshin_ultimate_kg",
        limit=10,
    )
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import networkx as nx


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass
class GraphPathHit:
    """图谱路径召回的单条结果.

    text 是"实体 --关系--> 实体"的路径描述；
    provenance 保留完整溯源链路，供命中溯源使用.
    """
    seed: str
    node: str
    path: list[str]          # 如 ["Ganyu", "is_from", "Liyue"]
    text: str                # 路径描述文本
    score: float = 0.0
    hops: int = 1
    node_type: str = "unknown"
    provenance: dict[str, Any] = field(default_factory=dict)


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff ]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "untitled"


# ---------------------------------------------------------------------------
# Entity Extraction from Query
# ---------------------------------------------------------------------------
def extract_query_entities(query: str) -> list[str]:
    """从查询中提取候选实体关键词.

    策略：
    - 英文按空格切分，过滤停用词
    - 中文按 2-4 字连续片段（重叠窗口）提取，配合全词保留
    返回去重后的候选词列表（按长度降序，优先长词）。
    """
    if not query:
        return []

    raw = query.strip()
    if not raw:
        return []

    candidates: set[str] = set()

    # 英文/数字词
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]*", raw):
        if token.lower() not in _ENGLISH_STOPWORDS:
            candidates.add(token)

    # 中文片段（2-4 字重叠窗口）
    zh = re.findall(r"[\u4e00-\u9fff]+", raw)
    for seg in zh:
        candidates.add(seg)
        for size in (2, 3, 4):
            for i in range(0, max(len(seg) - size + 1, 1)):
                candidates.add(seg[i : i + size])

    # 保留原始查询本身（避免长查询噪音，仅当查询较短时）
    if len(raw.split()) <= 6 and len(raw) <= 20:
        candidates.add(raw)

    # 按长度降序，短候选在后（避免短词先匹配到无关节点）
    ordered = sorted(candidates, key=lambda c: (len(c), c), reverse=True)
    return ordered


_ENGLISH_STOPWORDS = {
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with",
    "how", "what", "why", "who", "which", "is", "are", "was", "were",
    "between", "from", "by", "at", "do", "does", "did", "relationship",
    "relation", "between", "about", "vs", "versus", "versus",
}


# ---------------------------------------------------------------------------
# Seed Matching
# ---------------------------------------------------------------------------
def match_seed_nodes(
    query: str,
    nodes: list[dict[str, Any]],
    max_seeds: int = 5,
) -> list[dict[str, Any]]:
    """在节点中匹配查询实体，返回种子节点列表（按匹配度降序）.

    node 结构约定: {"id": str, "label"/"name": str, "type": str}
    匹配策略：
    - 完全相等（不区分大小写）得分最高
    - 查询候选词是节点名的子串，或节点名是候选词子串
    - 中文按子串匹配
    """
    if not nodes:
        return []

    candidates = extract_query_entities(query)
    indexed: list[tuple[dict[str, Any], float, str]] = []

    for node in nodes:
        node_id = str(node.get("id") or node.get("name") or node.get("label") or "")
        label = str(node.get("label") or node.get("name") or node_id)
        if not label or not node_id:
            continue
        label_lower = label.lower()
        node_id_lower = node_id.lower()
        best_score = 0.0
        matched_key = ""

        for cand in candidates:
            c = cand.lower()
            if not c or len(c) < 2:
                continue
            if c == label_lower or c == node_id_lower:
                score = 10.0
            elif c in label_lower or label_lower in c:
                score = 6.0 * (len(c) / max(len(label_lower), 1))
            elif c in node_id_lower or node_id_lower in c:
                score = 5.0 * (len(c) / max(len(node_id_lower), 1))
            else:
                continue
            if score > best_score:
                best_score = score
                matched_key = c

        if best_score >= 2.0:
            indexed.append((node, best_score, matched_key))

    indexed.sort(key=lambda x: x[1], reverse=True)
    return [{"node": n, "score": s, "matched": m} for n, s, m in indexed[:max_seeds]]


# ---------------------------------------------------------------------------
# Graph Loading (from Cognee, reused from graph_analytics pattern)
# ---------------------------------------------------------------------------
async def load_graph_nodes_edges(
    dataset_id: Optional[str] = None,
    dataset_name: Optional[str] = None,
    cognee_root: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从 Cognee 加载 nodes + edges（复用 markdown_exporter 的取数逻辑）."""
    if not importlib.util.find_spec("cognee"):
        return [], []

    try:
        from exporters.markdown_exporter import MarkdownExporter  # type: ignore

        exporter = MarkdownExporter(cognee_root=cognee_root)
        result = await exporter._try_get_graph_data(dataset_id=dataset_id, dataset_name=dataset_name)
        if result is None:
            return [], []
        nodes, edges = result
        return nodes or [], edges or []
    except Exception:
        return [], []


def build_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> nx.DiGraph:
    """将 nodes + edges 构建为 NetworkX 有向图."""
    G = nx.DiGraph()

    for node in nodes:
        node_id = str(node.get("id") or node.get("name") or "")
        if not node_id:
            continue
        label = str(node.get("label") or node.get("name") or node_id)
        ntype = str(node.get("type") or node.get("node_type") or "unknown")
        G.add_node(node_id, label=label, node_type=ntype)

    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target:
            continue
        relation = str(edge.get("relation") or edge.get("relationship_name") or "related_to")
        G.add_edge(source, target, relation=relation)

    return G


# ---------------------------------------------------------------------------
# Path Expansion
# ---------------------------------------------------------------------------
def _path_text(G: nx.DiGraph, path: list[str]) -> str:
    """把节点路径渲染成 'A --关系--> B --关系--> C' 文本.

    注意：中间节点只输出一次，不能逐段拼接（否则会出现 "B B" 重复）.
    """
    if len(path) < 2:
        return ""
    parts: list[str] = []
    for i in range(len(path) - 1):
        src = path[i]
        dst = path[i + 1]
        label = G.nodes[src].get("label", src) if src in G.nodes else src
        dlabel = G.nodes[dst].get("label", dst) if dst in G.nodes else dst
        rel = G[src][dst].get("relation", "related_to") if G.has_edge(src, dst) else "related_to"
        if i == 0:
            parts.append(f"{label} --{rel}--> {dlabel}")
        else:
            parts.append(f"--{rel}--> {dlabel}")
    return " ".join(parts)


def expand_from_seed(
    G: nx.DiGraph,
    seed_node_id: str,
    max_hops: int = 2,
    max_per_hop: int = 8,
) -> list[dict[str, Any]]:
    """从种子节点做多跳邻居扩展，返回路径信息列表."""
    if seed_node_id not in G.nodes:
        return []

    results: list[dict[str, Any]] = []
    seed_label = G.nodes[seed_node_id].get("label", seed_node_id)

    # 1 跳
    for neighbor in G.successors(seed_node_id):
        path = [seed_node_id, neighbor]
        rel = G[seed_node_id][neighbor].get("relation", "related_to")
        text = _path_text(G, path)
        results.append({
            "seed": seed_label,
            "node": neighbor,
            "path": path,
            "text": text,
            "score": 1.0,
            "hops": 1,
            "node_type": G.nodes[neighbor].get("node_type", "unknown"),
            "relation": rel,
        })
        if len(results) >= max_per_hop:
            break

    # 2 跳（若 1 跳结果少）
    if max_hops >= 2:
        for neighbor in G.successors(seed_node_id):
            for second in G.successors(neighbor):
                path = [seed_node_id, neighbor, second]
                text = _path_text(G, path)
                results.append({
                    "seed": seed_label,
                    "node": second,
                    "path": path,
                    "text": text,
                    "score": 0.6,
                    "hops": 2,
                    "node_type": G.nodes[second].get("node_type", "unknown"),
                    "relation": G[neighbor][second].get("relation", "related_to"),
                })
                if len(results) >= max_per_hop + 8:
                    break
            if len(results) >= max_per_hop + 8:
                break

    return results


# ---------------------------------------------------------------------------
# Main Retrieval
# ---------------------------------------------------------------------------
async def graph_path_retrieval(
    query: str,
    dataset_id: Optional[str] = None,
    dataset_name: Optional[str] = None,
    limit: int = 10,
    nodes: Optional[list[dict[str, Any]]] = None,
    edges: Optional[list[dict[str, Any]]] = None,
    cognee_root: Optional[str] = None,
) -> list[GraphPathHit]:
    """图谱路径召回主入口.

    允许外部传入 nodes/edges（测试与复用场景），否则从 Cognee 加载.
    """
    if nodes is None or edges is None:
        nodes, edges = await load_graph_nodes_edges(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            cognee_root=cognee_root,
        )

    if not nodes or not edges:
        return []

    G = build_graph(nodes, edges)
    if G.number_of_nodes() == 0:
        return []

    seeds = match_seed_nodes(query, nodes, max_seeds=5)
    if not seeds:
        # 无种子实体时：退化为"图中心度"召回（少量高连接节点作为兜底）
        return []

    hits: list[GraphPathHit] = []
    seen: set[tuple[str, ...]] = set()

    for seed in seeds:
        node_id = str(seed["node"].get("id") or seed["node"].get("name") or "")
        if not node_id or node_id not in G.nodes:
            continue
        for entry in expand_from_seed(G, node_id, max_hops=2):
            key = tuple(entry["path"])
            if key in seen:
                continue
            seen.add(key)
            label = G.nodes[entry["node"]].get("label", entry["node"]) if entry["node"] in G.nodes else entry["node"]
            hits.append(GraphPathHit(
                seed=entry["seed"],
                node=label,
                path=entry["path"],
                text=entry["text"],
                score=round(entry["score"] * seed["score"] / 10.0, 3),
                hops=entry["hops"],
                node_type=entry["node_type"],
                provenance={
                    "route": "graph",
                    "dataset": dataset_name,
                    "seed_matched": seed["matched"],
                    "seed_node": entry["seed"],
                    "node_id": entry["node"],
                    "graph_path": entry["path"],
                    "relation": entry["relation"],
                },
            ))

    hits.sort(key=lambda h: (h.hops, -h.score))
    return hits[:limit]


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------
async def graph_retrieval(
    query: str,
    dataset_name: Optional[str] = None,
    dataset_id: Optional[str] = None,
    limit: int = 10,
    cognee_root: Optional[str] = None,
) -> list[GraphPathHit]:
    """便捷函数：图谱路径召回."""
    return await graph_path_retrieval(
        query=query,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        limit=limit,
        cognee_root=cognee_root,
    )
