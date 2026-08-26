"""OntologyLearner：用 AOF/cognee 做 ontology 迭代自举的核心引擎。

编排流程（每个迭代轮）：
    seed owl → cognee.add+cognify 建图谱 → 提取 nodes/edges
        → 用 is_a 边做"实例→类"归纳 → [人工 review 可选] → 合并成新类集合
        → 用收敛判据判断是否提前终止

设计要点：
- 依赖注入：graph_builder 传入可调用的建图函数，便于测试替换。
- 收敛：概念饱和度 + 类稳定 + 概念增长减速(decay) + max_iterations 组合。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tools.ontology_learning import convergence
from tools.ontology_learning.owl_writer import read_class_names, write_owl


@dataclass
class IterationRecord:
    iteration: int
    class_count: int
    added_classes: list[str]
    saturation: float | None
    stability: float | None
    decay: float | None
    stop: bool
    reason: str
    instances_seen: list[str] = field(default_factory=list)


@dataclass
class LearningResult:
    seed_classes: list[str]
    final_classes: list[str]
    iterations: list[IterationRecord]
    converged: bool
    reason: str
    final_owl_path: str | None = None


class OntologyLearner:
    """AOF ontology 迭代自举引擎。"""

    def __init__(
        self,
        graph_builder,  # callable: (owl_text: str, round_n: int, out_dir) -> (nodes, edges)
        sat_threshold: float = 0.10,
        stab_threshold: float = 0.20,
        decay_threshold: float = 0.30,
        base_relations: list[tuple[str, str, str]] | None = None,
    ):
        self.graph_builder = graph_builder
        self.sat_threshold = sat_threshold
        self.stab_threshold = stab_threshold
        self.decay_threshold = decay_threshold
        self.base_relations = base_relations or []

    def learn(
        self,
        seed_owl_path: str,
        max_iterations: int = 5,
        review_fn=None,  # callable(candidates: list[dict]) -> accepted list[dict]
        out_owl_path: str | None = None,
    ) -> LearningResult:
        from tools.ontology_learning.owl_writer import build_owl_text
        from tools.ontology_learning.instance_to_class import (
            extract_instances,
            map_instances_to_classes,
        )

        seed_classes = set(read_class_names(seed_owl_path))
        classes = set(seed_classes)
        prev_classes = set(seed_classes)
        prev_added_count = None
        records: list[IterationRecord] = []

        for i in range(1, max_iterations + 1):
            owl_text = build_owl_text(classes, self.base_relations)
            nodes, edges = self.graph_builder(owl_text, i)
            instances = extract_instances(nodes)
            cls_map = map_instances_to_classes(nodes, edges)

            # 候选新类 = 图谱里出现但不在当前类集合中的领域类
            # 大小写不敏感去重：图谱小写类(如 drug) 若已存在大写种子类(Drug)则归并
            known = set(classes)
            known_lower = {c.lower(): c for c in classes}
            candidates = []
            for c, v in cls_map.items():
                target = known_lower.get(c.lower(), known_lower.get(c.replace('_', ' ').lower(), c))
                canon = target  # 命中已有类则用其规范名；否则用图谱类名
                if canon in known:
                    continue  # 已存在，不重复加法
                candidates.append({"class_name": canon, "instances": sorted(set(v)), "instance_count": len(set(v))})

            # 人工 review（可选；None => 全部自动接受）
            accepted = candidates if review_fn is None else review_fn(candidates)

            added = []
            for c in accepted:
                classes.add(c["class_name"])
                added.append(c["class_name"])

            # 收敛计算
            saturation = convergence.compute_concept_saturation(prev_classes, classes)
            stability = (
                None
                if i < 2
                else convergence.compute_class_stability(
                    {c: None for c in prev_classes}, {c: None for c in classes}
                )
            )
            decay = None
            if prev_added_count is not None:
                decay = convergence.compute_concept_decay(prev_added_count, len(added))

            stop, reason = convergence.should_stop(
                i,
                max_iterations,
                saturation=saturation,
                stability=stability,
                decay=decay,
                sat_threshold=self.sat_threshold,
                stab_threshold=self.stab_threshold,
                decay_threshold=self.decay_threshold,
            )

            records.append(
                IterationRecord(
                    iteration=i,
                    class_count=len(classes),
                    added_classes=added,
                    saturation=saturation,
                    stability=stability,
                    decay=decay,
                    stop=stop,
                    reason=reason,
                    instances_seen=[x[0] for x in instances[:10]],
                )
            )

            prev_classes = set(classes)
            prev_added_count = len(added)

            if stop:
                break

        final_owl = None
        if out_owl_path:
            write_owl(out_owl_path, classes, self.base_relations)
            final_owl = out_owl_path

        return LearningResult(
            seed_classes=sorted(seed_classes),
            final_classes=sorted(classes),
            iterations=records,
            converged=records[-1].stop if records else True,
            reason=records[-1].reason if records else "no iterations",
            final_owl_path=final_owl,
        )


__all__ = ["OntologyLearner", "LearningResult", "IterationRecord"]
