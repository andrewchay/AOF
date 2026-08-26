#!/usr/bin/env python3
"""
Part B: cognee 摄取对话文本 + 注入 conversation.owl 图谱抽取
=================================================================
实证:cognee 的 LLM 抽取能否在注入对话本体后识别对话/轨迹结构
（Conversation / ConversationTurn / Message / AgentRun / AgentStep / ToolCall）。

对比:
    注入 conversation.owl → 看是否抽取到对话结构类实体
    （对照 cso_validation 的模板方法）

用法:
    cd /Users/chaihao/LLM/AOF && .venv/bin/python examples/dialogue_trajectory_poc/build_graph.py [--with-ontology]
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/chaihao/LLM/AOF")
sys.path.insert(0, "/Users/chaihao/LLM/cognee")
logging.disable(logging.WARNING)

OWL = str(Path(__file__).parent / "data" / "conversation.owl")

# 一段含对话轮次 + Agent 工具调用轨迹的真实文本（供抽取）
TEXT = """In conversation conv-2026-0801, customer asked: my new laptop won't boot, black screen.
The support agent replied: sorry for the trouble, is it new? is the power light on?
Customer said: yes bought last month, power light on but screen black.
The agent decided to create service ticket TKT-88421 for free repair under warranty.
In another session conv-2026-0802 customer asked about logistics delay for project delivery.
The agent replied logistics runs normally with 2-3 day average.

In agent run run-9f3c2a, the inventory-planner agent decided to check warehouse stock.
It called tool query_inventory with region east_china and sku NB-100.
The tool returned stock level 12 percent below threshold 20 percent.
The agent then called tool calc_reorder_qty and got recommendation to reorder 800 units.
Finally the agent decided to create purchase order PO-2026-0901 approved.
"""

DIALOGUE_KEYWORDS = [
    "conversation", "turn", "message", "agentrun", "agentstep",
    "toolcall", "customer", "agent", "inventory", "query_inventory",
    "purchaseorder", "service",
]


def _is_dialogue_entity(label: str) -> bool:
    return any(k in label.lower() for k in DIALOGUE_KEYWORDS)


async def main(with_ontology: bool):
    suffix = time.strftime('%Y%m%d_%H%M%S')
    import cognee
    dataset_name = f"dialog_{"with" if with_ontology else "without"}_ontology_{suffix}"

    if with_ontology:
        os.environ["ONTOLOGY_FILE_PATH"] = OWL
        os.environ["ONTOLOGY_RESOLVER"] = "rdflib"
        os.environ["ONTOLOGY_MATCHING_STRATEGY"] = "fuzzy"
        print(f"[ontology] 注入 {OWL}")
    else:
        os.environ["ONTOLOGY_FILE_PATH"] = ""
        print("[ontology] 不注入 ontology")

    print(f"[1] add+cognify dataset={dataset_name}")
    await cognee.add(TEXT, dataset_name=dataset_name)
    try:
        await cognee.cognify(dataset_name=dataset_name)
    except Exception as e:
        print(f"    (cognify: {type(e).__name__}: {str(e)[:120]})")

    from bridge.graph_retrieval import load_graph_nodes_edges
    nodes, edges = await load_graph_nodes_edges(
        dataset_name=dataset_name, cognee_root="/Users/chaihao/LLM/cognee"
    )
    labels = [str(n.get("label", n.get("name", ""))) for n in nodes if n.get("label") or n.get("name")]
    generic = [l for l in labels if not _is_dialogue_entity(l)]
    dialogue = [l for l in labels if _is_dialogue_entity(l)]

    print(f"[2] 识别出 {len(labels)} 个实体")
    print(f"    对话/轨迹相关实体: {dialogue if dialogue else '(无)'}")
    print(f"    其他实体: {generic if generic else '(无)'}")

    print("\n[3] 结论")
    if dialogue:
        print("    注入对话本体 → 抽取到了对话/轨迹结构相关实体 ✅")
    else:
        print("    未抽取到对话结构实体 ⚠️（对话作为纯文本被通用化）")


if __name__ == "__main__":
    with_onto = "--with-ontology" in sys.argv
    asyncio.run(main(with_ontology=with_onto))
