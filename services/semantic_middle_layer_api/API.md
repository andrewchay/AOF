# AOF Semantic Middle Layer API

> 当前版本：传统 topic API + P0–P2 受治理语义控制面。本文旧版示例中的 `POST /v1/semantic/compile` 已不再生成 SQL，固定返回 `410 Gone`。

## 认证与租户边界

受治理端点（`/v1/semantic/proposals/*`、`/v1/semantic/compiler/*`、`/v1/semantic/query*`、`/v1/knowledge/*`、`/v1/ontology/*` 的受控操作）要求由 `AOF_SEMANTIC_IDENTITY_SECRET` 验证的 HMAC principal headers：

```text
x-aof-principal-subject
x-aof-principal-tenant
x-aof-principal-roles
x-aof-principal-timestamp
x-aof-principal-key-id
x-aof-principal-signature
```

主体有效期为五分钟。服务根据签名的主体确定 tenant 和动作角色；请求体中 `actor`、`approver` 或 `reviewer` 不能越权。缺失、过期或无效签名返回 `401`；未配置身份验证器返回 `503`。

## 受治理核心端点

| 端点 | 用途 | 成功状态 |
|---|---|---|
| `POST /v1/semantic/proposals` | 创建 evidence-bearing 资源提案 | 201 |
| `POST /v1/semantic/proposals/{id}/validate` | 运行语义/本体门禁 | 200 |
| `POST /v1/semantic/proposals/{id}/approve` | 独立审批 | 200 |
| `POST /v1/semantic/proposals/{id}/compile` | 生成指定 release targets | 200 |
| `POST /v1/semantic/proposals/{id}/publish` | 发布已批准的 release | 200 |
| `POST /v1/semantic/compiler/runs` | 按计划执行编译 | 201 |
| `POST /v1/semantic/compiler/runs/replay` | 独立重放编译 | 201 |
| `POST /v1/semantic/compiler/channels/promote` | 推进已批准 channel 指针 | 200 |
| `POST /v1/semantic/query` | 按 release/channel 和策略执行查询 | 200 |
| `POST /v1/semantic/query-runs/replay` | 重放查询 | 201 |
| `POST /v1/knowledge/sources` | 注册连续知识来源 | 201 |
| `POST /v1/knowledge/sources/{id}/ingest` | 摄入来源快照 | 201 |

### `POST /v1/semantic/query`

```json
{
  "channel": "production",
  "capability": "semantic_search",
  "query": "customer",
  "purpose": "weekly-growth-review",
  "policy_resource_id": "aof://tenant/platform/policy/query-production",
  "rationale": "Generate a governed weekly metric.",
  "parameters": {"limit": 10}
}
```

响应应被作为回执保存：其中的 release、query run、证据与 digest 才是可解释消费的依据。

SQL 的标准协议要求 `parameters.intent` 为 `SemanticIntent.to_dict()`，`query` 为该 intent digest。Agentic 的自然语言模式只解析已发布 metric/dimension 名称和 aliases；时间过滤、派生计算需要显式 typed intent。自然语言请求不能直接替代 SQL 意图。

### Agentic 查询

`POST /v1/agentic/runs` 要求 `run_id`、`session_id`、`query`、`purpose`、`channel`、`release_id`、`release_digest`、`policy_resource_id`、`allowed_capabilities`。返回计划、结果及底层 query receipt、证据引用、summary 和 decision ID。调用示例与限制见 [Agentic 运行手册](../../docs/operations/agentic-system-runbook.md)。重复 run ID 返回 409；执行失败持久化 failed 状态，不发布答案。

`POST /v1/agentic/runs/{id}/replay` 接收新 `run_id`，保存 `source_run_id` 和 `reproducible`，不追加会话记忆。`GET /v1/agentic/runs/{id}/evaluation` 检查结构、引用和计划完整性，分数不表示业务答案准确率。

`query_contract_id` 可选择同 release 已发布的业务问题合同，支持 SQL→规则→分析 Skill→待审批动作的依赖计划。动作执行走 `/v1/semantic/action-runs/{id}/approve`、`/execute`，`POST /v1/agentic/runs/{id}/refresh-actions` 同步回执。动作运行禁止直接 replay。

完整 schema 由 `python tools/export_openapi.py` 从应用生成，存于 `docs/api/semantic_middle_layer.openapi.yaml`，CI 检查漂移。

### 本体与规则端点

`/v1/ontology/drafts/*` 管理草案、验证、waiver、审批、发布与影响预览；`/v1/ontology/releases/{ontology_id}/{version}/sparql` 只查询已发布 release。传统 `/v1/reasoning/rulesets/*` 尚非签名多租户接口；企业规则查询应使用受治理 `/v1/semantic/query` 的 datalog capability。

---

## 传统 Topic API（兼容参考）

增强版 FastAPI 服务，提供本体构建、语义检索、SQL 生成和查询评估能力。

## 版本

- **当前版本**: 0.2.0
- **增强特性**: LLM 驱动的 SQL 生成、智能评估、映射库集成

## 启动服务

```bash
# 设置环境变量
export LLM_API_KEY="your-api-key"
export LLM_PROVIDER="custom"
export LLM_MODEL="deepseek/deepseek-chat"
export LLM_ENDPOINT="https://api.deepseek.com/v1"

# 启动服务
./run_api.sh

# 或使用 Python
cd services/semantic_middle_layer_api
python -m uvicorn app:app --host 0.0.0.0 --port 8787 --reload
```

## 端点文档

启动后访问: http://localhost:8787/docs

## 核心端点

### 数据摄取

#### POST /v1/ingest/docs
摄取文档数据。

```json
{
  "topic": "users",
  "docs_uri": "/path/to/docs"
}
```

#### POST /v1/ingest/metadata
摄取元数据。

```json
{
  "topic": "users",
  "metadata": {"version": "1.0", "source": "db"}
}
```

#### POST /v1/ingest/feedback
摄取反馈补丁。

```json
{
  "topic": "users",
  "patches": [
    {"action": "add", "term": "user_id", "type": "class"}
  ]
}
```

### 构建

#### POST /v1/build/topic
构建主题本体和映射。

```json
{
  "topic": "users",
  "max_iterations": 3,
  "skip_align": false
}
```

### 语义层（增强）

#### POST /v1/semantic/retrieve
智能语义检索。

```json
{
  "topic": "users",
  "query": "active users",
  "top_k": 10,
  "use_semantic": true
}
```

**响应**:
```json
{
  "topic": "users",
  "query": "active users",
  "hits": [
    {
      "type": "term",
      "key": "user_status",
      "data": {"name": "user_status", "description": "..."},
      "score": 0.95
    }
  ],
  "total_matches": 5
}
```

#### POST /v1/semantic/compile（已退役）
不再生成 SQL，固定返回：

```json
{"detail":{"code":"legacy_semantic_compile_retired","message":"Ungoverned semantic compilation is retired","replacement":"/v1/semantic/query","capability":"semantic_sql"}}
```

状态码：`410 Gone`。受治理查询请使用本文件顶部的 `/v1/semantic/query`。

#### POST /v1/semantic/evaluate
评估查询质量。

```json
{
  "topic": "users",
  "candidate": "SELECT * FROM users",
  "criteria": ["syntax", "performance", "correctness"]
}
```

**响应**:
```json
{
  "topic": "users",
  "candidate": "SELECT * FROM users",
  "score": 0.7,
  "risks": [
    {
      "type": "select_star",
      "severity": "medium",
      "message": "Avoid SELECT *, specify columns explicitly"
    }
  ],
  "suggestions": ["Specify required columns instead of SELECT *"],
  "llm_evaluation": {...}
}
```

### 导出

#### GET /v1/export/owl?topic=users
导出 OWL 本体文件路径。

#### GET /v1/export/mapping?topic=users
导出映射库文件列表。

#### GET /v1/export/regression?topic=users
导出回归测试工件。

### 健康检查

#### GET /healthz
```json
{
  "status": "ok",
  "version": "0.2.0",
  "llm_configured": "yes"
}
```

## 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `LLM_API_KEY` | LLM API 密钥 | 是（SQL 生成） |
| `LLM_PROVIDER` | LLM 提供商 | 否（默认: custom） |
| `LLM_MODEL` | LLM 模型 | 否（默认: deepseek/deepseek-chat） |
| `LLM_ENDPOINT` | LLM 端点 | 否（默认: DeepSeek） |
| `AOF_ROOT` | AOF 项目根目录 | 否（自动检测） |

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Service                      │
├─────────────────────────────────────────────────────────┤
│  Ingest Layer    │  Build Layer    │  Semantic Layer   │
│  ─────────────   │  ───────────    │  ─────────────    │
│  /ingest/docs    │  /build/topic   │  /semantic/...    │
│  /ingest/metadata│                 │                   │
│  /ingest/feedback│                 │                   │
├─────────────────────────────────────────────────────────┤
│  Mapping Library │  LLM Client     │  Rule Engine      │
│  Ontology Store  │  SQL Generator  │  Risk Detector    │
└─────────────────────────────────────────────────────────┘
```

## 特性对比

| 特性 | 旧版 (v0.1) | 新版 (v0.2) |
|------|-------------|-------------|
| 检索 | 简单文本匹配 | 智能语义匹配 + 评分 |
| SQL 生成 | 固定模板 | LLM 驱动 + 上下文感知 |
| 评估 | 简单规则 | 规则 + LLM 双评估 |
| 建议 | 无 | 详细改进建议 |

## 测试

```bash
# 运行 API 测试
pytest tests/test_api_integration.py tests/test_api_enhanced.py -v

# 运行所有测试
pytest tests/ -v
```

## 注意事项

1. **LLM 依赖**: SQL 生成需要配置 LLM_API_KEY
2. **评估可用性**: 评估端点无需 manifest 即可运行基础规则检查
3. **映射库**: 检索和编译依赖映射库，需先运行构建流程
