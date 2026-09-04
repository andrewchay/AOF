"""
CSO ontology 迭代自举 - 核心逻辑(复用)
- 实例→候选类归纳
- 收敛判断(概念饱和度 + 类结构稳定 + MaxIter)
本文件独立于 cognee, 可单测。
"""
from __future__ import annotations

# 需要从图谱提取的实体类型中过滤掉的cognee内部类型
# cognee 图谱中的节点类型(过滤掉派生的内部类型)
SKIP_NODE_TYPES = {"DocumentChunk", "TextDocument", "TextSummary", "EntityType"}


def extract_instances_from_nodes(nodes: list[dict]) -> list[tuple[str, str]]:
    """
    从AOF图谱nodes提取 (实例名, 实体类型) 列表。
    关键：cognee 的实体名在 `name` 字段(不是 `label`)，类型在 `type`。
    只保留 Entity 实例(过滤 DocumentChunk/TextDocument/TextSummary/EntityType)。
    """
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


def cluster_instances_to_classes(instances: list[tuple[str, str]]) -> list[dict]:
    """按实体类型归纳候选类。返回 [{class_name, instances, instance_count}]"""
    by_type: dict[str, list[str]] = {}
    for label, typ in instances:
        by_type.setdefault(typ, []).append(label)
    out = []
    for typ, labels in by_type.items():
        out.append({
            "class_name": typ,
            "instances": sorted(set(labels)),
            "instance_count": len(set(labels)),
        })
    return out


def compute_concept_saturation(old_classes: set, new_classes: set) -> float:
    """概念饱和度 = 新增类 / 新类总数。越小越饱和。"""
    added = new_classes - old_classes
    if not new_classes:
        return 1.0
    return len(added) / len(new_classes)


def compute_class_stability(prev_sub_of: dict, cur_sub_of: dict) -> float:
    """类结构稳定 = (增删的子类关系数) / 当前子类关系数。越小越稳定。"""
    prev = set((c, p) for c, p in prev_sub_of.items())
    cur = set((c, p) for c, p in cur_sub_of.items())
    if not cur:
        return 0.0
    changed = len(cur - prev) + len(prev - cur)
    return changed / len(cur)


def should_stop(iter_n: int, max_iter: int, saturation: float, stability: float,
                sat_threshold: float = 0.10, stab_threshold: float = 0.20) -> tuple[bool, str]:
    """递归终点：任一判据触发即停。"""
    if sat_threshold is not None and saturation <= sat_threshold:
        return True, f"概念饱和度达标 saturation={saturation:.2f} <= {sat_threshold}"
    if stab_threshold is not None and stability <= stab_threshold:
        return True, f"类结构稳定 stability={stability:.2f} <= {stab_threshold}"
    if iter_n >= max_iter:
        return True, f"达到最大迭代次数 max_iter={max_iter}"
    return False, f"继续迭代 (iter={iter_n}, sat={saturation:.2f}, stab={stability:.2f})"


if __name__ == "__main__":
    # 自测
    demo_nodes = [
        {"label": "overall survival (os)", "type": "Endpoint"},
        {"label": "progression-free survival (pfs)", "type": "Endpoint"},
        {"label": "simon optimal two-stage design", "type": "StatisticalMethod"},
        {"label": "atezolizumab", "type": "Drug"},
        {"label": "text_token123", "type": "DocumentChunk"},  # 应被过滤
        {"label": "date", "type": "TextSummary"},            # 应被过滤
    ]
    inst = extract_instances_from_nodes(demo_nodes)
    print("提取实例:", inst)
    cand = cluster_instances_to_classes(inst)
    print("候选类:", [(c["class_name"], c["instance_count"]) for c in cand])


def map_instances_to_classes(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    """
    用 `is_a` 边把 Entity 实例映射到其领域类(EntityType)。
    返回 {领域类: [实例名,...]} —— 这是"实例归纳为类"的核心。
    即使某个类型只有一个实例, 也保留(用于人工 review 判断是否够格成类)。
    """
    # id -> (name, typename)
    id2name = {}
    id2etype = {}
    for n in nodes:
        nid = str(n.get("id", ""))
        nm = str(n.get("name") or n.get("label") or "").strip()
        typ = str(n.get("type") or "")
        if not nm or nm.startswith("text_"):
            continue
        if typ == "Entity":
            id2name[nid] = nm
        elif typ == "EntityType":
            id2etype[nid] = nm

    # 遍历 is_a 类边: Entity --is_a--> EntityType
    cls_map: dict[str, list[str]] = {}
    for e in edges:
        rel = str(e.get("relation", ""))
        if rel not in ("is_a", "is_a_instance_of", "instance_of"):
            continue
        src = str(e.get("source", ""))
        tgt = str(e.get("target", ""))
        inst = id2name.get(src)
        cls = id2etype.get(tgt)
        if inst and cls:
            cls_map.setdefault(cls, []).append(inst)
    return cls_map
