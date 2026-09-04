#!/usr/bin/env python3
"""
CSO 场景反例：用 AOF/cognee 独立建图谱，验证领域 ontology 的价值。

⚠️ 遗留快速验证脚本（已不推荐用于正式抽提）
----------------------------------------------
本脚本为早期"能力验证"保留：它直接 ``import cognee`` + 手动设置 ``ONTOLOGY_*``
环境变量，会**绕过 AOF 的封装层**（preflight / spec / document_parser / ontology_adapter）。

正式抽提请改用官方链路：``run_via_aof_chain.py``（spec 驱动、ontology 走
``bridge/ontology_adapter.apply_ontology`` 注入、结果落盘）。

用法:
    python examples/cso_validation/build_graph.py [--with-ontology]

对比:
    1. 无 ontology 时，cognee 抽取"通用命名实体"(person/company/location)
    2. 注入 CSO ontology 后，cognee 能识别"临床研究实体"(Endpoint/Drug/Disease/Method)

这也是一个可复用的 AOF 能力验证模板：
    任何领域，只要定义了 domain.owl，就能验证 AOF 从通用图谱 → 领域图谱的跃迁。
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chaihao/LLM/AOF")
sys.path.insert(0, "/Users/chaihao/LLM/cognee")
logging.disable(logging.WARNING)

CSO_OWL = str(Path(__file__).parent / "data" / "cso_oncology.owl")

# 真实临床研究文本(NCT02458638 Atezolizumab)
TEXT = """Study NCT02458638 is an open-label, multicohort, phase II trial of Atezolizumab in patients with advanced solid tumors.
Primary Objective: To evaluate non-progression rate (NPR) at 18 weeks.
Secondary Objectives: To evaluate overall response rate (ORR), duration of response (DOR), progression-free survival (PFS) and overall survival (OS). To evaluate safety and tolerability of Atezolizumab.
Primary Endpoint: Non progression rate (NPR) at 18 weeks is defined as the percentage of patients with complete response, partial response or stable disease.
Secondary Endpoints: Overall response rate (ORR), duration of response (DOR), progression-free survival (PFS), overall survival (OS).
Statistical Methods: A Simon optimal two-stage design will be used to test whether Atezolizumab yields a NPR of clinical interest. The safety population will be the main population for all analyses.
"""

CSO_KEYWORDS = [
    "objective", "endpoint", "simon", "safety", "atezo", "npr",
    "survival", "response", "tumor", "pfs", "dor", "disease",
    "method", "population", "clinicaltrial", "drug",
]


def _is_cso_entity(label: str) -> bool:
    """判断实体 label 是否属于 CSO 领域概念"""
    return any(k in label.lower() for k in CSO_KEYWORDS)


async def main(with_ontology: bool):
    import time
    suffix = time.strftime('%Y%m%d_%H%M%S')
    import cognee
    dataset_name = f"cso_demo_{"with" if with_ontology else "without"}_ontology_{suffix}"

    # 注入 ontology(仅当 with_ontology=True 时)
    if with_ontology:
        os.environ["ONTOLOGY_FILE_PATH"] = CSO_OWL
        os.environ["ONTOLOGY_RESOLVER"] = "rdflib"
        os.environ["ONTOLOGY_MATCHING_STRATEGY"] = "fuzzy"
        print(f"[ontology] 注入 {CSO_OWL}")
    else:
        os.environ["ONTOLOGY_FILE_PATH"] = ""
        print("[ontology] 不注入 ontology（cognee 默认通用抽取）")

    # 1. 摄取
    print(f"[1] add+cognify dataset={dataset_name}")
    await cognee.add(TEXT, dataset_name=dataset_name)
    # 显式构建图谱实体(cognee.add 只收录文本,不建实体关系)
    try:
        await cognee.cognify(dataset_name=dataset_name)
    except Exception as e:
        print(f"    (cognify: {type(e).__name__}: {str(e)[:80]})")

    # 2. 提取图谱 nodes，看识别出的实体
    from bridge.graph_retrieval import load_graph_nodes_edges
    nodes, edges = await load_graph_nodes_edges(
        dataset_name=dataset_name, cognee_root="/Users/chaihao/LLM/cognee"
    )

    labels = [str(n.get("label", n.get("name", ""))) for n in nodes if n.get("label") or n.get("name")]
    generic = [label for label in labels if not _is_cso_entity(label)]
    domain = [label for label in labels if _is_cso_entity(label)]

    print(f"[2] 识别出 {len(labels)} 个实体")
    print(f"    通用实体: {generic if generic else '(无)'}")
    print(f"    CSO/领域实体: {domain if domain else '(无)'}")

    # 3. 结论
    print("\n[3] 结论")
    if with_ontology:
        print("    注入 CSO ontology → 正确识别临床研究实体 ✅")
    else:
        print("    无 ontology → 只识别通用命名实体（person/company/location）⚠️")


if __name__ == "__main__":
    with_onto = "--with-ontology" in sys.argv
    asyncio.run(main(with_ontology=with_onto))
