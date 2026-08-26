# AOF 对话消息 + Agent Trajectory 处理能力实证

> 日期: 2026-08-26
> 目的: 验证 AOF 对三类输入的承载能力——企业文档 / 企业对话消息 / Agent 运行轨迹（含工具调用），
> 为「轨迹用于后续训练」的场景提供实证依据。

## 环境
- AOF venv (Python 3.13)，cognee 0.5.5-local，本地 lancedb + cognee_db（无需 Nebula）
- Ollama（embedding bge-m3）+ DeepSeek（cognify 实体抽取 LLM）

## 样例数据
| 文件 | 内容 |
|------|------|
| `data/sample_conversation.jsonl` | 企业对话消息（2 个 session，客服多轮） |
| `data/agent_trajectory.json` | Agent 库存补货轨迹（5 步，含 3 次工具调用） |
| `data/conversation.owl` | 对话/轨迹本体（Conversation→Turn→Message, AgentRun→Step→ToolCall） |

## 实证一：Decision 溯源层承载 Agent trajectory ✅
`record_trajectory.py` —— 把 5 步轨迹逐步记入 DecisionProvenanceStore：
- **causal_chain** 重建整条因果链（reason→tool_result→reason→tool_result→decide，4 条 wasInformedBy 边）✅
- **impact** 下游影响：首步影响 4 个后续决策 ✅
- **audit_trail** PROV-O 审计轨迹：证据完整=True、哈希链校验=True ✅
- **verify_integrity** 全链防篡改：valid=True ✅

> 结论：DecisionProvenanceStore 天然能承载 Agent 轨迹的「因果执行链 + 合规审计面」，
> 只需把每步记成 decision(parent=上一步) 即可。`precedents` 还能做"同类历史轨迹找先例"。

## 实证二：cognee 抽取对话/轨迹结构 ✅
`build_graph.py`——cognee.add+cognify 摄取对话轨迹文本：
- **注入 conversation.owl**：识别出 dialogue 相关实体 9 个
  `conversation, customer, support agent, service, agentrun, inventory planner agent, agent, query inventory, purchaseorder` ✅
- **不注入本体**：仍识别出 `conversation, agentrun, inventory-planner, agent, query_inventory, purchaseorder, customer in conv-2026-0801` ✅
- 印证 AOF/CSO 关键洞察：**cognee LLM 抽取本身有较强通用语义能力，ontology 的价值是"约束/规范化"实体类型与关系**

## 关键发现（对照子代理代码审查）
| 环节 | 现状 | 是否能承载对话/轨迹 |
|------|------|---------------------|
| 摄取 | `.jsonl`/`.json` 受支持；REST 不走 content 直传，需文件落地 batch | ✅ 能装 |
| 图谱抽取 | cognee LLM + 可选本体注入 | ✅ 能抽对话/轨迹结构 |
| 决策溯源 | DecisionProvenanceStore（causal_chain/impact/audit/precedents）| ✅ 能重建轨迹因果链 |
| 时序检索 | AOF temporal 透传 cognee TEMPORAL，LLM 从 query 猜时间，无显式时间范围 | ⚠️ 弱，需数据带时间建模 |
| 按 session/agent 查询 | 字段只存不查，无聚合查询方法 | ⚠️ 需补 |
| **训练导出** | 三生成器（sft/rag_eval/agent_tool）**均为图谱→模板化合成**，**不吃真实对话/轨迹** | ❌ 缺口 |

## 能力边界总结
- **能**：对话消息和轨迹文本整体入库、图谱化（含本体约束规范化）、决策级因果溯源、合规审计
- **不能（需增强）**：真实对话/轨迹作为"原始样本"直接进训练导出管道；按 session/agent/时间窗口的原生聚合查询；
  工具调用完整入参/返回值的结构化存储（现 evidence 只收哈希引用）

## 运行
```bash
cd /Users/chaihao/LLM/AOF
.venv/bin/python examples/dialogue_trajectory_poc/record_trajectory.py   # Part A（决策溯源，零网络）
.venv/bin/python examples/dialogue_trajectory_poc/build_graph.py --with-ontology  # Part B（图谱抽取，需 LLM）
```
