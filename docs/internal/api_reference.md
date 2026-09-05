# AOF API 参考文档

> 维护提示（2026-09-05）：本长文以传统 Topic API 为主。企业语义控制面包括 `/v1/semantic/proposals/*`、`/v1/semantic/compiler/*`、`/v1/semantic/query*`、`/v1/knowledge/*`、`/v1/ontology/*` 受控操作与 `/v1/agentic/*`；请先阅读 [服务 API 文档](../../services/semantic_middle_layer_api/API.md)。传统 `/v1/reasoning/*` 不是签名多租户控制面。旧 `/v1/semantic/compile` 已退役且返回 `410 Gone`。

本文档提供 AOF Semantic Middle Layer API 的完整参考。

**基础信息**
- 基础 URL: `http://localhost:8787`
- API 版本: `v1`
- 内容类型: `application/json`

---

## 目录

1. [Ingest 数据摄取](#1-ingest-数据摄取)
2. [Sync 数据同步](#2-sync-数据同步)
3. [Analytics 图谱分析](#3-analytics-图谱分析)
4. [Build 构建](#4-build-构建)
5. [Semantic 语义层](#5-semantic-语义层)
6. [Dataset 数据集管理](#6-dataset-数据集管理)
7. [Visualize 可视化](#7-visualize-可视化)
8. [Feedback 反馈](#8-feedback-反馈)
9. [Export 导出](#9-export-导出)
10. [System 系统](#10-system-系统)

---

## 1. Ingest 数据摄取

### 1.1 增量同步

#### 执行增量摄取
```http
POST /v1/ingest/incremental
```

**请求体**
```json
{
  "directory": "/path/to/docs",
  "dataset_name": "my_docs",
  "recursive": true,
  "file_pattern": "*.txt",
  "dry_run": false
}
```

**响应**
```json
{
  "status": "success",
  "dataset_name": "my_docs",
  "dry_run": false,
  "changes": {
    "added": [{"path": "new_file.txt"}],
    "modified": [{"path": "changed.txt"}],
    "deleted": [{"path": "removed.txt"}],
    "renamed": []
  },
  "ingested_count": 5,
  "error_count": 0,
  "execution_time_ms": 1250,
  "message": "Ingested 5 files to Cognee"
}
```

#### 检测变化（预览）
```http
POST /v1/ingest/incremental/detect
```

**请求体**
```json
{
  "directory": "/path/to/docs",
  "dataset_name": "my_docs",
  "recursive": true
}
```

**响应**
```json
{
  "status": "success",
  "has_changes": true,
  "summary": {
    "added": 2,
    "modified": 1,
    "deleted": 0,
    "renamed": 0,
    "total": 3
  }
}
```

#### 获取增量统计
```http
GET /v1/ingest/incremental/stats?dataset_name=my_docs
```

**响应**
```json
{
  "status": "success",
  "stats": {
    "dataset_name": "my_docs",
    "has_state": true,
    "tracked_files": 150,
    "total_size_human": "25.50 MB",
    "last_updated": "2026-03-29T10:30:00"
  }
}
```

#### 重置增量状态
```http
DELETE /v1/ingest/incremental/state?dataset_name=my_docs
```

**响应**
```json
{
  "status": "success",
  "state_reset": true,
  "message": "State reset successfully, next ingestion will be full"
}
```

---

### 1.2 S3 摄取

#### 摄取单个 S3 文件
```http
POST /v1/ingest/s3/file
```

**请求体**
```json
{
  "bucket": "my-bucket",
  "key": "documents/report.pdf",
  "dataset_name": "reports",
  "endpoint_url": "https://minio.example.com",
  "region": "us-east-1"
}
```

**响应**
```json
{
  "status": "success",
  "bucket": "my-bucket",
  "key": "documents/report.pdf",
  "size": 154320,
  "content_type": "application/pdf",
  "dataset_name": "reports",
  "download_time_ms": 450
}
```

#### 批量摄取 S3 前缀
```http
POST /v1/ingest/s3/prefix
```

**请求体**
```json
{
  "bucket": "my-bucket",
  "prefix": "data/2024/",
  "pattern": "*.json",
  "dataset_name": "json_data",
  "max_concurrent": 5,
  "max_files": 100
}
```

**响应**
```json
{
  "status": "success",
  "total_objects": 50,
  "success_count": 48,
  "failed_count": 2,
  "total_bytes_human": "125.50 MB",
  "successful": [{"key": "data/1.json", "size": 1024}],
  "failed": [{"key": "data/bad.json", "error": "Invalid JSON"}]
}
```

#### S3 增量同步
```http
POST /v1/ingest/s3/sync
```

**请求体**
```json
{
  "bucket": "my-bucket",
  "prefix": "logs/",
  "pattern": "*.log",
  "dataset_name": "app_logs",
  "incremental": true
}
```

#### 列出 S3 桶
```http
GET /v1/ingest/s3/buckets?endpoint_url=https://minio.example.com
```

**响应**
```json
{
  "status": "success",
  "count": 3,
  "buckets": ["data", "documents", "backups"]
}
```

#### 列出 S3 对象
```http
GET /v1/ingest/s3/objects?bucket=my-bucket&prefix=data/&pattern=*.json&max_keys=100
```

**响应**
```json
{
  "status": "success",
  "count": 50,
  "objects": [
    {
      "key": "data/1.json",
      "size": 1024,
      "size_human": "1.00 KB",
      "last_modified": "2026-03-28T10:00:00",
      "etag": "abc123",
      "content_type": "application/json"
    }
  ]
}
```

---

### 1.3 URL 摄取

#### 摄取单个 URL
```http
POST /v1/ingest/url
```

**请求体**
```json
{
  "url": "https://example.com/document.html",
  "dataset_name": "web_docs",
  "timeout": 30
}
```

**响应**
```json
{
  "status": "success",
  "url": "https://example.com/document.html",
  "content_type": "text/html",
  "content_length": 15234,
  "title": "Example Document",
  "dataset_name": "web_docs",
  "download_time_ms": 245
}
```

#### 批量摄取 URL
```http
POST /v1/ingest/url/batch
```

**请求体**
```json
{
  "urls": [
    "https://example.com/page1.html",
    "https://example.com/page2.html"
  ],
  "dataset_name": "web_pages",
  "max_concurrent": 5
}
```

**响应**
```json
{
  "status": "success",
  "total": 2,
  "successful": 2,
  "failed": 0,
  "results": [
    {"url": "https://example.com/page1.html", "success": true},
    {"url": "https://example.com/page2.html", "success": true}
  ]
}
```

#### URL 摄取历史
```http
GET /v1/ingest/url/history?limit=50&success_only=true
```

#### URL 摄取统计
```http
GET /v1/ingest/url/stats
```

#### 清理 URL 临时文件
```http
DELETE /v1/ingest/url/cleanup?max_age_hours=24
```

---

### 1.4 批量目录摄取

#### 批量摄取目录
```http
POST /v1/ingest/batch
```

**请求体**
```json
{
  "directory": "/path/to/data",
  "dataset_name": "batch_data",
  "recursive": true,
  "file_pattern": "*.txt",
  "dry_run": false
}
```

**响应**
```json
{
  "status": "success",
  "dataset_name": "batch_data",
  "summary": {
    "total_files": 100,
    "success_count": 98,
    "error_count": 2
  },
  "errors": ["file1.txt: Permission denied"]
}
```

#### 预览批量摄取
```http
POST /v1/ingest/batch/preview
```

**请求体**
```json
{
  "directory": "/path/to/data",
  "recursive": true
}
```

**响应**
```json
{
  "status": "success",
  "preview": {
    "total_files": 100,
    "total_size": "50.5 MB",
    "by_extension": {".txt": 80, ".md": 20}
  }
}
```

#### 自动发现数据集
```http
POST /v1/ingest/discover
```

**请求体**
```json
{
  "directory": "/data",
  "recursive": true,
  "min_files": 5
}
```

**响应**
```json
{
  "status": "success",
  "dataset_count": 3,
  "datasets": [
    {
      "name": "documents",
      "path": "/data/docs",
      "file_count": 50,
      "total_size_human": "125.00 MB"
    }
  ]
}
```

---

### 1.5 基础摄取

#### 摄取文档
```http
POST /v1/ingest/docs
```

**请求体**
```json
{
  "topic": "my_topic",
  "docs_uri": "/path/to/documents"
}
```

#### 摄取元数据
```http
POST /v1/ingest/metadata
```

**请求体**
```json
{
  "topic": "my_topic",
  "metadata": {
    "title": "My Dataset",
    "version": "1.0"
  }
}
```

#### 摄取反馈
```http
POST /v1/ingest/feedback
```

**请求体**
```json
{
  "topic": "my_topic",
  "patches": [
    {"type": "add_class", "name": "NewConcept"}
  ]
}
```

---

## 2. Sync 数据同步

### 执行同步
```http
POST /v1/sync
```

**请求体**
```json
{
  "local_path": "/path/to/docs",
  "dataset_name": "my_docs",
  "direction": "to_cognee",
  "strategy": "merge",
  "incremental": true
}
```

**响应**
```json
{
  "success": true,
  "dataset_name": "my_docs",
  "direction": "to_cognee",
  "status": "completed",
  "summary": {
    "added": 5,
    "updated": 2,
    "deleted": 0,
    "conflicts": 0,
    "errors": 0
  },
  "execution_time_ms": 2500
}
```

### 添加定时同步任务
```http
POST /v1/sync/scheduled
```

**请求体**
```json
{
  "local_path": "/path/to/docs",
  "dataset_name": "my_docs",
  "interval_minutes": 60,
  "direction": "to_cognee",
  "strategy": "merge"
}
```

**响应**
```json
{
  "status": "success",
  "task_id": "abc123def456",
  "message": "Scheduled sync task created, runs every 60 minutes"
}
```

### 列出定时任务
```http
GET /v1/sync/scheduled
```

**响应**
```json
{
  "status": "success",
  "count": 2,
  "tasks": [
    {
      "task_id": "abc123",
      "local_path": "/path/to/docs",
      "dataset_name": "my_docs",
      "interval_minutes": 60,
      "last_sync": "2026-03-29T10:00:00",
      "next_sync": "2026-03-29T11:00:00"
    }
  ]
}
```

### 移除定时任务
```http
DELETE /v1/sync/scheduled/{task_id}
```

### 获取同步状态
```http
GET /v1/sync/status?dataset_name=my_docs
```

**响应**
```json
{
  "status": "success",
  "sync_status": {
    "dataset_name": "my_docs",
    "strategy": "merge",
    "cognee_available": true,
    "last_sync": "2026-03-29T10:00:00",
    "sync_count": 5
  }
}
```

---

## 3. Analytics 图谱分析

### 计算完整图谱指标
```http
POST /v1/analytics/metrics
```

**请求体**
```json
{
  "dataset_name": "my_docs",
  "compute_communities": true
}
```

**响应**
```json
{
  "status": "success",
  "metrics": {
    "dataset_name": "my_docs",
    "computed_at": "2026-03-29T10:00:00",
    "statistics": {
      "node_count": 150,
      "edge_count": 320,
      "density": 0.014,
      "avg_degree": 4.2,
      "connected_components": 1
    },
    "top_nodes": [
      {
        "node_id": "concept_1",
        "label": "Machine Learning",
        "pagerank": 0.085,
        "degree": 25,
        "community": 0
      }
    ],
    "communities": [
      {
        "community_id": 0,
        "node_count": 45,
        "density": 0.12,
        "top_nodes": [...]
      }
    ]
  }
}
```

### PageRank 分析
```http
POST /v1/analytics/pagerank
```

**请求体**
```json
{
  "dataset_name": "my_docs",
  "top_k": 20,
  "alpha": 0.85
}
```

**响应**
```json
{
  "status": "success",
  "dataset_name": "my_docs",
  "top_k": 20,
  "nodes": [
    {
      "node_id": "concept_1",
      "label": "Machine Learning",
      "pagerank": 0.085,
      "degree": 25
    }
  ]
}
```

### 社区检测
```http
POST /v1/analytics/communities
```

**请求体**
```json
{
  "dataset_name": "my_docs",
  "algorithm": "louvain",
  "resolution": 1.0
}
```

**响应**
```json
{
  "status": "success",
  "dataset_name": "my_docs",
  "algorithm": "louvain",
  "community_count": 5,
  "communities": [
    {
      "community_id": 0,
      "node_count": 45,
      "density": 0.12,
      "avg_pagerank": 0.015,
      "top_nodes": [...]
    }
  ]
}
```

### 中心性分析
```http
POST /v1/analytics/centrality
```

**请求体**
```json
{
  "dataset_name": "my_docs",
  "centrality_type": "betweenness",
  "top_k": 20
}
```

**类型**: `degree`, `betweenness`, `closeness`, `eigenvector`, `pagerank`

### 最短路径
```http
POST /v1/analytics/shortest_path
```

**请求体**
```json
{
  "dataset_name": "my_docs",
  "source": "node_a",
  "target": "node_z"
}
```

**响应**
```json
{
  "status": "success",
  "dataset_name": "my_docs",
  "source": "node_a",
  "target": "node_z",
  "exists": true,
  "path": ["node_a", "node_b", "node_c", "node_z"],
  "length": 3
}
```

### 获取基础统计
```http
GET /v1/analytics/statistics?dataset_name=my_docs
```

**响应**
```json
{
  "status": "success",
  "dataset_name": "my_docs",
  "statistics": {
    "node_count": 150,
    "edge_count": 320,
    "density": 0.014,
    "avg_degree": 4.2,
    "max_degree": 25,
    "min_degree": 1,
    "connected_components": 1
  }
}
```

---

## 4. Build 构建

### 构建主题本体
```http
POST /v1/build/topic
```

**请求体**
```json
{
  "topic": "my_topic",
  "max_iterations": 3,
  "skip_align": false
}
```

**响应**
```json
{
  "run_id": "my_topic_20260329_123456",
  "manifest": "/path/to/manifest.json",
  "topic": "my_topic"
}
```

---

## 5. Semantic 语义层

### 3.1 检索与编译

#### 语义检索
```http
POST /v1/semantic/retrieve
```

**请求体**
```json
{
  "topic": "my_topic",
  "query": "revenue by quarter",
  "top_k": 10,
  "use_semantic": true
}
```

**响应**
```json
{
  "topic": "my_topic",
  "query": "revenue by quarter",
  "hits": [
    {
      "type": "metric",
      "key": "quarterly_revenue",
      "score": 0.95
    }
  ],
  "total_matches": 5
}
```

#### SQL 编译
```http
POST /v1/semantic/compile
```

**请求体**
```json
{
  "topic": "my_topic",
  "intent": "Calculate total revenue by quarter for 2024",
  "target": "sql",
  "context": {}
}
```

**响应**
```json
{
  "topic": "my_topic",
  "intent": "Calculate total revenue by quarter for 2024",
  "generated_sql": "SELECT quarter, SUM(revenue) FROM sales WHERE year=2024 GROUP BY quarter",
  "context": {...}
}
```

#### 查询评估
```http
POST /v1/semantic/evaluate
```

**请求体**
```json
{
  "topic": "my_topic",
  "candidate": "SELECT * FROM sales",
  "criteria": ["syntax", "performance", "correctness"]
}
```

**响应**
```json
{
  "topic": "my_topic",
  "candidate": "SELECT * FROM sales",
  "score": 0.75,
  "risks": [
    {"type": "select_star", "severity": "medium"}
  ],
  "suggestions": ["Specify required columns"]
}
```

### 3.2 增强搜索

#### 获取搜索配置
```http
POST /v1/semantic/search/enhanced
```

**请求体**
```json
{
  "topic": "my_topic",
  "query": "What are the main concepts?",
  "search_type": "auto",
  "user_intent": "analytics",
  "top_k": 10
}
```

**响应**
```json
{
  "topic": "my_topic",
  "query": "What are the main concepts?",
  "search_config": {
    "search_type": "auto",
    "selected_type": "GRAPH_COMPLETION",
    "user_intent": "analytics",
    "top_k": 10
  },
  "available_search_types": {
    "base": ["graph_completion", "rag_completion", "chunks", "summaries"],
    "advanced": ["graph_completion_cot", "temporal", "code", "cypher"]
  }
}
```

#### 执行搜索
```http
POST /v1/semantic/search/execute
```

**请求体**
```json
{
  "topic": "my_topic",
  "query": "Show me all relationships",
  "search_type": "graph_completion",
  "top_k": 10
}
```

**响应**
```json
{
  "status": "success",
  "search_type_used": "GRAPH_COMPLETION",
  "result_count": 5,
  "results": [...],
  "execution_time_ms": 850
}
```

---

## 6. Dataset 数据集管理

### 4.1 数据集操作

#### 列出数据集
```http
GET /v1/datasets?topic=my_topic
```

**响应**
```json
{
  "status": "success",
  "count": 5,
  "datasets": [
    {
      "id": "ds_123",
      "name": "my_docs",
      "data_count": 150,
      "status": "ready"
    }
  ]
}
```

#### 获取数据集状态
```http
GET /v1/datasets/{dataset_id}/status?pipeline_name=cognify_pipeline
```

**响应**
```json
{
  "status": "success",
  "dataset_id": "ds_123",
  "state": "completed",
  "progress": 100,
  "message": "Processing completed"
}
```

#### 列出数据项
```http
GET /v1/datasets/{dataset_id}/data?limit=100
```

**响应**
```json
{
  "status": "success",
  "dataset_id": "ds_123",
  "count": 50,
  "data_items": [
    {
      "id": "item_1",
      "name": "document.txt",
      "mime_type": "text/plain"
    }
  ]
}
```

#### 删除/清空数据集
```http
DELETE /v1/datasets/{dataset_id}?mode=empty
```

**模式**
- `mode=empty` - 清空数据集（保留结构）
- `mode=data_item` + `data_id=xxx` - 删除特定数据项

---

## 7. Visualize 可视化

### 生成可视化
```http
POST /v1/visualize/generate
```

**请求体**
```json
{
  "topic": "my_topic",
  "open_browser": false
}
```

**响应**
```json
{
  "status": "success",
  "html_path": "/path/to/viz.html",
  "file_size_human": "2.50 MB",
  "preview_url": "file:///path/to/viz.html"
}
```

### 列出可视化文件
```http
GET /v1/visualize/list?topic=my_topic
```

### 打开可视化
```http
GET /v1/visualize/open/{filename}
```

---

## 8. Feedback 反馈

### 提交搜索反馈
```http
POST /v1/feedback/search
```

**请求体**
```json
{
  "topic": "my_topic",
  "query": "revenue analysis",
  "search_results": [...],
  "feedback_score": 4,
  "feedback_text": "Results were helpful but could include more recent data",
  "used_graph_elements": {
    "node_ids": ["n1", "n2"],
    "edge_ids": ["e1"]
  }
}
```

**响应**
```json
{
  "status": "recorded",
  "message": "Feedback recorded for ontology improvement analysis"
}
```

---

## 9. Export 导出

### 导出 OWL
```http
GET /v1/export/owl?topic=my_topic
```

**响应**
```json
{
  "topic": "my_topic",
  "owl": "/path/to/ontology.owl"
}
```

### 导出映射
```http
GET /v1/export/mapping?topic=my_topic
```

### 导出回归测试
```http
GET /v1/export/regression?topic=my_topic
```

---

## 10. System 系统

### 健康检查
```http
GET /healthz
```

**响应**
```json
{
  "status": "ok",
  "version": "0.2.0",
  "llm_configured": "yes"
}
```

### 获取构建产物
```http
GET /v1/artifacts/{run_id}
```

---

## 错误处理

所有 API 错误返回统一格式：

```json
{
  "detail": "Error message description"
}
```

**HTTP 状态码**
- `200` - 成功
- `400` - 请求参数错误
- `404` - 资源不存在
- `500` - 服务器内部错误

---

## 环境变量配置

### S3 配置
```bash
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_REGION=us-east-1
export S3_ENDPOINT_URL=https://minio.example.com  # S3-compatible
```

### LLM 配置
```bash
export LLM_API_KEY=your_key
export LLM_PROVIDER=custom
export LLM_MODEL=deepseek/deepseek-chat
export LLM_ENDPOINT=https://api.deepseek.com/v1
```

---

## 客户端示例

### Python
```python
import requests

# 增量摄取
response = requests.post("http://localhost:8787/v1/ingest/incremental", json={
    "directory": "/path/to/docs",
    "dataset_name": "my_docs"
})
result = response.json()
print(f"Ingested {result['ingested_count']} files")

# 执行搜索
response = requests.post("http://localhost:8787/v1/semantic/search/execute", json={
    "topic": "my_topic",
    "query": "What are the main concepts?"
})
results = response.json()
```

### cURL
```bash
# 增量摄取
curl -X POST http://localhost:8787/v1/ingest/incremental \
  -H "Content-Type: application/json" \
  -d '{"directory": "/data/docs", "dataset_name": "docs"}'

# S3 摄取
curl -X POST http://localhost:8787/v1/ingest/s3/prefix \
  -H "Content-Type: application/json" \
  -d '{"bucket": "my-bucket", "prefix": "data/", "pattern": "*.json"}'
```
