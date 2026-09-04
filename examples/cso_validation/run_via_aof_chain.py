#!/usr/bin/env python3
"""
CSO 临床抽提 —— 走 AOF 官方链路（模板）

本脚本是"正确做法"的示范：**不直接 import cognee、不手动设置 ONTOLOGY_* 环境变量**，
一切交给 AOF 的 bridge 抽象层（spec 驱动）。

链路（与 aof_add.py 完全一致）：
    ensure_preflight_for_add    →  预检（cognee 可导入 / 数据路径 / LLM key）
    run_add_from_spec           →  摄取（含 document_parser 解析层）
    ensure_preflight_for_cognify→  预检（ontology.file 存在 / LLM key）
    run_cognify_from_spec       →  cogny（其中 _build_ontology_config 把 spec.ontology
                                    打包成 cognee.cognify(config=...) 的 ontology_config 注入）
    bridge.graph_retrieval      →  读图谱 nodes/edges，验证识别出的实体

用法:
    python examples/cso_validation/run_via_aof_chain.py            # 用 cso_spec.json（含 ontology）
    python examples/cso_validation/run_via_aof_chain.py --no-onto  # 对照：去掉 ontology 注入

环境:
    需 LLM_API_KEY（cognify 实体抽取）。脚本通过 load_dotenv 加载 AOF_ROOT/.env。
    若 .env 已由外部 source，则不会重复覆盖。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AOF_ROOT = HERE.parents[1]
# 把 AOF 根加入 sys.path，使 `from bridge import ...` 可解析（唯一导入 AOF 的路径；不 import cognee）
if str(AOF_ROOT) not in sys.path:
    sys.path.insert(0, str(AOF_ROOT))

# 可选加载项目 .env（若已由 shell 注入则跳过覆盖）
try:
    from dotenv import load_dotenv  # type: ignore
    PROJECT_ROOT = str(HERE.parents[1])
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=False)
except Exception:  # pragma: no cover - dotenv 不可用时依赖外部注入
    pass

# ---- 只 import AOF 的 bridge 抽象，绝不 import cognee ----
from bridge.cognee_add_runner import run_add_from_spec  # noqa: E402
from bridge.cognee_runner import run_cognify_from_spec  # noqa: E402
from bridge.errors import PreflightError  # noqa: E402
from bridge.preflight import ensure_preflight_for_add, ensure_preflight_for_cognify  # noqa: E402

# CSO 领域关键词（用于在抽取结果中区分"通用实体" vs "临床实体"）
CSO_KEYWORDS = [
    "objective", "endpoint", "simon", "safety", "atezo", "npr",
    "survival", "response", "tumor", "pfs", "dor", "disease",
    "method", "population", "clinicaltrial", "drug", "study",
    "atezolizumab", "progression", "overall", "phase",
]


def _is_cso(*labels: str) -> bool:
    return any(k in lab.lower() for lab in labels if lab for k in CSO_KEYWORDS)


def _load_spec(path: Path, drop_ontology: bool) -> dict:
    with path.open("r", encoding="utf-8") as f:
        spec = json.load(f)
    if drop_ontology:
        spec["ontology"] = {}  # 对照：去掉 ontology，验证"通用抽取"基线
    return spec


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-onto", action="store_true", help="对照：不注入 ontology")
    ap.add_argument("--spec", default=os.path.join(os.path.dirname(__file__), "cso_spec.json"))
    args = ap.parse_args()

    spec = _load_spec(Path(args.spec), drop_ontology=args.no_onto)
    if args.no_onto:
        # 用独立 dataset 名，避免与带 ontology 的运行复用同一图（保证基线干净）
        spec["dataset"] = spec["dataset"] + "_noonto"
    dataset = spec["dataset"]
    mark = "WITH_ONTOLOGY" if not args.no_onto else "NO_ONTOLOGY"
    print(f"\n===== [{mark}] 走 AOF 官方链路：dataset={dataset} =====")
    print(f"       ontology.file = {json.dumps((spec.get('ontology') or {}).get('file'))}")

    # 数据路径：data/cso_clinical/NCT02458638_atezolizumab.md
    data_file = Path(spec["knowledge_repo"]) / "NCT02458638_atezolizumab.md"
    print(f"       数据文件 = {data_file}  (exists={data_file.exists()})")

    # 1) Add 预检 + 摄取（走 document_parser → run_add_from_spec）
    print("\n[1] preflight_add + run_add_from_spec ...")
    ensure_preflight_for_add(spec, data_paths=[str(data_file)], require_api_key=True)
    add_res = await run_add_from_spec(spec, data=os.path.join(spec["knowledge_repo"], "NCT02458638_atezolizumab.md"))
    print(f"      add 完成: {type(add_res).__name__}")

    # 2) Cognify 预检 + 图谱构建（ontology 在此注入）
    print("\n[2] preflight_cognify + run_cognify_from_spec ...")
    ensure_preflight_for_cognify(spec, require_api_key=True)
    # 关键验证点：确认 spec.ontology 被映射进 cognify 的 config（AOF 层注入，而非环境变量）
    try:
        from bridge.cognee_runner import _build_ontology_config  # noqa: E402
        cfg = _build_ontology_config(spec)
        if cfg and cfg.get("ontology_config", {}).get("ontology_resolver") is not None:
            print("      ✅ ontology 已由 AOF bridge 注入 cognee config(ontology_config.resolver)")
        elif args.no_onto:
            print("      (对照模式：无 ontology 注入，cognee 用默认 RDF 解析)")
        else:
            print("      ⚠️ 未检测到 ontology_config 注入，需检查 spec.ontology")
    except Exception as e:
        print(f"      (ontology 内省跳过: {type(e).__name__}: {e})")

    cognify_res = await run_cognify_from_spec(spec)
    print(f"      cognify 完成: {type(cognify_res).__name__}")

    # 3) 读图谱，验证识别出的实体
    print("\n[3] load_graph_nodes_edges ...")
    from bridge.graph_retrieval import load_graph_nodes_edges  # noqa: E402
    cognee_root = (spec.get("cognee") or {}).get("root", "/Users/chaihao/LLM/cognee")
    nodes, edges = await load_graph_nodes_edges(dataset_name=dataset, cognee_root=str(cognee_root))

    labels = [str(n.get("label", n.get("name", ""))) for n in nodes if n.get("label") or n.get("name")]
    cso = [lab for lab in labels if _is_cso(lab)]
    generic = [lab for lab in labels if not _is_cso(lab)]
    print(f"      识别出 {len(labels)} 个实体, {len(edges)} 条边")
    print(f"      CSO/临床实体({len(cso)}): {cso if cso else '(无)'}")
    print(f"      通用实体({len(generic)}): {generic if generic else '(无)'}")

    # 4) 结论（对比基线）
    print("\n[4] 结论")
    if args.no_onto:
        print("      无 ontology → 倾向通用命名实体（对照基线）")
    else:
        hit = any(_is_cso(lab) for lab in labels)
        print(f"      注入 CSO ontology → 临床实体识别: {'✅ 成功' if hit else '⚠️ 未命中临床实体，需排查'}")

    # 落盘结构化结果（evidence，供审计）
    result = {
        "mode": mark,
        "dataset": dataset,
        "ontology_file": (spec.get("ontology") or {}).get("file"),
        "nodes": len(labels),
        "edges": len(edges),
        "cso_entities": cso,
        "generic_entities": generic,
    }
    out = Path(os.path.dirname(__file__)) / "runs" / f"aof_chain_result.{mark.lower()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n      result 已落盘: {out}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except PreflightError as e:
        print(f"\n❌ Preflight 失败: {e}\n（提示：若报缺少 LLM_API_KEY，请先 `set -a; source .env; set +a` 或确认 .env 已加载）")
        sys.exit(1)
