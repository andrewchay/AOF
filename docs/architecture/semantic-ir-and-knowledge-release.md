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
