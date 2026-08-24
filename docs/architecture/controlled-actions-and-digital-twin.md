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

## 双时态 Object/Fact

`BitemporalObjectStore` 不把对象保存为可覆盖的当前 JSON，而是保存 Fact version。每个 version 同时具有
业务有效区间 `[valid_from, valid_to)` 和系统认知区间 `[tx_from, tx_to)`；同一 `fact_id` 的修正关闭旧
transaction interval 并新增 version，不删除旧值。`recorded_at` 必须单调前进，fact identity 不得改变对象或
field。

`snapshot(valid_at, known_at)` 因而能分别回答“该业务时点什么为真”和“在该认知时点系统知道什么”，返回
values、Fact version/digest、source、valid/transaction intervals 及关联 ActionRun。重叠且同时有效的同字段
Fact 会被视为歧义而拒绝，不用隐式 last-write-wins 掩盖冲突。Store 按 tenant 隔离，并提供 digest scan、
schema version 和 SQLite online backup。

## 事件订阅与增量确定性规则

RuleSet 可在 spec 中声明 `event_subscription`：接收的 event types、trigger predicate、同 Release 的
ActionType、purpose 与 Action Policy。`DatalogCompiler` 将该契约写入 release-bound artifact；
`PublishedEventSubscriptionResolver` 同时验证 reproduced channel、Datalog artifact、Action catalog bytes 和
Release digest，禁止 Rule 与 Action 跨 Release 拼接。

`IncrementalActionRuleRuntime` 对 event ID、subscription ID 和 asserted facts 做事务去重，持久保存 subscription
事实集合。每个新事件重新计算确定性 Datalog fixed point，但只对首次出现的目标 derived fact 生成一个稳定
ActionTrigger；重启或重复投递不会再次触发。Trigger 绑定 Event digest、Subscription/ruleset version、Release、
derived proof、ActionType 和 Policy，并记录 Decision Provenance。Trigger 只是受治理的 ActionRequest 候选，仍须
经过 ActionPlan、Policy、影响、审批与 ActionRun，不能直接调用 Connector。

当前实现选择“持久事实增量 + 批量固定点”作为可复现基线，不宣称是 Rete。只有真实吞吐/撤回/延迟基线要求
更高时才替换为 Rete 网络；替换实现必须保持相同 subscription digest、derived fact 与 ActionTrigger 语义。

## 当前完成边界

当前已完成统一资源类型、确定性 catalog、release-pinned ActionPlan、事务型 ActionRun，以及双时态
Object/Fact point-in-time 查询，以及 release-bound 事件增量规则与 ActionTrigger。生成式 MCP/SDK 行动契约
和统一生产 E2E 属于后续 Slice；完成前不得声称完整企业行动平台已生产就绪。
