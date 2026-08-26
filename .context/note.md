# 调研笔记

## 2026-08-26 让 AOF 抽提真正走官方链路（根治"绕到 cognee 底层"）

> 触发：用户困惑"在 AOF 空间内抽临床数据却没执行 AOF 代码"。根因是一线脚本直接 `import cognee` + 手动 `ONTOLOGY_*` 环境变量，绕过 AOF runner 层。本次做了 A+B 两阶段根治。

### 核心结论
1. **AOF 官方链路**（已被验证真实触发）：`aof_add`→`run_add_from_spec`（含 document_parser）+ `run_cognify_from_spec`（`_build_ontology_config`→`ontology_adapter.apply_ontology` 注入 `cognify(config=...)`）→`graph_retrieval`。
2. **正确定性**：cognee 的实体 label 抽取主要靠 LLM；ontology 的职责是"定型"（type 归属精确化，如 `population`→`analysispopulation`/`safetypopulation`），不是"能否抽取"。
3. **"走官方链路"验收三层证据**：①脚本只 import `from bridge import ...`、无 `import cognee`；②`run_cognify_from_spec` 内 `config.ontology_config.resolver` 非空（AOF 注入而非环境变量）；③preflight/解析/result log 均有输出。

### 落地产物（examples/cso_validation/）
- `cso_spec.json`：CSO 专用 spec（ontology.file=cso_oncology.owl）
- `data/cso_clinical/NCT02458638_atezolizumab.md`：临床文本文档
- `run_via_aof_chain.py`：**官方链路模板**，零 import cognee；实测 22 nodes/38 edges/19 临床实体（WITH_ONTOLOGY）
- `runs/aof_chain_result.with_ontology.json` & `.no_ontology.json`：结构化结果
- `README_aof_validation.md`：追加"官方链路版"验证记录 + 对"无 ontology"的认知修正

### bridge 增强（杜绝穿透）
- `bridge/ontology_adapter/apply_ontology(spec)`：正式、唯一的 ontology 注入公共 API（CLI 复用，REST/MCP 不消费 cognee 摄取链路故不适用）
- `bridge/cognee_add_runner._check_ontology_adherence(spec)`：校验 spec.ontology.file + 检测 `ONTOLOGY_*` 环境变量穿透并警告（不硬拦，避免破坏 REST/MCP）
- `tests/test_ontology_entry_gate.py`：7 用例

### 经验
- 新增依赖需加载 `.env`（DeepSeek key + ollama bge-m3），`run_via_aof_chain.py` 内部 `load_dotenv`。
- 脚本从 examples 子目录运行 `from bridge import ...` 需把 AOF 根加入 sys.path（`parents[1]`）。
- 对照基线需用**独立 dataset 名**，避免 cognify 幂等复用上次图。

---

## 2026-08-26 AOF 承载「对话消息 + Agent trajectory」能力评估（实证）

> 背景：场景需求 = AOF 不仅处理企业文档，还要处理**企业对话消息** + **Agent 运行 trajectory（含工具调用）**，且轨迹要能用于后续训练。
> 已做最小实证（examples/dialogue_trajectory_poc/），本条目为结论沉淀。

### 实证结论（已实测）
| 环节 | 现状 | 承载能力 |
|------|------|---------|
| 摄取 | `.jsonl/.json` 受支持（REST 无 content 直传，需文件落地 batch）；cognee.add 接受文本/对象 | ✅ 能装 |
| 图谱抽取 | cognee LLM 抽取 + 可选 OWL 本体注入约束 | ✅ 能抽对话/轨迹结构 |
| 决策溯源 | DecisionProvenanceStore：causal_chain/impact/audit_trail/find_precedents/verify_integrity | ✅ 能重建轨迹因果链与合规审计 |
| 时序检索 | AOF temporal 透传 cognee TEMPORAL，LLM 从 query 猜时间，无显式时间范围 | ⚠️ 弱，需数据带时间建模 |
| 按 session/agent 查询 | `session_id`/`agent_id` 字段只存不查，无聚合查询方法 | ⚠️ 需补 |
| **训练导出** | sft/rag_eval/agent_tool 三生成器**均为图谱→模板化合成**，**不吃真实对话/轨迹** | ❌ 缺口 |

### 实证细节
- **Decision 承载轨迹**：把 5 步工具调用轨迹逐步记 decision(parent=上一步)→ `causal_chain` 重建整条 wasInformedBy 因果链；`audit_trail` 证据完整+哈希校验通过；`impact` 首步影响 4 个下游。→ trajectory 的"决策级因果 + 合规面"天然可用。
- **本体注入价值**：注入 conversation.owl 后 cognee 抽出 `conversation/customer/support agent/agentrun/agent/query inventory/purchaseorder` 等 9 个对话/轨迹实体；不注入也能靠 LLM 通用识别出部分，**印证 ontology="约束/规范化"而非"从无到有"**这一关键洞察（与 cso_validation 一致）。

### 若要支持"轨迹用于训练"需补的能力（roadmap 候选）
1. **真实轨迹样本通道进训练导出** ⭐**已实现(2026-08-26)**：原三生成器只读图谱/文档做合成。
   新增 `RawTrajectoryLoader`+`RawTrajectoryGenerator`：真实对话→SFT多轮、真实trajectory→SFT带推理链。
   接入 `/v1/training-data/generate`(generators=["raw"]+raw_sources) 与 exporter。
   详见 docs/architecture/raw-trajectory-training-data.md；单测13个，全量619无回归。
2. **按 session/agent/时间窗口聚合查询**：DecisionRecord 字段存了 session_id/agent_id 但无查询方法；补 list_by_session/list_by_agent/list_since 以支撑"重放一次完整运行"。
3. **工具调用完整入参/返回值结构化存储**：现 evidence 只收 EvidenceRef{id,content_hash}，丢具体入参返回。可在 trajectory 账本中加结构化 ToolCallEntry。
4. **时序检索加显式时间范围参数**：AOF temporal 现透传 cognee（LLM 猜时间），补 from_time/to_time 显式窗，让"查某时段轨迹"可控。
5. **对话/轨迹专用本体模板**（conversation.owl 可作为起点）：作为开箱 template，用户拖入即用。

### 关键文件
- examples/dialogue_trajectory_poc/{record_trajectory.py, build_graph.py, data/*, README.md}
- bridge/decision_provenance.py（现有，Decision 一级）
- bridge/training_data/{sft,rag_eval,agent_tool}.py（现有，合成向）

## 2026-08-17 semantica 与 AOF 关系调研

> 来源：GitHub `semantica-agi/semantica`（8.3k stars, 847 forks, 2343 commits, MIT license）

### Semantica 是什么

**自我定位**："The Open Source Palantir for AI Agents"（开源版 Palantir），官方描述为 **"Graph-Native Infrastructure for Context and Accountable AI Systems"** —— 面向上下文与可问责 AI 系统的图原生基础设施。

本质：一个**确定性的知识图谱 + 决策可溯源地基层**，铺在 LLM、向量库、Agent 框架**下层**（"sits underneath your LLM"），不需要 LLM 也能完成图谱构建、推理与溯源。

**核心能力**
| 能力 | 说明 |
|------|------|
| Context Graphs | 结构化、可查询的"Agent 所知/所决定/所推理"图谱 |
| Decision Intelligence | 每条决策是一等公民，可按先例搜索、因果关联 |
| 本体治理 (Ontology Hub) | SHACL 约束、冲突检测、合规规则、OWL 生成、SKOS 词汇表+可视化编辑器 |
| 全链路审计 | W3C PROV-O 溯源 + 审计轨迹导出 JSON/CSV/RDF |
| 确定性推理 | 前向链、Rete 网络、Datalog、SPARQL，可解释路径 |
| 知识管道 | 多源摄取、实体感知分块、NER/关系/事件抽取、语义去重、保溯源合并 |
| 图谱分析 | 中心性、社区检测、链路预测、最短路径 |
| 多语言图存储 | RDF(Oxigraph/Blazegraph/Jena/RDF4J) + LPG(Neo4j/FalkorDB/AGE/Neptune)，即插即用 |
| 企业连接器 | Databricks(Unity Catalog/Delta)、Snowflake 原生连接器 |
| 可视化 + 集成 | 交互式工作台、Agno/CrewAI/MCP/REST/CLI |

**目标人群**：AI/ML 平台团队、数据平台团队、合规/风控/审计团队、受监管行业（金融/医疗/法律/政府/国防）。

**关键差异化卖点**：从"嵌入向量"到"存意义"（store meaning, not just embeddings），可回答监管者的"why"。

---

### AOF 是什么（对照）

**自我定位**："企业知识的 Agent-Ready 资产化引擎"，一次摄取产出**知识图谱、RAG 向量库、LLM Wiki/OKF、Agent 训练数据**四种形态资产，通过 REST/MCP/Web 消费。

基于 **Cognee** 知识图谱引擎，技术栈：FastAPI + NebulaGraph + Redis + K8s，面向中小企业（P3 定位为产品化）。

**核心能力**
| 能力 | 说明 |
|------|------|
| 多源摄取 | 本地文件/URL/S3/数据库/混合文档 + 增量同步(blake2b) |
| 图谱分析 | PageRank/社区检测/中心性/路径/统计 |
| 14 种搜索 + RAG 混合检索 | 关键词+向量+图谱三路召回、RRF 融合、命中溯源(provenance) |
| 四形态资产导出 | Markdown(GBrain 风格 compiled_truth+timeline)/LLM Wiki/OKF/RAG 向量库/训练数据(SFT/RAG-Eval/Agent-Tool) |
| 企业级 | RBAC、多租户、审计、缓存、限流弹性 |
| Agent 化接口 | MCP Server(12 工具)/OKF 目录服务/RAG 统一检索 API/Web 控制台 |
| Skills | 纯 Markdown Agent 操作手册（Fat Skills, Thin Harness） |

---

### 两者关系分析

**1. 赛道完全重叠，互为直接竞品/参照物**

两者都瞄准"企业知识 → Agent 可用的结构化资产"这一赛道，核心叙事都是：**别再只存嵌入向量，要存有意义、可溯源、可审计的知识图谱**。

| 维度 | Semantica | AOF |
|------|-----------|-----|
| 核心引擎 | 自研确定性图管道 + 决策溯源层 | Cognee + NebulaGraph |
| 知识图谱构建 | ✅ 强（NER/关系/事件抽取+语义去重） | ✅（Cognee cognify） |
| 本体/治理 | ✅ 强（SHACL/OWL/SKOS/冲突检测/视觉编辑器） | ⚠️ 弱（仅 genshin_ontology.owl 样例 + OKF lint） |
| 决策可溯源性 | ✅ 核心卖点（Decision Intelligence + PROV-O） | ⚠️ 部分（只有 RAG 命中溯源 provenance） |
| 可审计合规 | ✅ 核心卖点（面向监管） | ⚠️ 部分（审计日志，但非 PROV-O 级） |
| 确定性推理 | ✅ 强（Rete/Datalog/SPARQL） | ❌ 无独立推理引擎（借图谱分析） |
| 多图存储 | ✅ Polyglot（RDF+LPG） | ⚠️ Cognee/Nebula 两个 |
| 训练数据生成 | ❌ 无 | ✅ 独有（SFT/RAG-Eval/Agent-Tool） |
| Web 可视化 | ✅ 交互工作台 | ✅ Vue3 + vis-network |
| MCP | ✅ | ✅（12 工具） |
| 开箱 LLM Wiki/OKF 导出 | ❌ 无 | ✅ 独有 |
| 定位人群 | 受监管大型企业（Palantir 对标） | 中小企业 |
| 开源协议 | MIT | 未标（README 写 Your License Here） |

**2. AOF 的差异化优势（semantica 没有的）**
- **Agent 训练数据生成**（SFT / RAG-Eval / Agent-Tool 三种类型）
- **LLM Wiki / OKF 渐进式披露知识包** + index.md/log.md，专为 Agent 低成本消费设计
- **RAG 三路混合检索 + 命中溯源**（图谱增强检索）
- 轻量、中小企业友好、用成熟引擎（Cognee）而非自研

**3. AOF 相对弱项（semantica 强的地方，值得借鉴）**
- **决策级溯源（PROV-O）**：semantica 把"记录决策 → 追溯因果链 → 找先例 → 评估影响"做成一等公民；AOF 目前只有 RAG 级别的 provenance。**这是 AOF 最值得补的能力**，直接命中"可信 AI/监管"叙事。
- **本体治理成熟度**：SHELAC/OWL/SKOS + 冲突检测 + 可视化本体编辑器。AOF 只有轻量 OKF lint。
- **确定性推理引擎**（Rete/Datalog/SPARQL）：AOF 没有，依赖图谱分析。
- **企业数据连接器**（Databricks/Snowflake）：AOF 只有通用 DB schema 提取。

**4. 是否引用/依赖关系**：两者**无代码引用关系**。AOF 技术栈基于 Cognee、NebulaGraph、FastAPI；semantica 完全自研。AOF 的灵感参考是 GBrain（Fat Skills, Thin Harness）和 LLM Wiki/OKF 趋势，README 未提及 semantica。

**5. 对 AOF 的战略意义**
- semantica 验证了"知识图谱 + 可溯源 + 面向 Agent"赛道**有真需求和热度**（8.3k stars），且定位偏大型受监管企业。
- AOF 的目标客户是中小企业，方向错开，但**"决策可追溯/审计"是行业共同叙事**，AOF 若想提升可信度与差异化，应在 roadmap 中加入**决策级溯源（类 PROV-O）** 能力，作为与纯 RAG 工具（Dify/RAGFlow）区隔的重要抓手。
- 建议关注 semantica 的 MCP / 本体治理 / 决策溯源模块，作为 AOF 后续功能设计与选型的参考（不必依赖，可吸收思想）。

### 建议行动
- 将"决策级溯源与可审计"列入 AOF roadmap 差异化能力（待用户确认是否需要更新 roadmap.md / todo.md）。
- 可选：将本调研的核心结论沉淀到 CLAUDE.md（AOF 的定位边界与对标）。
