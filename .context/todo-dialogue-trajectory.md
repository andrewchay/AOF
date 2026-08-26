# 任务追踪 — AOF 处理「对话消息 + Agent trajectory」能力实证

> 开始: 2026-08-26
> 状态: ✅ 实证完成
> 目标：实证验证 AOF 对三类输入（企业文档 / 企业对话消息 / Agent 运行轨迹）的处理能力，
> 摸清通/卡点，沉淀增强方案到工作区级 note.md。

## 阶段 A：实证探索
- [x] 读取 AOF core（cognee_add / run_add_from_spec / ontology 接入点 / 训练数据导出）
- [x] 确认「决策溯源(decision_provenance)」现有能力边界（decision 账本 + MCP/REST 完整接口）
- [x] training_data 导出是否可吃 Agent 轨迹（SFT / Agent-Tool 生成器输入形态 → 均无真实轨迹通道）

## 阶段 B：实证跑通最小链路
- [x] 造最小对话消息样例（jsonl，多轮多角色）
- [x] 造最小 Agent trajectory 样例（含 tool call 序列 + 决策）
- [x] Part A：Decision 溯源层实证 —— causal_chain 重建 5 步轨迹因果链、impact、audit、integrity ✅
- [x] Part B：cognee + conversation.owl 抽取对话/轨迹结构（9 个 dialogue 实体）✅

## 阶段 C：结论与方案
- [x] 输出《AOF 对话消息 + Agent trajectory 处理能力评估与增强方案》→ 工作区级 .context/note.md
- [x] 明确哪些靠本体建模、哪些需新代码（训练导出吃轨迹 / session聚合 / 时间窗查询）
- [x] 评估 trajectory 对训练数据导出的可用性（现状合成生成，需新增真实轨迹样本通道）

## 产物
- examples/dialogue_trajectory_poc/（OWL 本体 + 样例 + 两个实证脚本 + README）
- 工作区级 .context/note.md 新增条目
