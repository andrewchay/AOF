# 受控行动与数字孪生运行层

## 定位

AOF 的可信查询层回答“基于哪个 Release、计划和证据得出结果”。受控行动层进一步回答：允许谁基于哪个
Release 对哪个企业对象执行什么副作用、执行前看到了哪些影响、是否获得审批、结果是否确定，以及失败后如何
补偿或进入 reconciliation。

行动运行层不得把 LLM 输出直接映射为 Connector 调用。LLM 最多提出 Intent；确定性编译器产生
release-pinned ActionPlan，Policy gate 决定是否允许进入审批或执行状态。

## Semantic IR v1 扩展

### Function

Function 描述 Connector 的逻辑操作，只包含 `connector` 引用、`operation`、明确的 `side_effects` 和
`timeout_ms`。API key、token、password、credential 等密钥材料禁止进入 Semantic Resource 和 Release，
由部署期 Connector provider 注入。

### ActionType

ActionType 绑定 Function 和目标 ObjectType，声明：

- JSON object input schema；
- `effect_class`：`reversible` 或 `irreversible`；
- `idempotency_scope`：`object`、`request` 或 `tenant`；
- 必需审批角色；
- 后续 Slice 引入的权限、影响与补偿契约。

Function 和 ObjectType 必须同时存在于同一 Release，并显式列入 ActionType 的 `depends_on`。不可逆动作必须
声明至少一个审批角色。

### Workflow

Workflow 是 ActionType node 的确定性 DAG。每个 node 具有稳定 `node_id`、`action_type_id` 和 node
dependencies；未知依赖、重复 node、未发布 ActionType 和环都会阻断编译。画布位置、UI 布局和运行状态不属于
Workflow 的语义 revision。

## 编译与发布

`ActionCompiler` 注册为 `actions@1`，并强依赖 `semantic-json@1`。它输出
`application/vnd.aof.action-catalog+json`，包含源 Release ID/digest、Function、ActionType、Workflow 的精确
revision，以及 Workflow 的确定性 execution order。相同输入重复编译必须得到相同 content hash。

编译器会递归拒绝 action spec 中的 secret-like field。Catalog 只证明契约可编译，不代表动作已获准执行。

## ActionPlan 与执行前治理

`ActionRequest` 规范化 channel、ActionType、目标对象、输入、purpose、idempotency key 和 Policy resource，
并生成 request digest。`GovernedActionPlanner` 只接受 independently reproduced channel run，逐 bytes 验证
`actions` 与 `semantic-json` artifact，再从同一 Release 解析 ActionType 和 Action Policy。

Action Policy 以 `policy_type=action` 声明 role → ActionType 权限及 ActionType 级 purpose/最大影响对象数。
Planner 在任何 Connector 可见之前依次完成 JSON object input schema、租户、角色、purpose 和 blast radius
检查。输出的 `aof.action-plan/v1` 绑定 channel pointer/version、CompilationRun、Release、artifact hashes、
ActionType revision、Policy revision/report 和 impact report。Impact report 至少列出精确对象集合、对象数量、
effect class、引用该动作的 Workflow 和是否需要审批。

## ActionRun 状态机

`SqliteActionRunRepository` 以 tenant/action/idempotency scope/key（object scope 还包含对象集合）形成唯一
idempotency identity。重复提交相同 plan 返回同一 ActionRun；同一 identity 绑定不同 plan 会被事务唯一门拒绝。
Repository 对状态更新使用 optimistic digest、验证每个 history event 和嵌入 ActionPlan digest，并提供全库
完整性扫描、schema version 与 SQLite online backup。

需要审批的 Run 从 `awaiting_approval` 开始，请求人不得自批，审批者必须持有 ActionType 指定角色。执行前先把
状态持久化为 `executing`，再调用部署期 `ActionConnector`；因此 worker 在调用前后崩溃时，重启实例不会盲目
重试，而是把遗留 `executing` 转为 `reconciliation_required`。Connector 凭据只存在于 provider，不进入
ActionPlan 或 ActionRun。

已确认成功进入 `succeeded`；明确未产生效果的失败进入 `failed`；已产生部分效果的可逆动作调用同 Connector
上的精确 compensation operation，成功后进入 `compensated`。超时、连接中断、未知 effect 或补偿不确定均进入
`reconciliation_required`。终态重复 execute 返回原 Run，不再次产生副作用。submit、approve、execute 和结果
均链接 Decision Provenance，绑定 Release、CompilationRun、ActionPlan 和 Policy revision。

## 当前完成边界

当前已完成统一资源类型、确定性 catalog、release-pinned ActionPlan，以及事务型 ActionRun 的幂等、审批、
补偿和 reconciliation 状态机。双时态对象、事件规则和生成式 MCP 行动工具属于后续 Slice；在统一生产 E2E
完成前不得声称完整企业行动平台已生产就绪。
