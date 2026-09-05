# P0–P6 验收台账

日期：2026-09-05。按本任务最初的 P0–P6 定义验收：P3 是统一知识底座，P4 是本体消费，P5 是 Agentic System，P6 是单域业务闭环。历史实现说明曾重排编号，本表恢复原定义。工程基线与真实企业业务验收分别记录。

| 阶段 | 当前可复验能力 | 验收证据 | 尚未关闭 |
|---|---|---|---|
| P0 统一基线 | semantic_core 规范合同、签名控制面、旧裸 SQL 端点退役、兼容路径标识 | main 基线与完整回归 | 历史 reasoning/原始 ontology MCP 仍属兼容接口，不作为企业多租户入口 |
| P1 事实与证据 | 解析/来源快照、带证据资源、提案与批准状态 | document_parser、continuous ingestion、semantic_release_e2e | 真实企业文档和账务数据的领域核验 |
| P2 治理发布 | 约束验证、职责分离、签名发布、编译、promotion/rollback | semantic_release_e2e、semantic_governance | 企业管理员的发布策略配置与验收 |
| P3 统一知识底座 | 同一 release 的 RDF 图、关系数据快照、证据检索索引、可执行 Function/ActionType catalog | finance_review_e2e 验证所有结果的 release_digest 一致 | 默认稀疏向量；外部 Embedding/图库部署属于后续基础设施扩展 |
| P4 本体消费 | 发布 QueryContract 生成有依赖的受控计划，SQL 数据范围、图、证据、Skill、Datalog 和文本校验 | agentic_default_runtime、finance_review_e2e | 通用开放问题解析和多领域质量评估；当前支持受限词汇/已发布业务问题 |
| P5 Agentic System | 计划→数据→规则→分析 Skill→待审批 ActionPlan→独立审批→执行→回执/对账；短期 session context 和长期 DecisionRecord 分离 | finance_review_e2e 的成功与不确定结果分支；action/workflow 测试 | 真实 ERP/CRM 连接器配置；LLM fallback 尚未接入默认执行器 |
| P6 单域闭环 | 财务收入偏差、客户合同、回款、人工复核工单；审计、备份恢复、执行前阻断 | finance_review_e2e（合成样本、实际本地执行器） | 真实企业数据替换、业务负责人确认数值/条款/价值；不能将样本测试称为真实企业验收 |

## 本次实际本地验收

`tests/test_finance_review_e2e.py` 回答固定已发布业务问题：2026 年 9 月华东收入偏差。样本客户收入 80、预算 100、偏差 -20、已回款 50、未回款 30；其他月份与其他地区被 SQL 排除。合同证据定位到 HT001 第 3.1 条，Datalog 从执行结果产生需人工复核事实，分析 Skill 保留“不认定违约/不确定偏差原因”的解释，自动提交待审批工单。审批前零业务写入；独立审批后写入本地 SQLite 工单；注入连接中断时进入 reconciliation_required。查询和动作、分析函数与证据全部绑定同一 release。

`tests/test_agentic_default_runtime.py` 不注入 Agentic 模拟执行器：建立发布并 promotion 的资源，签名 REST 请求，经 QueryPolicy 查询 RDF 和 SQLite。问题 `gmv 指标多少，Customer 有哪些关系？` 返回 GMV=17 和真实 Customer 边；验证独立回放、回执持久化、不追加 memory、重复 ID 不调用工具、错误 release 在业务读取前阻断，以及 Skill/RAG/稀疏向量路径。

`evaluate().score` 只表示结构完整性，不是答案正确率。Summary 采用工具结果摘录并验证 citation 对应文本；这不构成自动事实核查。没有证据支持的返回、未配置 SQL 执行器和空检索结果均失败关闭。

## 环境盘点

当前机器有 Docker；检查时仅有 `optimed-postgres` 容器，没有 AOF 集成环境容器。主工作区 `.env` 存在 LLM/Embedding 配置项，尚未验证连通性或模型调用。现有 Compose/Kubernetes 文件是部署模板，不能当作已部署环境。其他项目的 PostgreSQL 不作为 AOF 验收库。

## 验收命令

在安装 `requirements-dev.txt` 的隔离 Python 环境中：

```bash
python -m pytest -q tests/test_semantic_release_e2e.py tests/test_finance_review_e2e.py tests/test_agentic_default_runtime.py tests/test_agentic_system.py tests/test_agentic_api.py tests/test_enterprise_runtime_e2e.py
python -m pytest -q
ruff check .
python tools/governance/validate_release_gate.py --root data/governance --strict
python tools/export_openapi.py --check
```

## 全部闭环的判定

本地工程闭环要求上述单域正负路径通过、核心文档一致、PR 检查通过并合入 main。真实业务闭环还要求替换样本并由业务负责人确认；完整四层产品目标还包括外部 LLM/Embedding 与更广领域的能力。P6 原定义不要求先部署四套大型数据库，也不将 KMS/HA/容量/灾备作为这条单域工程纵切的前置条件。
