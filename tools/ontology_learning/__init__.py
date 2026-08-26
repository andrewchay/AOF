"""AOF Ontology Learning 模块：用 AOF/cognee 做 ontology 迭代自举。

能力：
- 从领域文本用 AOF 建图谱
- 通过 is_a 边做"实例 → 类"归纳
- 概念饱和度 + 类结构稳定 + 概念增长减速 三判据收敛
- 可选人工 review
"""
from tools.ontology_learning.convergence import (
    compute_concept_saturation,
    compute_class_stability,
    compute_concept_decay,
    should_stop,
)
from tools.ontology_learning.instance_to_class import (
    extract_instances,
    map_instances_to_classes,
    cluster_by_type,
)
from tools.ontology_learning.owl_writer import read_class_names, build_owl_text, write_owl
from tools.ontology_learning.engine import OntologyLearner, LearningResult, IterationRecord

__all__ = [
    # convergence
    "compute_concept_saturation",
    "compute_class_stability",
    "compute_concept_decay",
    "should_stop",
    # instance -> class
    "extract_instances",
    "map_instances_to_classes",
    "cluster_by_type",
    # owl
    "read_class_names",
    "build_owl_text",
    "write_owl",
    # engine
    "OntologyLearner",
    "LearningResult",
    "IterationRecord",
]

__version__ = "0.1.0"
