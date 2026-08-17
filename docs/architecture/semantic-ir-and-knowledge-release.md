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
