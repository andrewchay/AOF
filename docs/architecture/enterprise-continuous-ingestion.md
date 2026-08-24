# 企业知识接入与持续编译

## 1. 边界与不变量

持续接入链路以 `KnowledgeSource` revision 为发布前输入，以可重放 `IngestionRun` 为事实边界：

```text
SourceDefinition -> Connector -> SourceBatch -> Knowledge ChangeSet
                 -> Semantic IR mapping -> Proposal gates -> Knowledge Release
                 -> CompilationRun -> independent replay -> channel
```

以下约束不可被调度器、UI 或 LLM 绕过：

- Source 配置不能包含 secret、token、password、credential 或 API key；认证由部署侧连接器提供。
- 每个 Source、Run、ChangeSet、Proposal 和 Release 都绑定 tenant；跨租户 ID 返回 not found。
- SourceBatch 必须声明 `cursor_from`、`cursor_to` 和源快照。游标只与成功 Run 在一个事务中推进。
- 完整快照按稳定 identity 计算 added/updated/deleted；缺失或重复 identity 作为失败 Run 保存。
- 相同 cursor 和内容复用既有 Run；相同 cursor 对应不同内容是连接器契约违规。
- schema drift 默认阻断；质量、SHACL/OWL/SKOS、冲突或回归 finding 决定 Proposal 是否进入 review。
- channel 只能指向经独立 replay 证明可复现、且由 reviewer 审批的 CompilationRun。

## 2. 连接器模型

内置连接器包括 JSONL 完整快照和 SQLite 表快照。两者在 API 中受
`AOF_INGESTION_FILE_ROOTS` 路径白名单约束，SQLite 游标由记录与 schema 的内容哈希产生。

HTTP JSON API 与事件流连接器采用注入式 transport/reader。部署适配器负责 OAuth、mTLS、数据库凭据、
消息消费组和限流；这些能力不进入 Source JSON，也不会进入审计输出。API 连接器返回 records、next cursor、
etag/response digest；事件连接器返回事件和确认后的 offset。

新增连接器实现 `SourceConnector.fetch(source, cursor) -> SourceBatch`，并通过
`SourceConnectorRegistry` 注册。连接器不能自行写 Source cursor，也不能在结果未知时重试副作用请求。

## 3. 映射与编译策略

映射适配器把 ChangeSet 转换为 canonical `SemanticResource`，再调用：

`POST /v1/knowledge/ingestion-runs/{run_id}/stage`

该边界重新计算所有 resource revision，将 IngestionRun 与 ChangeSet 作为 Proposal 的 source evidence，执行
本体校验、查询回归、冲突检查和影响分析。它不接受未定版的自然语言映射结果。

`ContinuousCompilePolicy` 有两个发布模式：

- `manual`：门禁通过后返回 `awaiting_approval`，由 reviewer、compiler、publisher 的独立签名身份继续。
- `automatic`：只适用于服务账号已显式配置且职责分离的流水线；仍执行审批、首次编译、独立 replay 和发布，
  任一步失败均不移动 channel。

schema drift 与校验冲突始终优先于发布模式。自动模式不是绕过审批，而是由受控服务身份执行同一状态机。

## 4. 审计与恢复语义

每次成功或失败接入都写决策账本，REST 返回 `audit_decision_id`。成功链继续包含
`knowledge_ingestion_accepted`、Proposal/validation/approval/publish、首次 compile、独立 replay 和 channel
promotion。schema drift 产生独立 blocked decision。

失败 Run 不推进 cursor、不自动重试。运营方修复连接器或源数据后必须以新 attempt ID 再次执行；重复旧
attempt 仅返回相同失败证据。由此可以区分“请求失败”“结果未知”和“已确认无变化”。

## 5. 能力边界

当前内核提供受治理契约、四类连接器边界、确定性 diff、Proposal 门禁与 replay 发布。企业数据库 CDC、
Kafka/Pulsar consumer、SaaS OAuth 和领域映射包属于部署适配器；接入时必须保留上述 SourceBatch 和
SemanticResource 契约，不能直接写 Release 或 channel。

