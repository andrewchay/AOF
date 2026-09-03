#!/usr/bin/env python3
"""
CSO ontology 迭代自举 POC
用 AOF 多轮从文本迭代完善 ontology:
  种子owl → AOF建图谱 → 提取实例 → 归纳候选类 → [人工review] → 生成新owl → 收敛判断

用法:
  python iterate_ontology.py --rounds 3          # 最多3轮
  python iterate_ontology.py --review-on         # 每轮停等人工确认候选类
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

# 确保能导入同目录模块
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/Users/chaihao/LLM/AOF")
sys.path.insert(0, "/Users/chaihao/LLM/cognee")
logging.disable(logging.WARNING)

from iterate_core import (  # noqa: E402
    extract_instances_from_nodes,
    map_instances_to_classes,
    compute_concept_saturation,
    should_stop,
)
from owl_generator import read_class_names, build_owl  # noqa: E402

BASE = Path(__file__).parent
SEED_OWL = BASE.parent / "data" / "cso_oncology.owl"
OUT_DIR = BASE / "runs"
TEXT = """Study NCT02458638 is an open-label, multicohort, phase II trial of Atezolizumab in patients with advanced solid tumors.
Primary Objective: To evaluate non-progression rate (NPR) at 18 weeks.
Secondary Objectives: To evaluate overall response rate (ORR), duration of response (DOR), progression-free survival (PFS) and overall survival (OS). To evaluate safety and tolerability of Atezolizumab.
Primary Endpoint: Non progression rate (NPR) at 18 weeks is defined as the percentage of patients with complete response, partial response or stable disease.
Secondary Endpoints: Overall response rate (ORR), duration of response (DOR), progression-free survival (PFS), overall survival (OS).
Statistical Methods: A Simon optimal two-stage design will be used to test whether Atezolizumab yields a NPR of clinical interest. The safety population will be the main population for all analyses.
"""


async def run_round(round_n: int, owl_content: str) -> list:
    """执行一轮 AOF 建图谱, 返回 nodes."""
    import cognee
    suffix = time.strftime("%Y%m%d_%H%M%S")
    ds = f"cso_iterate_r{round_n}_{suffix}"
    tmp_owl = OUT_DIR / f"round_{round_n}_ontology.owl"
    tmp_owl.write_text(owl_content, encoding="utf-8")
    os.environ["ONTOLOGY_FILE_PATH"] = str(tmp_owl)
    os.environ["ONTOLOGY_RESOLVER"] = "rdflib"
    os.environ["ONTOLOGY_MATCHING_STRATEGY"] = "fuzzy"

    await cognee.add(TEXT, dataset_name=ds)
    try:
        await cognee.cognify(dataset_name=ds)
    except Exception as e:
        print(f"    (cognify warn: {str(e)[:60]})")

    # 等待索引就绪并重试提取(cognee cognify 后索引可能尚在写入)
    import asyncio
    from bridge.graph_retrieval import load_graph_nodes_edges
    nodes = []
    edges = []
    for attempt in range(4):
        await asyncio.sleep(4)  # 每次等待后再试
        try:
            nodes_try, edges_try = await load_graph_nodes_edges(
                dataset_name=ds, cognee_root="/Users/chaihao/LLM/cognee")
            if nodes_try:
                nodes = nodes_try
                edges = edges_try or []
                print(f"    [提取] attempt{attempt}: {len(nodes)} nodes, {len(edges)} edges")
                break
        except Exception as e:
            print(f"    [提取] attempt{attempt} err: {str(e)[:60]}")
    return nodes, edges


def review_candidates(candidates, review_on: bool):
    """人工 review 候选类。review_on=False 时自动接受(演示)。"""
    if not review_on:
        return candidates
    print("\n=== 人工 review 候选类 (输入 y 接受 / n 拒绝) ===")
    accepted = []
    for c in candidates:
        ans = input(f"  接受类 [{c['class_name']}] (实例{c['instance_count']}个)? [y/n] ")
        if ans.strip().lower() in {"n", "no"}:
            continue
        accepted.append(c)
    return accepted


async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--review-on", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)

    classes_so_far = set(read_class_names(SEED_OWL))
    relations = [("Endpoint", "isEvaluatedBy", "StudyObjective"),
                 ("Endpoint", "hasStatisticalMethod", "StatisticalMethod")]
    prev_classes = set(classes_so_far)
    prev_class_set = set(classes_so_far)

    print("=== CSO Ontology 迭代自举 POC ===")
    print(f"种子类({len(classes_so_far)}): {sorted(classes_so_far)}\n")

    convergence_log = []
    for round_n in range(1, args.rounds + 1):
        print(f"\n{'=' * 60}\n[轮 {round_n}] 构造 onSubmit owl...")
        owl_content = build_owl(SEED_OWL, classes_so_far, relations)

        run_result = await run_round(round_n, owl_content)
        nodes, edges = run_result
        instances = extract_instances_from_nodes(nodes)
        print(f"  提取实例 {len(instances)} 个: {[i[0][:30] for i in instances[:8]]}")

        # 用 is_a 边把实例映射到领域类("实例归纳为类")
        cls_map = map_instances_to_classes(nodes, edges)
        candidates = [{"class_name": c, "instances": sorted(set(v)),
                       "instance_count": len(set(v))} for c, v in cls_map.items()]
        # 过滤掉已经在种子ontology中的类(只归纳"新类")
        known = set(read_class_names(SEED_OWL))
        candidates = [c for c in candidates if c["class_name"] not in known]
        print(f"  归纳候选新类: {[c['class_name'] for c in candidates]}")

        accepted = review_candidates(candidates, args.review_on)
        print(f"  review后接受类: {[c['class_name'] for c in accepted]}")

        new_classes = set(classes_so_far)
        for c in accepted:
            new_classes.add(c["class_name"])

        print(f"  本轮后类别总数: {len(new_classes)}")

        saturation = compute_concept_saturation(set(prev_classes), new_classes)
        # 类集合稳定性: 与本轮相比, 类新增/删除的比例(0=完全稳定)。轮1无前轮, 不算
        stability = 1.0 if round_n < 2 else             (len(set(prev_class_set) ^ set(new_classes)) / max(len(new_classes), 1))
        stop, reason = should_stop(round_n, args.rounds, saturation, stability,
                                   sat_threshold=0.10, stab_threshold=0.10)
        convergence_log.append({"round": round_n, "classes": len(new_classes),
                                "saturation": round(saturation, 3), "stability": round(stability, 3),
                                "stop": stop, "reason": reason})
        print(f"  [收敛] 概念饱和度={saturation:.2f} 类稳定={stability:.2f}")
        print(f"  [收敛] 是否停止: {stop} — {reason}")

        prev_classes = new_classes
        prev_class_set = new_classes
        classes_so_far = new_classes
        if stop:
            break

    summary = {"seed_classes": sorted(read_class_names(SEED_OWL)),
               "final_classes": sorted(classes_so_far),
               "convergence": convergence_log,
               "review_mode": "manual" if args.review_on else "auto-accept"}
    out_file = OUT_DIR / "summary.json"
    out_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== 完成 ===\n最终类别 " + str(len(classes_so_far)) + " 个. 摘要: " + str(out_file))


if __name__ == "__main__":
    asyncio.run(main())
