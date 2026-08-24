# 确定性推理与企业运行层

## 1. 运行闭环

企业运行层把已发布语义从“可查询”推进到“可模拟、可审批、可执行、可恢复”：

```text
DomainEvent / FactChange
        ↓
release-pinned Datalog state
        ↓
bitemporal baseline ↔ counterfactual candidate
        ↓
ActionPlan / WorkflowPlan
        ↓
approval → ActionRun → effect / compensation / reconciliation
```

所有边界都绑定 tenant、Release/CompilationRun 或 ruleset version、内容摘要、签名 actor 和 Decision
Provenance。LLM 可以提出意图或 assumptions，但不能生成未校验的 Connector 调用、修改事实状态或移动运行指针。

## 2. 可撤回确定性推理

`ReasoningFactChange` 是 immutable assertion/retraction 单元，具有稳定 change ID、`effective_at`、source 和
digest。`SqliteIncrementalReasoningRuntime` 保存完整 change log，并按 `(effective_at, change_id)` 的确定性顺序
物化 asserted facts 与 Datalog closure。因此：

- 重复 change ID 与相同内容返回同一 Run；不同内容发生冲突；
- 晚到旧事件不会覆盖较新的撤回；
- 基础事实撤回会使失去支持的递归派生事实和 proof 一并消失；
- 每个 Run 保存输入快照、derived proof、增删 delta、ruleset version 和 replay hash；
- 规则 program 不能在同一 ruleset ID 下偷换；升级必须发布新 ID/version。

当前实现是“持久 change log + 确定性重物化 + delta 发布”，提供正确的撤回、乱序和 replay 语义；它不是
Rete 网络，也未宣称具备高频流式吞吐。只有基准证明重物化不满足延迟目标后，才引入 Rete/semi-naive 执行器；
新执行器必须保持完全相同的 change、proof、Run digest 和 replay 契约。

## 3. 双时态反事实模拟

`SimulationRequest` 绑定一个已经通过权限和影响门的 ActionPlan，并指定：

- `valid_at`：业务世界的有效时间；
- `known_at`：系统当时掌握知识的时间；
- field → Datalog predicate 的显式映射；
- candidate assertions/retractions；
- outcome 与 blocking predicates。

Simulation 先从 `BitemporalObjectStore` 读取 baseline，再在内存中叠加 assumptions，分别执行同一规则集。
结果包含新增/移除 outcome、受影响对象、review/blocked 状态和两个结果 hash。该服务不接收
`ActionConnectorRegistry`，结构上无法产生副作用；重复 simulation ID 幂等，历史 Run 使用冻结事实严格 replay。

## 4. WorkflowRun

`WorkflowPlan` 中所有节点必须来自同一 tenant、channel、Release 和 reproduced CompilationRun；每个节点保存完整
ActionPlan，DAG 在创建时进行未知依赖和环检查。`WorkflowRunService` 只激活依赖已确认 succeeded 的节点：

- 节点审批复用 ActionRun 的职责分离和所需角色；
- 每个节点拥有独立 ActionRun、idempotency identity、receipt 和决策证据；
- 下游明确失败时，已成功可逆节点按逆拓扑顺序补偿；
- 超时、worker 崩溃、未知 effect 或补偿不确定进入 `reconciliation_required`，永不盲重试；
- 每次 activate/approve/complete/compensate 都形成 checkpoint 和决策链。

## 5. 签名控制面

`EnterpriseRuntimeControlPlane` 为 REST 和未来 MCP/SDK 提供同一状态机。角色边界如下：

| 动作 | 角色 |
| --- | --- |
| 应用 FactChange / replay reasoning | `reasoner` |
| 运行 / replay simulation | `analyst` |
| 启动 Workflow | `operator` |
| 审批 Workflow node | `reviewer` + ActionType 所需角色 |
| 推进 Workflow checkpoint | `worker` |
| 查询 | 已授权只读角色 |

actor、tenant 和 roles 只来自 HMAC 签名 Principal。请求 body 中同名字段被忽略或与签名 tenant 对比后拒绝。

## 6. 已验证与部署边界

已验证：fact assertion/retraction、递归证明失效、乱序事件、重复事件/change、双时态知识修正、Simulation replay、
两节点 Workflow、职责分离审批、逆序补偿、未知结果 reconciliation、重启恢复、在线备份、篡改检测，以及
Event → Reasoning → Simulation → Workflow → Action 的端到端链路。

未内置：企业 Kafka/Pulsar consumer、Rete 高吞吐网络、真实 CRM/ERP Connector、KMS/HSM signer 和跨区 HA。
这些是部署 provider 与容量工程，不得以 fixture 结果宣称真实生产吞吐或下游兼容性。

