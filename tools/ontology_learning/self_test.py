"""验证 OntologyLearner 引擎（不依赖真实 cognee，用模拟 graph_builder）。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.ontology_learning import OntologyLearner, write_owl


def make_seed_owl(tmpdir: str) -> str:
    """构造最小种子 ontology（只含 2 个类）。"""
    seed = Path(tmpdir) / "seed.owl"
    write_owl(str(seed), {"Endpoint", "Drug"})
    return str(seed)


def make_mock_graph_builder():
    """模拟 graph_builder：每轮返回不同数量的 is_a 边（新增概念递减以触发收敛）。"""
    # 每轮提前定义好实体 → 类映射；从第3轮起不再新增，来触发 decay/饱和度收敛
    rounds = [
        # round 1：新增2类
        {"endpoint": ["npr", "orr"], "drug": ["atezolizumab"]},
        # round 2：再新增1类
        {"endpoint": ["npr", "orr"], "drug": ["atezolizumab"], "statisticalmethod": ["simon"]},
        # round 3（+）：无新增
        {"endpoint": ["npr", "orr"], "drug": ["atezolizumab"], "statisticalmethod": ["simon"]},
    ]

    def _iter(cls_map, iteration):
        nodes, edges = [], []
        id = 0
        for cls, insts in cls_map.items():
            etype_id = f"etype_{cls}_{iteration}"
            nodes.append({"id": etype_id, "name": cls, "type": "EntityType"})
            for inst in insts:
                e_id = f"e_{id}_{iteration}"
                nodes.append({"id": e_id, "name": inst, "type": "Entity"})
                edges.append({"relation": "is_a", "source": e_id, "target": etype_id})
                id += 1
        return nodes, edges

    def build(owl_text, iteration):
        # iteration 从 1 开始，映射到 rounds（第4轮后复用第3个）
        idx = min(iteration - 1, len(rounds) - 1)
        return _iter(rounds[idx], iteration)

    return build


def main() -> int:
    tmpdir = tempfile.mkdtemp()
    seed_owl = make_seed_owl(tmpdir)

    builder = make_mock_graph_builder()
    learner = OntologyLearner(graph_builder=builder, decay_threshold=0.3, sat_threshold=0.1)

    result = learner.learn(seed_owl, max_iterations=5, out_owl_path=str(Path(tmpdir) / "learned.owl"))

    print("=== OntologyLearner 引擎验证 ===")
    print(f"种子类: {result.seed_classes}")
    print(f"最终类: {result.final_classes}")
    print(f"收敛: {result.converged} — {result.reason}")
    print(f"最终 OWL: {result.final_owl_path}")
    print("\n迭代记录:")
    for it in result.iterations:
        print(
            f"  iter{it.iteration}: 类数={it.class_count} 新增={it.added_classes} "
            f"sat={it.saturation} stab={it.stability} decay={it.decay} → stop={it.stop}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
