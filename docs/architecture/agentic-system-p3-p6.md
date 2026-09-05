# AOF Agentic System P3–P6

## 目标与边界

阶段编号以 [原始 P0–P6 验收台账](p0-p6-acceptance.md) 为准。本文下方按实现组件组织，不再重新定义用户的阶段范围。

P3–P6 把 P0–P2 已发布的本体资源变成可规划、可执行、可解释、可重放的业务问答与任务运行时。系统保存显式任务计划、路由理由码、工具回执和证据，不保存或要求模型私有思维链。

```text
用户 Query
  -> P4 OntologyIntentRouter / published QueryContract
  -> P3 release-pinned graph / relational / evidence / Skill projections
  -> P5 AgenticPlan + session context + governed query/action/workflow
  -> evidence-bound summary
  -> P6 finance-domain acceptance + audit + replay
```

## 本体消费与意图路由（P4）

`OntologyIntentRouter` 把问题路由到 `graph_search`、`skill_search`、`vector_search`、`semantic_sql`、`rag_retrieve` 或显式批准的 `llm_fallback`。一次问题可以选择多个能力，但不能超过 `max_steps`。

每次路由保留 `query_digest` 和稳定的 `rationale_codes`。本体意图无法识别时，只有策略允许才回退 RAG；LLM 回退默认关闭。未知能力、无批准回退或超出步骤预算均失败关闭。

## 规划、记忆、工具协同与总结（P5）

`AgenticPlan` 固定 release ID/digest，将每个检索能力表示为显式步骤，最后增加依赖全部结果的 summary 步骤。`SqliteAgenticRunRepository` 保存租户隔离的 run 与多轮 session memory，并在读取时验证 digest。

能力执行器必须返回结构化 `output` 和非空 `evidence`。`AgenticSummaryValidator` 要求每条 claim 至少引用一个当前 run 的 capability result，未知或缺失 citation 会阻断回答。

## 同版本投影与受治理执行（P3/P5）

默认检索执行器连接现有 `QueryControlPlane`；业务计划的动作连接 `ActionControlPlane`，分析 Skill 使用注册的只读实现：

| Agentic 能力 | 受治理查询能力 |
|---|---|
| `graph_search` | `graph` |
| `skill_search` | `semantic_search` + 已发布 semantic-json 中的 Function/ActionType/Workflow |
| `vector_search` / `rag_retrieve` | `semantic_search` |
| `semantic_sql` | `semantic_sql` |
| `rule_search` | `datalog`，事实只取前置已执行 SQL |
| `skill_execute` | 已发布且经 catalog 授权的只读 finance_variance Function |
| `action_submit` | ActionControlPlane，提交后等待独立审批 |

每个步骤再次验证签名 principal、tenant、channel、QueryPolicy 和 release digest。版本 pin 在执行器读取业务数据前校验。图查询通过 URI 局部名称或 RDFS/SKOS label 识别一个实体；SQL 解析已发布 metric/dimension 名称或 aliases，编译为 typed intent 后只读执行。无法唯一解析、未配置 SQL 后端、检索无结果均返回错误，不将空结果包装成成功答案。

发布的 `QueryContract.spec.agentic_steps` 可定义 SQL→规则→动作的依赖。`business_plan.py` 验证问题、purpose、能力和步骤依赖后生成计划。只读 finance_variance Function 消费已执行 SQL 和受控 catalog；动作对象仅来自前置规则结果，自动生成 ActionPlan 并 submit，要求独立审批，Agentic 返回 awaiting_approval。批准/执行使用原有 action-runs 控制面；refresh-actions 拉取回执并更新为 succeeded 或 reconciliation_required。

## 单域验收、审计与恢复（P6）

成功和失败 run 记录 `agentic_query_orchestration` DecisionRecord；成功工具的 query decision 作为父决策。运行前先持久化 reservation，逐步记录工具结果，成功结果与两条会话记忆在同一事务提交。失败保留已完成步骤，重复 ID 不重做工具。`evaluate()` 检查结构和引用完整性，不能用其分数声称业务准确率。

重放比较规范化的路由、计划、工具输出和证据，receipt 单独存放，结果不一致返回并持久化 `reproducible=false`。重放使用原始 memory snapshot，不写入会话。记忆按 tenant/session 隔离，最多读取 20 条、执行上下文至多 64 KiB；不是每用户私有会话。SQLite digest 用于损坏检测，不等同于防管理员篡改的外部签名。

## REST 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/v1/agentic/route` | 仅规划路由，无工具执行 |
| POST | `/v1/agentic/runs` | 执行并持久化 Agentic run |
| GET | `/v1/agentic/runs/{run_id}` | 读取当前租户 run |
| POST | `/v1/agentic/runs/{run_id}/replay` | 独立重放 |
| GET | `/v1/agentic/runs/{run_id}/evaluation` | 运行质量门 |
| GET | `/v1/agentic/sessions/{session_id}/memory` | 读取租户隔离会话记忆 |
| POST | `/v1/agentic/runs/{run_id}/refresh-actions` | 同步动作回执及总运行状态 |

运行和重放要求签名 principal 拥有 `analyst`、`operator` 或 `admin` 角色；读取允许已有只读角色。请求体不能指定 tenant 或 actor。

## 当前验收范围

仓库测试覆盖多路中文/英文路由、RAG/LLM 回退控制、证据阻断、citation 校验、租户隔离记忆、重放、篡改检测、签名 REST 角色控制，以及既有增量推理、WorkflowRun、Simulation、Action、生产就绪和企业 E2E。

尚需外部环境验收：真实图/向量/Skill/SQL 后端、真实 LLM fallback、Kafka/Pulsar、ERP/CRM connector、KMS/HSM、HA/容量/压测、canary 和灾难恢复演练。这些不能由本地 fixture 测试替代。

默认图后端是实际 RDFLib，SQL 是只读 SQLite，向量为稀疏词频余弦检索，不是外部 Embedding。默认路由为词汇规则；业务 QueryContract 支持已发布、有依赖的 DAG，未实现开放式动态重规划。LLM fallback 的默认执行器尚未实现。当前限制及单域实际验收见 [台账](p0-p6-acceptance.md)。
