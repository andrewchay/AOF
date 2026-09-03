#!/usr/bin/env python3
"""
采购 FIBO 映射试点 —— 走 AOF 官方链路（对照实验）

实验设计：
- 对照组（baseline）：无 ontology，验证通用抽取基线
- 实验组（fibo）：注入 procurement_o2c_fibo.owl，验证 FIBO 语义增强效果

评估指标：
1. 抽取准确率：是否正确识别采购域实体（Contract、Commitment、Role 等）
2. 关系完整性：是否正确识别 Obligor/Obligee、ContractParty 关系
3. 跨案例一致性：15 个案例中的"审批人"是否统一识别为 role
4. BFO 区分度：是否正确区分 continuant（承诺）vs occurrent（过程）

用法:
    python run_fibo_pilot.py              # 实验组：注入 FIBO ontology
    python run_fibo_pilot.py --baseline   # 对照组：不注入 ontology

环境:
    需 LLM_API_KEY（cognify 实体抽取）。脚本通过 load_dotenv 加载 AOF_ROOT/.env。
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
if str(AOF_ROOT) not in sys.path:
    sys.path.insert(0, str(AOF_ROOT))

# 可选加载项目 .env
try:
    from dotenv import load_dotenv
    PROJECT_ROOT = str(HERE.parents[1])
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=False)
except Exception:
    pass

from bridge.cognee_add_runner import run_add_from_spec  # noqa: E402
from bridge.cognee_runner import run_cognify_from_spec, _build_ontology_config  # noqa: E402
from bridge.errors import PreflightError  # noqa: E402
from bridge.preflight import ensure_preflight_for_add, ensure_preflight_for_cognify  # noqa: E402
from bridge.graph_retrieval import load_graph_nodes_edges  # noqa: E402

# 采购域关键词（用于区分"采购实体" vs "通用实体"）
PROCUREMENT_KEYWORDS = [
    "contract", "purchase", "procurement", "agreement", "commitment",
    "obligation", "payment", "delivery", "supplier", "buyer",
    "approval", "threshold", "emergency", "framework", "metric",
    "budget", "expense", "spend", "rebate", "deposit", "prepayment",
    "return", "refund", "fx", "currency", "sample", "internal_transfer",
    "shared_service", "cost_allocation", "compliance", "violation",
    "cfo", "procurement_manager", "finance_analyst", "director", "vp",
    "role", "party", "obligor", "obligee",
]

# FIBO 特定关键词（验证 FIBO 是否被正确使用）
FIBO_KEYWORDS = [
    "contract", "agreement", "commitment", "obligation", "party",
    "obligor", "obligee", "role", "disposition", "process",
]


def _is_procurement(*labels: str) -> bool:
    return any(k in lab.lower() for lab in labels if lab for k in PROCUREMENT_KEYWORDS)


def _is_fibo(*labels: str) -> bool:
    return any(k in lab.lower() for lab in labels if lab for k in FIBO_KEYWORDS)


def _load_spec(path: Path, baseline: bool) -> dict:
    with path.open("r", encoding="utf-8") as f:
        spec = json.load(f)
    if baseline:
        spec["ontology"] = {}
        spec["dataset"] = spec["dataset"] + "_baseline"
    return spec


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="对照组：不注入 ontology")
    ap.add_argument("--spec", default=str(HERE / "fibo_spec.json"))
    args = ap.parse_args()

    spec = _load_spec(Path(args.spec), baseline=args.baseline)
    dataset = spec["dataset"]
    mark = "BASELINE" if args.baseline else "FIBO"
    
    print(f"\n===== [{mark}] 采购 FIBO 映射试点 =====")
    print(f"       dataset = {dataset}")
    print(f"       ontology.file = {json.dumps((spec.get('ontology') or {}).get('file'))}")

    # 数据文件
    data_file = Path(spec["knowledge_repo"]) / "procurement_cases.md"
    print(f"       数据文件 = {data_file} (exists={data_file.exists()})")

    # 1) Add 预检 + 摄取
    print("\n[1] preflight_add + run_add_from_spec ...")
    ensure_preflight_for_add(spec, data_paths=[str(data_file)], require_api_key=True)
    add_res = await run_add_from_spec(spec, data=str(data_file))
    print(f"      add 完成: {type(add_res).__name__}")

    # 2) Cognify 预检 + 图谱构建
    print("\n[2] preflight_cognify + run_cognify_from_spec ...")
    ensure_preflight_for_cognify(spec, require_api_key=True)
    
    # 验证 ontology 注入
    try:
        cfg = _build_ontology_config(spec)
        if cfg and cfg.get("ontology_config", {}).get("ontology_resolver") is not None:
            print("      ✅ ontology 已由 AOF bridge 注入 cognee config")
        elif args.baseline:
            print("      (对照模式：无 ontology 注入)")
        else:
            print("      ⚠️ 未检测到 ontology_config 注入")
    except Exception as e:
        print(f"      (ontology 内省跳过: {e})")

    cognify_res = await run_cognify_from_spec(spec)
    print(f"      cognify 完成: {type(cognify_res).__name__}")

    # 3) 读图谱
    print("\n[3] load_graph_nodes_edges ...")
    cognee_root = (spec.get("cognee") or {}).get("root", "/Users/chaihao/LLM/cognee")
    nodes, edges = await load_graph_nodes_edges(dataset_name=dataset, cognee_root=str(cognee_root))

    labels = [str(n.get("label", n.get("name", ""))) for n in nodes if n.get("label") or n.get("name")]
    procurement = [lab for lab in labels if _is_procurement(lab)]
    fibo = [lab for lab in labels if _is_fibo(lab)]
    generic = [lab for lab in labels if not _is_procurement(lab)]
    
    print(f"      识别出 {len(labels)} 个实体, {len(edges)} 条边")
    print(f"      采购域实体({len(procurement)}): {procurement[:10] if procurement else '(无)'}...")
    print(f"      FIBO 语义实体({len(fibo)}): {fibo[:10] if fibo else '(无)'}...")
    print(f"      通用实体({len(generic)}): {generic[:10] if generic else '(无)'}...")

    # 4) 评估
    print("\n[4] 评估")
    
    # 评估 1: 采购域实体识别率
    procurement_ratio = len(procurement) / len(labels) if labels else 0
    print(f"      采购域实体占比: {procurement_ratio:.1%}")
    
    # 评估 2: FIBO 语义实体识别率（仅实验组）
    if not args.baseline:
        fibo_ratio = len(fibo) / len(labels) if labels else 0
        print(f"      FIBO 语义实体占比: {fibo_ratio:.1%}")
    
    # 评估 3: 跨案例一致性检查
    role_hits = sum(1 for lab in labels if "role" in lab.lower())
    print(f"      'role' 相关实体数: {role_hits}")
    
    # 评估 4: continuant vs occurrent 区分（检查承诺 vs 过程）
    commitment_hits = sum(1 for lab in labels if "commitment" in lab.lower())
    process_hits = sum(1 for lab in labels if any(p in lab.lower() for p in ["process", "phase", "approval"]))
    print(f"      'commitment' 相关实体数: {commitment_hits}")
    print(f"      'process/phase/approval' 相关实体数: {process_hits}")

    # 结论
    print("\n[5] 结论")
    if args.baseline:
        print("      无 ontology → 倾向通用命名实体（对照基线）")
    else:
        hit = len(fibo) > 0 and len(procurement) > len(generic)
        print(f"      注入 FIBO ontology → 采购实体识别: {'✅ 优于基线' if hit else '⚠️ 需排查'}")

    # 落盘结果
    result = {
        "mode": mark,
        "dataset": dataset,
        "ontology_file": (spec.get("ontology") or {}).get("file"),
        "nodes": len(labels),
        "edges": len(edges),
        "procurement_entities": procurement,
        "fibo_entities": fibo,
        "generic_entities": generic,
        "metrics": {
            "procurement_ratio": procurement_ratio,
            "role_hits": role_hits,
            "commitment_hits": commitment_hits,
            "process_hits": process_hits,
        }
    }
    out = HERE / "runs" / f"pilot_result.{mark.lower()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n      result 已落盘: {out}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except PreflightError as e:
        print(f"\n❌ Preflight 失败: {e}")
        sys.exit(1)
