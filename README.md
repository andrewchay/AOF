# Agentic Ontology Factory (AOF)

> **当前定位**：受治理的语义运行时
> **文档状态**：仓库能力入口；已实现机制、待验证闭环与生产目标严格区分。

AOF 是一个受治理的企业语义运行时平台。它将企业知识、业务口径与规则转化为可版本化、可审阅、可推理的语义资产，让人和 Agent 在明确的证据、权限、发布与审批边界内查询、建议、仿真和执行受控动作。

AOF 不只是知识图谱、RAG 或 Text-to-SQL。它们是语义资产的输入、承载或消费面；核心是让已发布的语义能被确定性运行，并能追溯决策和动作。

## 当前能力

| 层 | 已实现机制 | 边界 |
|---|---|---|
| 语义资产治理 | OWL、SHACL、SKOS 草稿校验、审查、审批、豁免、发布与不可变 release | 不代表任意领域本体已通过业务验收 |
| 语义运行 | Canonical Semantic IR、mapping、Datalog、查询与编译重放 | 不保证模型输出正确，所有消费仍受版本与策略约束 |
| 受控动作 | Action Contract、Plan、Policy、Control Plane、Run、补偿与双时态仿真 | 不等于已完成真实生产自动执行 |
| 决策证据 | append-only、SHA-256 哈希链、审计轨迹、先例和影响检索 | 决策孪生与偏差检测仍是后续能力 |
| 接入与适配 | MCP、CLI/API、文档/SQL/元数据摄取、图后端、OKF/Markdown 导出 | 外部系统、HA、KMS/HSM、容灾等生产验证不在本仓库结论内 |

## 从资产到运行

```text
文档 / 元数据 / SQL / 专家反馈
  → semantic asset draft → validate / review / approve → publish / release
  → Semantic IR、mapping、规则与确定性推理
  → 查询、仿真、建议、授权与受控动作
  → 决策、运行和结果证据 → 下一次修订候选
```

高风险路径始终分离：LLM 可以提出意图或建议；发布语义、批准计划、执行连接器、补偿和人工对账是独立状态，不能由单次生成直接跨越。

## Agent 与 API 入口

MCP Server 当前注册 **28 个**工具，覆盖：

- 语义查询、持久化 QueryRun 与独立重放；
- 混合检索、图谱分析、数据集与 OKF 消费；
- 决策记录、审计轨迹和先例检索；
- OWL/SHACL/SKOS 草稿治理；
- Datalog 规则集发布与确定性推理；
- 文档解析等知识摄取辅助能力。

工具、REST API、CLI 与 SDK 都必须服从其后端的 release、权限、策略和审计契约。工具总数会演进，以 `mcp_server.py` 为准。

## 首个业务闭环：财务控制平面

财务是 AOF 的第一个可审计证明点，而不是产品边界。ERP 仍是正式账本；AOF 位于其上方，负责受治理的解释、例外闭环、行动编排、审批、证据与价值归因。Text-to-SQL 和指标口径治理是其中的受控入口和基础能力，不是平台的终局价值。

```text
ERP / 预算 / 银行 / 采购 / 合同 / 运营数据
  → 财务语义、Policy Pack 与责任边界
  → 例外/机会识别、解释与影响推演
  → 行动包、审批与受控系统动作
  → 结果对账、升级/回滚与 Impact Ledger
```

首个试点应围绕一个高频财务例外流程，用真实历史案例验证闭环时效、控制完整性、结果可审计性与价值归因。在此之前，AOF 不宣称已验证 L3/L4 成熟度、仿真业务收益或端到端生产自动化。完整定位见 [运行时平台定位与财务控制平面](docs/product/AOF_运行时平台定位与财务控制平面_v2.md)。

## 文档导航

- [总架构与能力边界](ARCHITECTURE.md)
- [架构专题导航](docs/architecture/README.md)
- [语义中间层输入与产物契约](docs/语义中间层接口契约.md)
- [Semantic IR 与 Knowledge Release](docs/architecture/semantic-ir-and-knowledge-release.md)
- [本体治理与确定性推理](docs/architecture/ontology-governance-and-reasoning.md)
- [受控行动与数字孪生运行层](docs/architecture/controlled-actions-and-digital-twin.md)
- [决策级溯源](docs/architecture/decision-provenance.md)
- [运行手册](docs/operations/)

## 运行与验证

仓库中的文件/SQLite 实现用于开发与可重复验证；生产部署需要独立完成身份信任域、密钥管理、连接器、容量、可观测性、备份恢复与灾难恢复验收。请以专题运行手册中的准入条件为要求，而非将其视为已验证的生产事实。

## 历史定位

早期版本以“知识图谱 + 四形态资产导出”描述 AOF。这些能力仍作为适配与消费面保留，但不再构成完整架构叙事。
