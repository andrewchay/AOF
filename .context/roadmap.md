# AOF 产品发展路线图

> 日期: 2026-08-07
> 目标: 面向中小企业，从"知识图谱平台"转型为"Agent-Ready 知识资产化引擎"，逐级搭建 Agentic OS 底座。

---

## 总体思路

围绕**四大资产形态**（知识图谱 / RAG 向量库 / LLM Wiki-OKF / Agent 训练数据）分阶段补齐，配合**产品化**（界面、模板、交付）与**Agent 化**（MCP/OKF 接口）两条主线推进。

---

## Phase 1 — 资产化内核（1-2 个月）🎯 当前阶段

> 目标：把"四形态产出"打磨成可用的内核，优先 LLM Wiki / OKF 差异化能力。

### 交付物
- [x] 知识图谱构建、增量同步、图谱分析（已有）
- [x] Markdown 导出（compiled_truth + timeline，含 Kuzu Cypher）（已有）
- [x] 训练数据生成 SFT / RAG-Eval / Agent-Tool（已有，未提交待 review）
- [ ] **LLM Wiki 导出器**：把图谱/文档编译成相互链接的 Markdown Wiki（Obsidian 兼容）
- [ ] **OKF 规范输出**：原子化 Markdown + YAML Frontmatter（type/title/description/tags）+ `index.md`（渐进式披露）+ `log.md`（审计）
- [ ] **Lint 工具**：知识库结构体检（断链、重复、口径冲突检测）
- [ ] **RAG 向量库深化**：混合检索、多路召回、命中溯源

### 成功标准
- 能从原神 KG 等真实数据集一键导出 OKF 兼容知识包
- LLM Wiki 导出具备 index.md 渐进式披露，Agent 可低成本消费
- Lint 能自动发现断链与口径冲突

---

## Phase 2 — Agent 化与接口 (2-4 个月)

> 目标：让 AOF 产出的知识能被 Agent 直接消费，成为 Agentic OS 的知识底座。

### 交付物
- [ ] **OKF 知识目录服务**：动态目录 + 渐进式披露 API
- [ ] **MCP Server**：把 AOF 知识查询暴露为 MCP 工具，支持 Dify/LangGraph/Claude 等
- [ ] **Agent 消费闭环**：Agent-Tool 训练数据反哺评测，AOF 知识实际被 Agent 调用并反馈
- [ ] **联邦查询**（可选）：跨图谱/跨知识包统一查询入口
- [ ] SDK / REST API 完善（现有 SDK 基础上补 OKF/RAG 接口）

### 成功标准
- 一个标准 Agent（如 Dify 工作流）通过 MCP 直接检索 AOF 知识库并完成任务
- 知识接口符合 OKF 宽容消费模型

---

## Phase 3 — 产品化与轻量版 (4-6 个月)

> 目标：让非技术用户也能用，形成可交付的商业产品，覆盖中小微企业。

### 交付物
- [ ] **低代码 Web 界面**：上传即建图，可视化图谱/Wiki 预览，Agent 对话测试台
- [ ] **行业模板**：制造 / 金融 / 政务 / 通用行业知识包模板（对标数商云/达观）
- [ ] **轻量版（对齐轻量版 Agentic OS）**：单机部署、本地向量库（Chroma/sqlite-vec）、成本透明/限额
- [ ] **私有化部署包**：Docker/K8s 一键部署、离线审核
- [ ] 观测看板（贵/慢/准 三维）

### 成功标准
- 非技术用户 30 分钟内完成"上传 → 建知识库 → 跑通 Agent 问答"
- 轻量版可独立交付，覆盖 1-5 人团队

---

## Phase 4 — Agentic OS 底座 (6-9 个月)

> 目标：对齐两份附件中的 Agentic OS 目标架构，形成完整底座。

### 交付物
- [ ] **多 Agent 协同**：接入 A2A 协议/编排，知识作为共享记忆层
- [ ] **组织记忆**：决策历史、最佳实践沉淀为长期记忆
- [ ] **口径治理 Agent**：自动检测并仲裁跨 Agent 指标/术语分歧
- [ ] **知识自进化**：FAQ 自迭代、未答问题自动入待补池
- [ ] 与 MCP/A2A 生态标准对齐，输出 Agent Card

### 成功标准
- 至少 1 个端到端跨 Agent 工作流依托 AOF 知识底座跑通
- AOF 成为企业 Agentic OS 的"组织记忆 + 知识引擎"

---

## 资源与依赖

- 现状：单机原型已能跑通 Genshin KG 全链路 → 是 P1 验证的基础。
- 依赖技能：skills/SKILL_TRAINING_DATA.md（已有）、SKILL_INGEST.md 等。
- 外部生态：MCP SDK、Obsidian（LLM Wiki 预览）、Chroma/sqlite-vec。

## 里程碑总览

| 里程碑 | 时间 | 标志 |
|--------|------|------|
| 内核成型 | M2 | 四形态产出 + OKF + Lint 可用 |
| Agent 可消费 | M4 | MCP 打通，真实 Agent 调用成功 |
| 产品可售卖 | M6 | 轻量版 + Web 界面 + 模板交付 |
| OS 底座 | M9 | 多 Agent 协同 + 组织记忆跑通 |
