# AOF 架构专题导航

> **主架构真值源**：[ARCHITECTURE.md](../../ARCHITECTURE.md)
> **职责**：本页只负责导航、术语和文档维护边界；不重复或替代主架构。

## 架构地图

```text
接入与治理边界
  → 语义资产生命周期（Draft / Validate / Review / Publish / Release）
  → 语义运行时（Semantic IR / 编译 / 推理 / 查询与重放）
  → 仿真与受控动作（Plan / Authorize / Execute / Compensate）
  → 决策证据与过程资产
  → 适配与承载层（摄取、Cognee/图存储、SQL/BI、导出、连接器）
```

Cognee、Bridge、semantic middle layer、OKF、RAG 与 Text-to-SQL 均是上图的输入、适配或消费组件；它们不单独定义 AOF 的系统边界。

## 专题文档

| 主题 | 真值文档 | 关注点 |
|---|---|---|
| Semantic IR 与发布 | [semantic-ir-and-knowledge-release.md](semantic-ir-and-knowledge-release.md) | 不可变 revision、release、编译契约 |
| 本体治理与推理 | [ontology-governance-and-reasoning.md](ontology-governance-and-reasoning.md) | OWL/SHACL/SKOS、审查、Datalog |
| 受控动作与仿真 | [controlled-actions-and-digital-twin.md](controlled-actions-and-digital-twin.md) | ActionPlan、授权、执行、补偿、双时态模拟 |
| 决策证据 | [decision-provenance.md](decision-provenance.md) | Evidence、Decision、审计、先例与影响 |
| 确定性企业运行时 | [deterministic-enterprise-runtime.md](deterministic-enterprise-runtime.md) | 回放、事件、恢复与受控执行 |
| Bridge 适配层 | [bridge-design.md](bridge-design.md) | AOF 与 Cognee、摄取、检索等适配边界 |
| Cognee 集成 | [cognee-integration.md](cognee-integration.md) | 图谱引擎集成专题，不是全局架构 |
| 上下文交换 | [context-exchange-v1.md](context-exchange-v1.md) | 跨上下文审批和发布边界 |
| 真实轨迹数据 | [raw-trajectory-training-data.md](raw-trajectory-training-data.md) | 训练数据通道与隐私/审计边界 |

## 术语

| 术语 | 定义 |
|---|---|
| Semantic Asset | 可审阅、可版本化的本体、mapping、规则、样例和相关资源 |
| Revision / Release | 单资源不可变内容版本 / 可共同运行的修订清单 |
| Semantic Runtime | 对已发布资产进行编译、推理、查询、仿真与受控行动的运行层 |
| Bridge | 与图存储、摄取、检索和业务系统连接的适配编排层 |
| Action Plan / Run | 受策略约束的行动计划 / 实际执行记录；二者不可混同 |
| Decision Provenance | “为何、基于什么证据、影响什么”的决策记录，不等同访问审计 |
| Statement / Runtime State | 可读导出本体与实际运行资产；需要漂移对账，当前尚未完全自动化 |

## 维护规则

1. 改变系统边界、运行主线或能力状态时，先更新 `ARCHITECTURE.md`。
2. 改变某一具体契约时，更新相应专题文档和测试；不要把专题细节复制回主架构。
3. 代码、API 和工具数量以实现为准；文档中的计数应避免成为长期事实源。
4. 已实现机制、待验证业务闭环、生产运行要求必须分别标识。
5. 历史或阶段性文档应注明适用时间与范围，避免被误读为当前架构。
