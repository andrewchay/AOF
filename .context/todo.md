# 任务追踪 — AOF 阶段 A→B→C

> 开始: 2026-08-26
> 目标: A(走官方链路重做CSO抽提) + B(杜绝穿透+正式ontology注入API) + C(第一业务闭环：指标口径治理)
> 计划: .context/plan/aof-execute-via-official-chain.md（A/B）；阶段 C 依据 docs/AOF架构评审-OntoEffect视角-2026-08-30.md P1 建议

## 阶段 A：真·AOF 链路重做 CSO 抽提
- [x] A0 前置确认：cognee.cognify 需 LLM_API_KEY；.env 里已配 DeepSeek(ollama bge-m3 嵌入)，运行需先加载 .env
- [x] A1 写 cso_spec.json + 临床文本落地 data/cso_clinical/
- [x] A2 写 run_via_aof_chain.py（零 import cognee，走 preflight→run_add→run_cognify→read_graph）
- [x] A3 跑通有/无 ontology 两组：WITH_ONTOLOGY 22nodes/38edges/19临床实体，assert ontology_config 由 bridge 注入 ✅；对照 noonto 19/36/17；result json 已落盘
- [x] A4 追加官方链路验证记录 → README_aof_validation.md（含对"无 ontology 也能抽"的精确认知修正）

## 阶段 B：机制上杜绝穿透 + 正式 ontology 注入 API
- [x] B1 增强 run_add_from_spec：_check_ontology_adherence 校验 spec.ontology + 环境变量穿透警告
- [x] B2 抽取 bridge/ontology_adapter.apply_ontology(spec) 公共 API，cognee_runner._build_ontology_config 复用（兼容既有测试）
- [x] B3 评估：REST/MCP 不消费 cognee 摄取链路（REST 为语义查询层、MCP 为本体治理），无可接入点 → 定为不适用；摄取统一走 CLI/runner + run_via_aof_chain.py 模板
- [x] B4 文档收口：build_graph.py 加"遗留快速验证"banner；run_via_aof_chain.py 作为官方模板
- [x] B5 单测：tests/test_ontology_entry_gate.py 7 用例（adherence gate + apply_ontology）全过，既有 7 用例无回归

## QA（阶段 A/B）
- [x] ruff 干净
- [x] 全量 606 tests 通过（新增 7 个 ontology entry gate 测试，0 回归）

## 阶段 C：第一业务闭环 —— 指标口径治理（procurement_caliber_governance）

> 依据：docs/AOF架构评审-OntoEffect视角-2026-08-30.md 发现 3 + P1 建议
> 闭环链路：语义中间层(metric_catalog) → Lint 体检(口径冲突) → 回归样例库(变更门禁) → 专家反馈回喂 → 新版本发布(releases)
> 定位：从"造本体的 demo"（原神 KG / CSO 临床）升级为"用本体跑业务闭环"的 demo，验证 AOF 是"被验证的 L3"

### C1 建模与资源定义（之十一 SOP：对象/关系/动作建模）
- [x] C1.1 命名第一闭环本体：采购支出控制域「指标口径治理」本体（semantic_resources.yaml，575 行）
- [x] C1.2 对象与动作建模：objects_and_functions.yaml（含 RBAC 角色：finance_analyst / procurement_manager / internal_audit / cfo / release_bot 等）

### C2 审批流闭环（提议 → 审核 → 批准 → 发布）
- [x] C2.1 SemanticGovernanceService 全流程序通：create_draft → validate → approve → compile → publish
- [x] C2.2 权限矩阵 + 职责分离（SoD）：提议者不可自审、审核者不可自批（TestPolicyEnforcement 覆盖）
- [x] C2.3 争议升级路径：request_changes / waive（cfo 为最终口径裁定人）

### C3 决策溯源（之十四：决策—行动—结果数据）
- [x] C3.1 DecisionProvenanceStore 接入闭环：每次口径裁定落 append-only hash 链账本（TestDecisionProvenance 覆盖）

### C4 历史案例回放验证（之十一：10-30 个真实案例）
- [x] C4.1 15 个历史口径争议案例落盘：data/historical_cases.yaml（PRC-2025-001 ~ 015，含 dispute/context/resolution/outcome）
- [x] C4.2 pytest 回放验证全过：TestHistoricalCaseReplay + 全部 11 用例通过（2026-08-31 实测）
- [ ] C4.3 修复 replay_cases.py 独立运行路径 bug（tests/resources → ../resources，pytest 路径正常、CLI 直跑报错）

### C5 度量指标定义（P1-4：闭环可度量）
- [ ] C5.1 定义闭环北极星指标：口径争议处理时长 / SQL 口径错误率 / 回归门禁拦截次数 / 专家反馈回喂周期
- [ ] C5.2 把 Lint 体检（口径冲突检测）接入变更门禁：每次发布前显式对账（对应评审 P3-11 漂移对账门禁）

### C6 真实场景验证（之十一：4-8 周可演示、可试运行）
- [ ] C6.1 选定一个真实试点（内部或种子客户），用真实口径争议替换/扩充合成案例
- [ ] C6.2 产出闭环演示叙事：从一次口径争议发起到新 release 发布的完整 walkthrough
- [ ] C6.3 验证后回填评审文档：L3「机制齐全」→「闭环已验」

---

## 里程碑：CTO + CSO v0.3 融合本体完成 (2026-09-02)

> 详见: examples/cso_validation/CTO_CSO_V03_FUSED_BASE.md

### Phase 1-4 全部完成 ✅

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 复合终点 + 缩写映射 | ✅ 完成 |
| Phase 2 | 语义对齐 (safety/tolerability/Simon) | ✅ 完成 |
| Phase 3 | 设计特征 (open_label/multicohort) | ✅ 完成 |
| Phase 4 | 外部实例 (DrugBank 药物 + UMLS 疾病) | ✅ 完成 |

### 最终成果

| 指标 | 数值 |
|------|------|
| **总实体数** | 379 (275 类 + 32 实例 + 72 CTO) |
| **CSO 命名空间** | 151 实体 |
| **CTO 命名空间** | 228 实体 |
| **药物实例** | 16 个 (Atezolizumab, Pembrolizumab, ...) |
| **疾病实例** | 16 个 (NSCLC, CRC, HCC, ...) |
| **测试匹配率** | **100%** (40/40) |
| **相比原始 CSO** | **+8.3x** |

### 核心能力

- ✅ 完整临床试验设计概念覆盖
- ✅ 复合终点 + 缩写 100% 匹配
- ✅ 设计特征 (open_label, multicohort, single_arm, randomized)
- ✅ 统计方法 (Simon_two_stage_design, CI, HR, ITT)
- ✅ 真实药物实例 (16 个常用肿瘤药物)
- ✅ 真实疾病实例 (16 个常见肿瘤类型)

### 文件

- 融合本体: `examples/cso_validation/data/cto_cso_v03_fused.rdf.xml`
- AOF Spec: `examples/cso_validation/cto_cso_v03_fused_spec.json`
- 完整文档: `examples/cso_validation/CTO_CSO_V03_FUSED_BASE.md`

---

## 里程碑：CSO v0.4 回补给 OptiMed (2026-09-02)

> 详见: /Users/chaihao/LLM/OptiMed/ontology/MIGRATION_TO_V04.md

### 任务完成 ✅

- [x] 将 CTO+CSO 融合本体从 RDF/XML 转换为 Turtle 格式
- [x] 生成 `cso_v0.4.ttl` (55K, 315 类, 34 实例, 30 关系)
- [x] 创建 `README_v0.4.md` 详细文档
- [x] 更新 OptiMed 主 `README.md` 添加 v0.4 信息
- [x] 创建 `MIGRATION_TO_V04.md` 迁移记录
- [x] 验证所有关键类和实例 (21/21 类, 16 药物, 18 疾病)

### 生成文件

| 文件 | 大小 | 说明 |
|------|------|------|
| `OptiMed/ontology/src/cso_v0.4.ttl` | 55K | CTO 融合版本 (Turtle 格式) |
| `OptiMed/ontology/src/README_v0.4.md` | 5.1K | v0.4 详细文档 |
| `OptiMed/ontology/MIGRATION_TO_V04.md` | - | 迁移记录 |

### 验证结果

- ✅ 总类数: 315 (CSO 87 + CTO 228)
- ✅ NamedIndividuals: 34 (16 药物 + 18 疾病)
- ✅ ObjectProperties: 30
- ✅ 所有关键类验证通过 (21/21)
- ✅ 测试匹配率: 100% (40/40)

### 版本对比

| 指标 | v0.3 | v0.4 (CTO Fused) | 提升 |
|------|------|------------------|------|
| 总类数 | 76 | 315 | +4.1x |
| NamedIndividuals | 0 | 34 | 新增 |
| ObjectProperties | 14 | 30 | +2.1x |
| 测试匹配率 | 36.4% | 100% | +2.7x |

---

## 里程碑：采购 FIBO 试点对照实验跑通 (2026-09-03)

> 详见: examples/procurement_fibo_pilot/RESULTS.md

### 对照实验结果

| 指标 | 基线 | FIBO（修复后） |
|------|------|--------------|
| `ontology_valid=true` 节点 | 0 | **11** |
| role 类实体 | 0 | 4 |
| 采购域实体占比（启发式） | 7.0% | 13.5% |

### 排障记录（3 个根因，均已修复并验证）

- [x] Kùzu 锁竞争：多进程并行摄取同一 `.cognee_system` → 改串行执行
- [x] Embedding 422：shell 残留 `EMBEDDING_MODEL=deepseek/deepseek-embedding` 压过 `.env` 的 ollama/bge-m3（load_dotenv override=False 不覆盖）→ 清理环境变量
- [x] 跨语言匹配失效（核心发现）：cognee resolver 按 URI 本地名建 lookup 且实体只匹配 individuals 类目；中文实体名 vs 英文类名 difflib 恒 0 命中 → OWL 增补 25 个中文本地名 NamedIndividual 作匹配桥，`ontology_valid` 0 → 11

### 遗留

- [ ] commitment 口径类未命中（LLM 抽取粒度波动）：扩充 individuals 同义变体或在案例文本强化口径术语
- [ ] `load_graph_nodes_edges` 方式 1 无 dataset 过滤会混入历史数据：权威统计需直读 dataset 自己的 Kuzu 文件解析 properties JSON
- [ ] DeepSeek key 曾于 09-02 晚后失效一次（已换新）；`deepseek-v4-flash`/`v4-pro` 显式指定会拒绝 tool_choice，必须用 `deepseek-chat` 别名
