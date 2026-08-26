"""实例 → 类 归纳：从 AOF/cognee 图谱 nodes/edges 中把实体实例映射到领域类。

关键实现（已在 CSO 场景真实验证）：
- cognee 的实体名字在 `name` 字段（不是 `label`，`label` 恒为 None）
- 具体实例节点 `type == "Entity"`，领域类节点 `type == "EntityType"`
- 通过 `Entity --is_a--> EntityType` 边把实例归到其领域类
  （例：`atezolizumab --is_a--> drug`，`non-progression rate (npr) --is_a--> endpoint`）
"""
from __future__ import annotations

# cognee 图谱中的派生节点类型（过滤掉）
SKIP_NODE_TYPES = {"DocumentChunk", "TextDocument", "TextSummary", "EntityType", "EdgeType"}

# is_a 类关系名
IS_A_RELATIONS = {"is_a", "is_a_instance_of", "instance_of", "subclass_of"}


def extract_instances(nodes: list[dict]) -> list[tuple[str, str]]:
    """提取 (实例名, 类型) 列表。只保留 Entity 实例，过滤内部派生节点。"""
    instances: list[tuple[str, str]] = []
    for n in nodes:
        name = str(n.get("name") or n.get("label") or "").strip()
        typ = str(n.get("type") or n.get("node_type") or "Entity")
        if not name or name.startswith("text_") or name.isdigit():
            continue
        if typ in SKIP_NODE_TYPES:
            continue
        instances.append((name, typ))
    return instances


def map_instances_to_classes(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    """
    用 `is_a` 边把 Entity 实例映射到其领域类(EntityType)。
    返回 {领域类: [实例名, ...]} —— 这是"实例归纳为类"的核心。
    """
    id2instance = {}
    id2entity_type = {}
    for n in nodes:
        nid = str(n.get("id", ""))
        nm = str(n.get("name") or n.get("label") or "").strip()
        typ = str(n.get("type") or "")
        if not nm or nm.startswith("text_"):
            continue
        if typ == "Entity":
            id2instance[nid] = nm
        elif typ == "EntityType":
            id2entity_type[nid] = nm

    cls_map: dict[str, list[str]] = {}
    for e in edges:
        rel = str(e.get("relation", ""))
        if rel not in IS_A_RELATIONS:
            continue
        inst = id2instance.get(str(e.get("source", "")))
        cls = id2entity_type.get(str(e.get("target", "")))
        if inst and cls:
            cls_map.setdefault(cls, []).append(inst)
    return cls_map


def cluster_by_type(instances: list[tuple[str, str]]) -> list[dict]:
    """备用策略：按节点 type 字段聚类候选类。返回 [{class_name, instances, instance_count}]。"""
    by_type: dict[str, list[str]] = {}
    for name, typ in instances:
        by_type.setdefault(typ, []).append(name)
    return [
        {"class_name": typ, "instances": sorted(set(labels)), "instance_count": len(set(labels))}
        for typ, labels in by_type.items()
    ]


__all__ = ["extract_instances", "map_instances_to_classes", "cluster_by_type"]
