"""Ontology learning 收敛判据。

提供一个"ontology 迭代自举"的递归终点判定：概念饱和度 + 类结构稳定 + MaxIter。

这些函数无外部依赖(不依赖 cognee)，可独立单测。核心逻辑已在
examples/cso_validation/iterative_poc 中经过真实 cognee 验证（seed 7 -> 13 -> 18 类，
概念饱和度 0.46 -> 0.28 递减）。
"""
from __future__ import annotations


def compute_concept_saturation(previous_classes: set, current_classes: set) -> float:
    """概念饱和度 = 新增类 / 当前总类数。越小表示越接近"已无新概念"。

    - 1.0 == 全部都是新概念（初始轮）
    - 0.0 == 无任何新增（完全饱和）
    """
    new_total = len(current_classes)
    if new_total == 0:
        return 1.0
    added = len(current_classes - previous_classes)
    return added / new_total


def compute_class_stability(
    previous_hierarchy: dict, current_hierarchy: dict
) -> float:
    """类结构稳定 = 增删的子类关系数 / 当前子类关系总数。越小越稳定。

    参数为 {类名: 父类名} 的子类映射；用 `(class, parent)` 对集合做对称差。
    - 0.0 == 结构完全不变(稳定)
    """
    prev = set((c, p) for c, p in previous_hierarchy.items())
    cur = set((c, p) for c, p in current_hierarchy.items())
    if not cur:
        return 0.0
    changed = len(cur - prev) + len(prev - cur)
    return changed / len(cur)


def compute_concept_decay(previous_added: int, current_added: int) -> float:
    """概念增长速率(decay)：本轮新增 / 上轮新增。用于判断"新增收敛"。

    这个判据来自对现有 AOF ontology_factory 的增强：当新一轮归纳出的新概念
    远少于上一轮时（decay < threshold），说明 ontology 趋于充盈，可提前终止。
    - 1.0 == 本轮新增与上轮持平
    - 0.0 == 本轮无新增
    """
    if previous_added <= 0:
        return 1.0
    return current_added / max(previous_added, 1)


def should_stop(
    iteration: int,
    max_iterations: int,
    saturation: float | None = None,
    stability: float | None = None,
    decay: float | None = None,
    sat_threshold: float = 0.10,
    stab_threshold: float = 0.20,
    decay_threshold: float = 0.30,
) -> tuple[bool, str]:
    """递归终点：任一活跃判据触发即停止。所有判据可独立启用/关闭(传 None 关闭)。

    Returns:
        (stop, reason)
    """
    if decay is not None and decay <= decay_threshold:
        return True, f"概念增长减速 decay={decay:.2f} <= {decay_threshold}"
    if saturation is not None and saturation <= sat_threshold:
        return True, f"概念饱和度达标 saturation={saturation:.2f} <= {sat_threshold}"
    if stability is not None and stability <= stab_threshold:
        return True, f"类结构稳定 stability={stability:.2f} <= {stab_threshold}"
    if iteration >= max_iterations:
        return True, f"达到最大迭代次数 max_iterations={max_iterations}"
    return False, f"继续迭代 (iter={iteration}, sat={saturation}, stab={stability}, decay={decay})"


__all__ = [
    "compute_concept_saturation",
    "compute_class_stability",
    "compute_concept_decay",
    "should_stop",
]
