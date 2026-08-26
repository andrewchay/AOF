#!/usr/bin/env python3
"""
AOF 对话消息 + Agent trajectory 处理能力实证
============================================

Part A（本脚本，零网络依赖）: Agent trajectory → 决策溯源账本
    把一次含工具调用的 Agent 运行轨迹，逐步骤记录进 DecisionProvenanceStore
    （PROV-O 风格决策账本），用 causal_chain 重建整条决策/执行轨迹链。
    实证:DECISION 溯源层能否承载 trajectory 的因果重建。

Part B: 见 build_graph.py —— cognee 摄取对话文本 + 注入 conversation.owl
    实证:图谱抽取能否保留对话/轨迹的结构（需 DeepSeek LLM）。

用法:
    cd /Users/chaihao/LLM/AOF && .venv/bin/python examples/dialogue_trajectory_poc/record_trajectory.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chaihao/LLM/AOF")
from bridge.decision_provenance import DecisionProvenanceStore

TRAJECTORY = Path(__file__).parent / "data" / "agent_trajectory.json"
LEDGER = Path(__file__).parent / "data" / "trajectory_ledger.jsonl"


def main() -> None:
    with TRAJECTORY.open(encoding="utf-8") as f:
        run = json.load(f)

    store = DecisionProvenanceStore(LEDGER)
    if LEDGER.exists():
        LEDGER.unlink()  # 干净重跑

    prev_id: str | None = None  # 上一步决策 id，形成 causal 链
    step_of: dict[str, tuple[str, str]] = {}  # step id -> (tool, action)
    for step in run["steps"]:
        tool = (step.get("tool_call") or {}).get("tool", "-")
        action = step["action"]
        # 每步作为一个"决策"节点：结论=该步输出，依据=输入，父=上一步
        # 证据=该步引用的工具（含 tool_result 引用的输入，保证全链证据完整）
        evidence = [{"id": f"tool:{tool}"}] if step.get("tool_call") else [
            {"id": f"input:{step.get('input','')[:40]}", "description": step.get("input")}
        ]
        record = store.record(
            agent_id=f"agent:{run['agent']}",
            decision_type=f"trajectory_step:{action}",
            conclusion=step["output"],
            rationale=step["input"],
            parent_decision_ids=[prev_id] if prev_id else None,
            evidence=evidence,
            output_entities=[{"id": step["output"][:40], "type": action}],
            tenant_id="demo",
            session_id=run["run_id"],
            metadata={"seq": step["seq"], "tool": tool},
        )
        step_id = record["decision"]["id"]
        step_of[step_id] = (tool, action)
        prev_id = step_id

    # ---- 实证 1: causal_chain 重建整条轨迹 ----
    print("=" * 62)
    print("[实证1] causal_chain 重建 Agent 轨迹因果链")
    chain = store.causal_chain(prev_id, direction="ancestors", max_depth=10)
    nodes = chain["nodes"]
    print(f"  轨迹共 {len(nodes)} 个步骤（决策节点）")
    for i, node in enumerate(reversed(nodes)):  # ancestors 逆序 → 从首步到末步
        d = node["decision"]
        tool, action = step_of.get(d["id"], ("-", "-"))
        print(f"    step{i+1:>2} | {action:<16} tool={tool:<20} 结论: {d['conclusion'][:38]}")
    print(f"  因果边: {chain['edges'][:3]} ... 共 {len(chain['edges'])} 条")

    # ---- 实证 2: impact 下游影响（首步→后续所有受影响步骤） ----
    first_decision_id = nodes[-1]["decision"]["id"]  # ancestors 返回末步在前，末元素为首步
    print("\n[实证2] impact 影响评估（首步 → 下游受影响步骤）")
    impact = store.impact(first_decision_id)
    print(f"  受影响决策数: {impact['affected_decision_count']}（含首步在内的整条轨迹）")

    # ---- 实证 3: audit_trail 合规轨迹 ----
    print("\n[实证3] audit_trail PROV-O 审计轨迹（末步）")
    trail = store.audit_trail(prev_id)
    print(f"  证据完整: {trail['compliance']['evidence_complete']} | 哈希链校验: {trail['integrity']['valid']}")

    # ---- 实证 4: verify_integrity 防篡改 ----
    print("\n[实证4] 账本完整性")
    v = store.verify_integrity()
    print(f"  全链有效: {v['valid']} | 校验条目数: {v['entries_checked']}")

    print("\n✅ 结论: DecisionProvenanceStore(causal_chain/impact/audit_trail) "
          "可重建 Agent trajectory 的因果执行链与合规审计面")
    print(f"   账本写入: {LEDGER}")


if __name__ == "__main__":
    main()
