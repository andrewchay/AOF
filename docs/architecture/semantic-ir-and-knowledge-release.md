# AOF Semantic IR 与 Knowledge Release

## 已实现基线

`bridge.semantic_core` 是 AOF 统一语义语言的公共契约。语义资源使用稳定的
`aof://{tenant}/{domain}/{kind}/{name}` 身份；语义内容经过确定性 JSON 规范化后生成
`sha256:` revision。资源实例及嵌套内容不可变，反序列化会重新计算 revision，从而发现内容篡改。

P0 资源类型覆盖业务语义、数据绑定、规则、约束、查询模板、检索配置和策略。OWL、SKOS、
SHACL、SQL、OKF、RAG 与 MCP 不是主模型，而是后续由同一 Revision/Release 生成的编译产物。

## 身份层级

1. `resource_id`：跨版本稳定的业务身份。
2. `revision_id`：某一资源语义内容的不可变摘要。
3. `release_id`：一组能够共同运行的 revisions 的发布身份。
4. `release_digest`：Release Manifest 的可验证内容摘要。

治理时间、操作者和审批签名属于 attestation，不进入语义 revision；owner、security policy、
valid time、证据和依赖属于语义契约，会影响 revision。

## 确定性约束

- 映射键顺序、标签顺序、依赖顺序和证据顺序不影响 revision。
- 语义列表（例如字段顺序、规则顺序）保持原始顺序并影响 revision。
- 非有限浮点数、非字符串映射键和非 JSON 语义值被拒绝。
- 每条证据必须有稳定 `evidence_id`；同一 ID 的冲突内容被拒绝。
- 依赖必须是合法 AOF Resource ID，资源不能依赖自身。

## Knowledge Release Manifest

`KnowledgeRelease` 把能够共同运行的 revisions、编译产物、验证报告与治理引用冻结成一个
`aof.release/v1` manifest。构建时强制检查资源依赖闭包；资源和产物输入顺序不会影响
`release_digest`。反序列化和发布都会重新验证摘要。

本地 `FileReleaseRepository` 是开发后端：相同发布可幂等重试，但同一个 `release_id` 不允许被
不同摘要覆盖。生产实现应遵循相同接口语义并迁移到支持事务、租户隔离和并发约束的存储。

## 编译器合约

`bridge.semantic_core.compilers` 提供深而窄的插件接口。编译器必须声明目标、版本、支持的资源
类型以及明确忽略的资源类型；Release 中出现未分类类型时编译被阻断，不能静默丢失语义。

编译输入不是只有 revision 引用的 Manifest，而是 `CompilationInput`：候选 Release 加上完整、冻结的
`SemanticResource` 内容。Registry 要求资源 ID、类型和 revision 与 Manifest 精确相等，避免编译器
在相同 revision 引用下读取漂移的外部状态。

每个 `CompiledArtifact` 固定记录 compiler、候选 Release digest、全部输入 revision、媒体类型和原始
文件 SHA-256。`CompilerRegistry` 在返回产物前现场验证路径、文件摘要和 Release 绑定；产物 URI
不得逃逸编译输出目录。相同 Release 的可复现性通过跨目录构建得到相同 content hash 验证。

基线 `semantic-json` 编译器输出完整可移植的 IR bundle。Artifact 绑定的是无产物的候选 Release，
最终 Knowledge Release 再收录 Artifact 清单，因此不会形成“Release digest 包含自身”的循环摘要。

## 旧资产防腐层

`bridge.semantic_core.adapters` 将当前 AOF 资产转换为统一 IR，不要求一次性改写旧存储：

- ontology release → `Ontology` + `ConstraintSet` + `Vocabulary`，并校验三个源文件哈希；
- mapping library → `PhysicalDataset`、`Metric`、`Dimension`、`Concept`、`QueryTemplate`；
- Datalog release → `RuleSet`，保留 program、旧引擎 hash 和源文件 hash；
- OKF bundle → `RetrievalProfile`，记录所有 Markdown 文件及 bundle 内容摘要。

适配器产生的依赖必须闭包，能够直接构建 Knowledge Release。旧版本号和旧内容哈希作为迁移
证据保留，但发布时间等易变字段不进入语义 revision，避免相同资产在不同环境产生不同身份。

## Proposal 治理与发布状态机

`SemanticGovernanceService` 将统一语言真正接入发布控制面：

```text
proposed -> review | conflict_review -> approved -> ready -> published
                         |                 ^
                         +-- waiver -------+
                         +-- changes_requested
```

- Proposal 创建时冻结完整资源及候选摘要，并记录 `semantic_proposal` 决策；
- validate 汇总可插拔校验器 findings，阻断项必须解决或被策略允许且显式 waiver；
- impact 对比 parent Release，报告 added、removed、changed、unchanged；
- approve/request-changes 是显式人工决策，过期状态转换被拒绝；
- compile 只接受已批准提案，产物现场验签后形成最终 Release Manifest；
- publish 使用不可覆盖的 Release Repository，并记录发布 attestation。

proposal、validation、waiver、approval、compile、publish 都写入既有决策溯源账本并以父决策连接，
因此可直接复用因果链、先例、影响分析和审计轨迹能力。REST 控制面位于
`/v1/semantic/proposals/*`，不可变发布读取入口为 `/v1/semantic/releases/{release_id}`。

P0 的持久层是本地原子文件和 append-only 决策账本；生产替换必须保持相同状态机、摘要和禁止覆盖
语义，同时补齐数据库事务、并发版本检查、租户授权、审批职责分离和签名 attestation。

## P0.5 本体发布门禁

统一 Proposal 的 REST 控制面默认加载 `ontology_release_validator`，不再需要调用方手工注入校验器。
同一候选 Release 中的 `Ontology` 与 `Vocabulary` 会合并为数据图，`ConstraintSet` 合并为 Shapes 图，
然后执行与既有本体治理服务相同的确定性 SHACL Core 子集、OWL 冲突和 SKOS 完整性检查。

校验结果统一转换为 `SemanticFinding`，保留 constraint component、focus node、path 与原始 finding
类型等细节。SHACL 未支持约束和 RDF 解析错误不可 waiver；其他冲突必须显式 waiver 或修订后重新
提交，未解决的阻断 finding 不能进入 approval。无本体资源的 Release 不受该门禁影响。

## 全资源影响与语义回归门

`SemanticImpactAnalyzer` 以 Resource `depends_on` 构建传递依赖图，对比前后 revisions 后输出
`aof.semantic-impact-report/v1`。报告区分 added、removed、changed 和 downstream affected resources，
并派生需要重建的 MCP tools、QueryContracts 与 runtime artifacts；Proposal `/impact` 直接返回这些字段和
稳定 report digest，因此 Metric 或 Binding 变化不再只有文件级 diff。

`QueryContract` 是一等 Semantic Resource。默认 REST Proposal validator 当前支持 `semantic_sql` 与
`semantic_search` 两类回归：前者重新编译类型化 Intent 并比较参数化 SQL，后者在候选 RAG 资源快照上比较
golden resource IDs。编译失败或结果漂移均生成不可豁免 blocking finding，必须修改候选资源或更新并审查
contract revision 后才能发布。

## P0.5 多运行时编译

`default_compiler_registry()` 是 API 和嵌入式调用共享的默认编译器集合，当前包含：

- `semantic-json`：完整、可移植的统一 IR 快照；
- `owl`：Ontology 与 Vocabulary 的 RDF 源内容、格式和 Revision bundle；
- `shacl`：ConstraintSet 的 Shapes bundle；
- `datalog`：可加载的规则集及 program；
- `rag`：检索配置、概念、指标、维度、数据集、绑定和查询模板索引；
- `mcp`：所有 SemanticResource 的 MCP resource catalog，并为查询模板、规则集和检索配置生成工具描述。

所有 target 都显式分类每一种 ResourceKind；目标所需资源为空时编译失败，避免生成貌似成功的空产物。
产物使用 canonical JSON，输入资源顺序不影响字节摘要，并继续绑定候选 Release digest 与完整 Revision 集。

## P0.5 事务、租户与发布证明

REST 控制面默认使用 `SqliteReleaseRepository`。发布在 `BEGIN IMMEDIATE` 事务内完成，并以
`(tenant_id, release_id)` 为唯一键：同租户同 ID 的相同摘要可幂等重试，不同摘要不可覆盖；同一
release ID 可以在不同租户独立存在，读取时必须按租户定位，兼容性无租户读取遇到歧义会拒绝。
Proposal 创建时从 `aof://` 身份推导唯一 tenant，并拒绝跨租户资源或 scope 冒充。

`SemanticGovernancePolicy` 对 create、validate、waive、review、approve、compile、publish 分配明确
角色，并执行 creator ≠ approver、approver ≠ publisher 的职责分离。REST 默认启用该策略；actor
使用 `role:subject` 形式。

REST 不再信任请求体 actor。身份网关必须提供 `X-AOF-Principal-Subject/Tenant/Roles/Timestamp/Key-Id`
及其 HMAC 签名；服务端使用 `AOF_SEMANTIC_IDENTITY_SECRET` 验证完整 payload、时效和 key ID，然后
按操作从已签名 roles 选择 actor。缺失配置返回 503，缺失、过期或错误签名返回 401；所有 Proposal
和 Release 读取、写入都强制使用签名 Principal 的 tenant，跨租户访问表现为资源不存在。

配置 `AOF_RELEASE_SIGNING_SECRET` 后，publish 会生成 detached `aof.release-attestation/v1`：它绑定
tenant、Release ID/digest、发布决策、actor、时间、key ID 和 HMAC-SHA256 签名。密钥只存在于运行
时配置，不进入 Release、Proposal、决策账本或 attestation；`AOF_RELEASE_SIGNING_KEY_ID` 支持轮换。

## 仓库资产端到端验收

`tests/test_semantic_release_e2e.py` 使用仓库中的真实资产而不是内存假对象：

- `ontologies/genshin_ontology.owl`；
- `data/middle_layer/api_demo_topic/mapping/*.yaml`；
- `examples/semantic-release/rules/entity-search.dl`；
- `examples/semantic-release/okf/index.md`。

测试通过 adapters 形成同租户依赖闭包，运行默认本体 gate、独立审批、六目标编译、SQLite 发布、
HMAC 验签和决策审计完整性检查。真实 mapping 同时覆盖“不同物理表存在同名维度”的迁移情况；
适配器仅在发生重名时使用物理字段限定 Resource ID，避免把不同维度压成同一身份。

验收命令：

```bash
.venv/bin/python -m pytest tests/test_semantic_release_e2e.py -q
```

## 运行时消费闭环

`SemanticRuntimeConsumer` 在加载任何产物前重新验证文件 SHA-256、候选 Release digest 和完整输入
Revision。通过后，OWL/SHACL bundle 会实际解析为 RDF 图，Datalog bundle 会进入确定性推理引擎，
RAG bundle 可按统一资源内容检索，MCP bundle 会生成可枚举的 resources/tools catalog。运行时不接受
未注册 target，也不会在摘要失败时降级读取。

## 可治理编译计划

`CompilerRegistry.plan()` 是编译前的无副作用 dry-run。它解析 target 依赖 DAG、补齐传递 target、
执行每个 compiler 的资源兼容检查，并输出 `aof.compile-plan/v1`。计划固定 Release digest、请求目标、
拓扑步骤、每步资源与输入 Revision、精确 `target@version` compiler lock 和结构化 diagnostics；全部
内容生成 `plan_digest`。调用顺序和资源顺序不会改变计划，未注册 target、空输入能力或依赖环都会
形成 blocking diagnostic，不能进入实际编译。

## 编译策略与精确豁免

编译门禁不是进程内配置，而是统一 IR 中 revision-addressed 的 `Policy` 资源。`CompilerPolicy`
只接受 `spec.policy_type: compiler`，并对 CompilePlan 执行以下确定性检查：

- `allowed_compilers` 同时限定 target 和精确 `target@version` compiler identity；
- `required_targets` 要求目标已出现在依赖闭包中；
- `denied_targets` 显式阻止高风险或未投产 target；
- 未进入 target allowlist 的编译器默认拒绝。

策略结果为 `aof.compiler-policy-report/v1`，固定 Policy resource/revision、plan digest、稳定 finding ID、
waiver 状态和 report digest。`CompilationWaiver` 必须绑定精确 finding ID 与 Policy revision，并记录 actor、
rationale 和外部 authority；策略 revision 变化后旧 waiver 自动失效。只有列入 `waiver_allowed_codes` 的
风险 finding 可被豁免，CompilePlan 自身无效始终不可豁免。该报告是后续不可变 CompilationRun 的强制输入，
不会直接触发编译或发布副作用。

## 不可变编译运行与环境指针

`CompilationRunService` 在执行前重新生成 CompilePlan，并重新评估精确 Policy revision 与 waiver；调用方
传入的 plan 不能绕过 registry 或 Policy gate。`aof.compilation-run/v1` 固定 release/plan/policy/report
摘要、compiler lock、实际生效的 waiver、拓扑顺序产物、决策 ID 和 run digest。Run ID 写入后不可覆盖，
manifest 被修改会在读取时因摘要不匹配而拒绝。

首次 Run 只证明一次确定性编译，不能直接进入环境。`replay()` 必须使用相同 Release revisions、plan、
Policy report 与 waiver，在独立 run 目录重新编译；所有 `(target, compiler, content_hash)` 一致时，新 Run
才标记 `reproducible`。环境使用 `aof.compilation-channel/v1` 指针，不复制或覆盖产物：promotion 要求
可复现 Run、独立 approver 与 publisher；rollback 只能指向该 channel 历史上提升过且仍可验证的 Run。
每次执行、回放、提升和回滚都写入决策溯源账本，channel 保留带摘要的完整 pointer history。

## 签名编译控制面

REST 与 MCP 不各自实现编译逻辑，而是共同调用 `CompilerControlPlane`。边界只信任经
`SignedPrincipalVerifier` 验证的 subject、tenant 和 roles；请求体不能指定 actor。Release scope、所有
Resource ID 和 Policy ID 必须属于签名 tenant，Run repository 按 tenant 分区，所有编译、回放、审批、
提升和回滚决策也写入同一 tenant，避免跨租户先例与审计轨迹串线。

REST 提供 `/v1/semantic/compiler/{plan,evaluate,runs,runs/replay}` 与
`/v1/semantic/compiler/channels/{approvals,promote,rollback}`，并可读取不可变 Run 和 channel。MCP 提供
对应的 `aof_semantic_compile_*` tools，要求 gateway 将签名 Principal headers 放入
`principal_headers`。Run 请求必须回传 plan 阶段的 `expected_plan_digest`；promotion 必须引用由独立签名
reviewer 创建、且精确绑定 run/channel 的 approval decision，publisher 不能用请求体伪造审批人。

真实资产 E2E 会把仓库中的 Genshin OWL、mapping YAML、Datalog ruleset 与 OKF 文档编入同一个 Release，
通过签名 REST 完成六目标 plan、Policy gate、run、独立 replay、审批和 production pointer 提升，并检查
回放目录中的每一个实际产物文件。

## 统一可信查询计划

查询不能直接指定某个临时文件或未发布 Release。`QueryRequest` 只描述 channel、capability、query、purpose
与结构化 parameters；`TrustedSnapshotResolver` 将它解析成内容寻址的 `aof.query-plan/v1`：固定 tenant、
channel pointer/version、可复现 CompilationRun、Release、compiler Policy revision，以及 capability 所需的
精确 artifact 摘要。

当前 capability 与运行时 target 的最低依赖为：

- `semantic_search` → `rag`；
- `datalog` → `datalog`；
- `sparql` → `owl`；
- `query_template` → `semantic-json` + `mcp`。

Resolver 在返回 QueryPlan 前重新验证 channel pointer 与逐事件摘要/连续性、Run digest 与 tenant、独立 replay
状态、compiler lock、Release digest、artifact 路径边界和实际文件 SHA-256。缺失 target、跨租户、非可复现
Run 或任何字节篡改都会阻断。本 Slice 只建立可信 snapshot 与无副作用 QueryPlan；执行路由、QueryPolicy 和
查询决策审计由后续 Slice 消费该稳定契约。

## 类型化 Intent IR 与确定性 SQL

`SemanticIntent` 使用 Metric、Dimension 等稳定 Resource ID 表达查询意图，不接收物理表名、字段名或任意 SQL。
Intent 中的过滤条件是类型化 `IntentFilter`，按语义内容规范化，所有值在 SQL Plan 中转换为 `:pN` 参数；
输入顺序不会改变 intent digest 或 plan digest。反序列化会重新计算摘要，阻止 Agent 篡改已确认 Intent。

`SemanticSqlCompiler` 只从冻结的 PhysicalDataset、Metric 和 Dimension revisions 解析物理名称、聚合、度量字段
和分组字段。当前 ANSI 基线明确要求单一共享 Dataset，不会猜测 JOIN；未知资源、跨 Dataset 意图、不支持聚合和
不安全标识符全部阻断。输出 `aof.semantic-sql-plan/v1`，绑定 Intent digest、参数化 SQL、参数值和所有消费资源
revision。旧 mapping adapter 同步生成规范化 aggregation、measure、field 和 data_type，使既有资产进入同一编译路径。

## 统一确定性查询执行

`QueryExecutor` 只接受摘要自洽、且当前仍解析到同一 channel snapshot 的 QueryPlan。执行前会重新构造
QueryRequest、重新运行 TrustedSnapshotResolver，并比较完整 plan digest；改写 query、run、artifact 或
channel 已前移的旧计划都不能继续访问运行时文件。

四种 capability 共用 `aof.query-result/v1`，统一固定 request/plan/run/release digest、状态、data、artifact
evidence 与 result digest：

- `semantic_search` 在已验证 RAG bundle 上执行确定性文本检索与 limit；
- `datalog` 只运行 bundle 中编译并锁定的 RuleSet，facts 来自结构化 parameters，query 指定输出 predicate；
- `sparql` 合并已验证 OWL bundle，在不可变 snapshot 上执行只读 ASK/SELECT/CONSTRUCT/DESCRIBE；
- `query_template` 同时校验 semantic-json 中的 QueryTemplate revision 与 MCP catalog，再完成参数完整性检查和
  确定性渲染；它只返回 `executed: false`，不会把生成的 SQL 或其他命令直接发送给外部数据源。

该统一执行层不自行解释自然语言，也不允许调用方指定 artifact 路径。QueryPolicy、字段级授权和执行决策
溯源将在后续 Slice 包裹同一 QueryPlan/QueryResult，不改变各引擎的公共调用方式。

## 查询策略门禁

查询授权同样不是路由器中的硬编码。`QueryPolicy` 只接受 revision-addressed、`policy_type: query` 的 Policy
resource，并以 `role_capabilities` 和逐 capability rules 约束允许的 purpose、最大 limit、resource ID 与
requested fields。未授权 role、无效 Plan 与无效 limit 永远不可 waiver；其他风险只有被
`waiver_allowed_codes` 明确列出，并提供绑定精确 finding ID 与 Policy revision 的 `QueryPolicyWaiver` 才能解除。

`aof.query-policy-report/v1` 固定 plan、roles、全部 finding、waiver 状态与 report digest。每个获准 requested
field 生成独立、内容寻址的 field evidence，绑定 capability、Plan 和 Policy revision。`GovernedQueryExecutor`
只有在 report conforms 后才调用统一 QueryExecutor，并输出 `aof.governed-query-result/v1`，同时绑定原始
QueryResult、完整 Policy report 与 governed result digest；策略拒绝不会触碰运行时 artifact。

## 查询决策溯源与证据包

`AuditedQueryService` 将一次查询建模为可追溯的决策链：`semantic_query_plan` →
`semantic_query_policy` → `semantic_query_execute`。被实际采用的豁免以
`semantic_query_policy_waiver` 分支接入策略决策；查询计划还会在可用时连接产生该运行时快照的编译决策。

每次成功执行输出 `aof.query-evidence-package/v1`，整体绑定请求、计划、策略报告、治理后结果、编译制品证据、
字段授权证据以及 PROV-O 风格审计轨迹。证据包自身内容寻址，可离线检查篡改；查询执行决策带有 capability、
purpose 与 channel 标签，可直接用于先例查询和下游影响分析。

## 统一查询外部边界

`QueryControlPlane` 是 REST 与 MCP 共用的零信任入口。调用方必须提交签名 Principal、channel、capability、
purpose 和 QueryPolicy resource ID；tenant 与 roles 只取自验签后的 Principal。控制面不接受调用方上传 Policy，
而是从 QueryPlan 锁定的同一 CompilationRun 中额外校验 `semantic-json` 制品并加载已发布 Policy revision，避免
用临时宽松策略绕过治理。

REST `POST /v1/semantic/query` 与 MCP `aof_semantic_query` 使用同一控制面和返回契约，覆盖 semantic search、
确定性 semantic SQL、结构化 Graph traversal、Datalog、只读 SPARQL 和 QueryTemplate。每种能力均消费真实编译制品，并返回相同的 governed result、决策链与
`aof.query-evidence-package/v1`；签名错误、跨 tenant snapshot、未发布 Policy 或制品摘要不一致都会在执行前阻断。

成功查询会持久化为 `aof.query-run/v1`：不可变记录同时绑定规范化请求、CompilationRun、Release、QueryPlan、
Policy report、governed result、决策链和 evidence package，并由独立 key-id 的 HMAC attestation 签名。
`GET /v1/semantic/query-runs/{id}` 与 MCP `aof_semantic_query_get_run` 在返回前重新验签。严格回放必须提供源
`run_digest`；控制面还会重新解析 channel，并要求 plan、CompilationRun 和 Release 摘要与源 Run 完全一致，
因此环境指针移动后不会把“刷新执行”伪装成“原快照回放”。

`SqliteSemanticSqlExecutor` 是首个真实 SQL Executor SPI：只接受编译器生成的参数化 SQL，以只读连接执行，
支持显式 schema attachment，并对主库与 attachment 文件计算 `data_snapshot` 摘要。该摘要进入统一 evidence
envelope 和 QueryRun；严格回放除验证 Release/plan 外，还要求 governed result 与源 Run 一致，数据变化会被
识别为 refresh 而不是 replay。服务可通过 `AOF_QUERY_SQLITE_DATABASE` 和 JSON 对象形式的
`AOF_QUERY_SQLITE_ATTACHMENTS` 启用该后端，REST 与 MCP 仍共用同一个 Executor factory。

已通过身份验证的查询若在规划、策略或执行阶段失败，控制面写入签名的终态 `failed` QueryRun，并记录
`semantic_query_failed` 决策和规范化错误证据；失败记录不可覆盖。联邦执行器按拓扑顺序 fail-fast，并在错误中
固定失败 step ID，避免把部分结果表示为成功。

QueryPlan 还会从 Intent、QueryTemplate 或 capability 制品解析传递依赖闭包；非搜索能力在执行前对闭包中的
每个 Resource 做策略检查，semantic search 则在执行前形成可见 Resource/field scope，并在结果摘要计算前
执行投影。旧 `/v1/semantic/compile` 未锁定 Release、未执行 Policy 且允许 LLM 直接生成 SQL，因此所有服务
变体都固定返回 `410 Gone`，不提供可重新开启的公网旁路。

`QueryExecutorRegistry` 是 capability-keyed Executor SPI。默认执行器均通过同一注册接口接入，企业实现可替换
具体 SQL、图或检索后端而不改变 QueryPlan、Release pinning 和结果证据契约。`FederatedQueryRequest` 将多个
capability step 组织为显式依赖 DAG；Planner 拒绝未知依赖和环，并要求所有 step 锁定同一 CompilationRun 与
Release。`FederatedQueryExecutor` 按确定性拓扑顺序执行，输出 `aof.federated-query-result/v1`，集中绑定每个
子结果和去重后的编译制品 evidence。

## 生产运维闭环

生产模式由 `ProductionReadiness` 统一判定，而不是由各端点各自猜测。`AOF_RUNTIME_MODE=production` 时，
身份验签、Release 签名和 Query evidence 签名必须使用三个独立且足够强的 secret 及显式版本化 key ID，
同时要求可用的 OTLP exporter 与有效 SLO 文件。readiness 不序列化 secret；不符合时 HTTP 中间件只开放
liveness、metrics、readiness 和 SLO 诊断入口，其余业务流量 fail closed。未知 runtime mode 同样被阻断，
防止配置拼写错误静默降级为开发模式。

`SqliteReleaseRepository` 使用显式 schema version 1，支持并发幂等发布、`PRAGMA integrity_check` 加
逐 Manifest 摘要/租户/索引一致性扫描，以及 SQLite online backup。备份文件可直接由新 Repository
实例恢复并再次执行完整性验证；同一 tenant/release 的冲突摘要仍由事务唯一键阻断。

生产编译状态由 `SqliteCompilationRunRepository` 管理：不可变 CompilationRun 与 channel pointer 在
`BEGIN IMMEDIATE` 事务内提交，产物仍按内容摘要保存到文件系统。`verify_all()` 联合检查 SQLite、Run、
pointer history 和实际产物 bytes；`backup_to()` 使用 SQLite online backup 并复制产物树。生产 REST 与
MCP 的编译和查询控制面注入同一 Repository 类型，进程重启后继续解析相同 channel。QueryRun 仓库同样
串行化并发幂等写、提供 schema version、全库完整性扫描和 online backup。恢复验收必须先执行
`verify_all()`，不得把“数据库可打开”视为证据链完整。

真实 SQLite Executor 以 `QueryExecutionLimits` 执行：数据库和 attachment 只按 `mode=ro` 打开，完成
attachment 后安装 authorizer 拒绝 DDL、DML、PRAGMA、事务和再次 ATTACH；progress handler 同时执行
deadline、VM step budget 与协作式 cancellation 检查，结果读取使用 `max_rows + 1` 探测并在超限时整体失败，
不会返回伪装成功的截断结果。REST/MCP 共享 `AOF_QUERY_TIMEOUT_MS`、`AOF_QUERY_MAX_ROWS`、
`AOF_QUERY_MAX_VM_STEPS` 和 `AOF_QUERY_PROGRESS_INTERVAL`；非法值 fail closed。

`TrustedRuntimeTelemetry` 对 `compile.execute`、`release.promote` 和 `query.execute` 记录低基数
operation/status counter 与有界 latency samples。tenant、Release、CompilationRun、QueryRun、plan digest
只进入当前 OpenTelemetry span 的关联属性和诊断快照，不作为 Prometheus label，避免基数失控；raw query 与
secret 不被接收。`/v1/ops/trusted-runtime` 按 `trusted_runtime_slo` 输出结构化 breach alerts，`/metrics`
输出 `aof_trusted_*` 指标，Prometheus 规则对持续 breach 触发 critical 告警。生产 readiness 要求通用 HTTP
SLO 与 trusted runtime SLO 同时存在。

签名层统一使用 `DetachedSigningProvider`，Release 与 Query evidence 分别由
`ProviderReleaseAttestor`、`ProviderQueryEvidenceAttestor` 消费相同的小接口：读取 current key ID、
对规范化 bytes 签名、按 attestation key ID 验签。`LocalSigningKeyProvider` 是本地参考实现，轮换 current
key 后仍保留历史 key 验签，因此旧 Release 与 QueryRun 不会失去可验证性。

生产 KMS/HSM 通过 `ExternalSigningProvider` 接入；它只委托 `sign/verify`，没有读取 key material 的方法，
业务层和 attestor 从接口上都无法取得主密钥。外部签名失败会直接中止发布或 QueryRun 持久化，不降级到本地
密钥。旧 `ReleaseKeyProvider/RotatingReleaseAttestor` 保留兼容，新增代码统一使用 detached provider。
