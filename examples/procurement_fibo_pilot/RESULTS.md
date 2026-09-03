# 采购 FIBO 映射试点 — 对照实验结果

> 完成时间: 2026-09-03
> 数据: `data/procurement_cases.md`（15 个历史口径争议案例）
> 链路: AOF 官方链路（preflight → run_add_from_spec → run_cognify_from_spec → load_graph_nodes_edges）
> LLM: deepseek/deepseek-chat（tool_choice 结构化抽取）| Embedding: ollama/bge-m3（1024 维）

---

## 实验配置

| 组 | spec | ontology | dataset |
|---|---|---|---|
| 对照组 | `baseline_spec.json` | 无 | `procurement_fibo_pilot_baseline` |
| 实验组 | `fibo_spec.json` | `procurement_o2c_fibo.owl`（matching_cutoff=0.85） | `procurement_fibo_pilot` |

两组串行执行（cognee/Kùzu 为单写者锁，禁止并行）。

## 结果对比

| 指标 | 对照组 | 实验组（首轮） | 实验组（修复后） |
|---|---|---|---|
| 节点数 | 71 | 69 | 54 |
| 边数 | 269 | 271 | 138 |
| 采购域实体占比（脚本启发式） | 7.0% | 2.9% | 13.5% |
| `ontology_valid=true` 节点（权威） | — | **0** | **11** |
| role 类实体 | 0 | 1 | 4 |
| process/approval 类实体 | 0 | 0 | 1 |

`ontology_valid=true` 节点清单（实验组修复后，直接读 dataset Kuzu 文件统计）：

- EntityType: `approverrole`、`decisionmakerrole`、`emergencypurchasecontract`、`financeanalystrole`、`internalauditrole`、`singlesourceprocurement`
- Entity: `cfo`、`内审`、`单一来源采购`、`财务分析师`、`采购经理`

## 两轮实验组差异（核心发现）

首轮实验组结果为阴性（0 命中），根因不是 FIBO 建模错误，而是**匹配键语言不匹配**：

1. cognee `RDFLibOntologyResolver` 按 **URI 本地名**建 lookup（`_uri_to_key`），不读 `rdfs:label`；
2. 实体名只与 `individuals` 类目做匹配，类型名与 `classes` 类目匹配；首轮 lookup 为 27 classes / **0 individuals**；
3. LLM 从中文文档抽出的是中文实体名（差旅费/审批阈值/紧急采购），与英文 URI 本地名做 difflib 匹配，cutoff=0.85 下 **0/69 命中**（降到 0.5 也只有 3 个，跨语言字符串相似度恒低）。

修复方式（`procurement_o2c_fibo.owl`）：

- 补齐试点 README「新增本体类」表中已文档化但未合入的 16 个案例扩展类（Prepayment、ReturnProcess、SampleProcurement 等），classes 27 → 46；
- 新增 **25 个中文 URI 本地名的 NamedIndividual**（如 `&aof;紧急采购` → `rdf:type &aof;EmergencyProcurement`），作为跨语言匹配桥；LLM 抽出的中文名精确命中 individuals，并经 is_a 链把 FIBO/BFO 英文类名带入图谱；
- 修复 DOCTYPE 缺失的 `&xsd;` 实体声明（首轮 cognify 前即抛 `SAXParseException: undefined entity`）。

修复后验证：25 individuals 进入 lookup，试点目标术语 10/10 命中；图谱中 4/4 本体已覆盖且 LLM 抽出的中文实体全部 `ontology_valid=true`。未命中的 19 个中文实体为系统/部门类概念（erp应付账款系统、it部门等），本就不属于采购域本体，不命中是正确行为。

## 结论

- **机制成立**：ontology 注入后 `ontology_valid` 从 0 → 11，且命中集中在本体覆盖的领域概念上；对照组为 0，说明命中由本体注入贡献而非 LLM 自身。
- **实验预期部分达成**：EXPERIMENT.md 预估「采购域实体占比 30%→60%+」基于英文测试文本；真实中文语料下启发式占比为 7%→13.5%，但权威指标（ontology_valid）从 0→11 更能说明语义增强生效。脚本关键词启发式对中文命名（如「采购退货流程」含空格分词差异）低估覆盖面。
- **跨案例一致性初步可见**：审批人被统一归入 `approverrole`/`decisionmakerrole` 类型节点，15 个案例的角色语义收敛到 FIBO ContractParty 骨架。
- **commitment 类未命中**：LLM 本轮未抽出与「承诺口径」individual 完全一致的字符串（抽取粒度波动），后续可在 spec 提示词或案例文本中强化口径术语，或扩充 individuals 同义变体。

## 运行环境注意（复现必读）

1. **串行执行**：Kùzu 单写者锁，多进程并行摄取会报 `Could not set lock on file`；
2. **embedding 配置**：必须走 `.env` 的 `ollama/bge-m3`；若启动 shell 残留 SOP 时代的 `EMBEDDING_MODEL=deepseek/deepseek-embedding`，`load_dotenv(override=False)` 不会覆盖，DeepSeek 对该模型名返回 422（SOP 第 8 节已警告该组合不可用）；
3. **LLM 模型名**：`deepseek-chat`（别名，支持 tool_choice）；显式 `deepseek-v4-flash`/`deepseek-v4-pro` 为 thinking 模式，拒绝 tool_choice，cognify 结构化抽取会失败；
4. **结果统计口径**：`load_graph_nodes_edges` 的「方式 1」Kuzu 全局查询不带 dataset 过滤，会混入历史 dataset 节点；权威统计应直接读 dataset 自己的 `.pkl` 图谱文件并解析 `properties` JSON 中的 `ontology_valid`。

## 产物清单

| 文件 | 说明 |
|---|---|
| `runs/pilot_result.baseline.json` | 对照组结果 |
| `runs/pilot_result.fibo.json` | 实验组（修复后）结果 |
| `runs/baseline_run.log` / `runs/fibo_run.log` | 运行日志 |
| `procurement_o2c_fibo.owl` | 增强后本体（46 classes + 25 individuals，296 triples） |
| 本文件 | 结果与结论 |
