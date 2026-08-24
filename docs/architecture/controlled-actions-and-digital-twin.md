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

## 当前完成边界

本 Slice 已完成统一资源类型、跨资源引用校验、凭据隔离、Workflow DAG 校验和确定性 catalog 编译。
ActionPlan、权限/影响预检、ActionRun、审批/补偿、双时态对象、事件规则和生成式 MCP 行动工具属于后续
Slice；在相应 E2E 完成前不得声称 Action 已可生产执行。
