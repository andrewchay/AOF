# AOF Semantic Middle Layer API

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

#### POST /v1/semantic/compile
旧版未治理 SQL 生成入口已退役，固定返回 `410 Gone`。调用方必须迁移到
`POST /v1/semantic/query`，使用 `capability: "semantic_sql"` 和已发布 Release 中的
类型化 Intent resource ID；SQL、依赖闭包、权限与证据由可信查询控制面确定。

```json
{
  "detail": {
    "code": "legacy_semantic_compile_retired",
    "message": "Ungoverned semantic compilation is retired",
    "replacement": "/v1/semantic/query",
    "capability": "semantic_sql"
  }
}
```

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
