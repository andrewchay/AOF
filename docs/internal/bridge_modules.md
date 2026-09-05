# AOF Bridge 模块文档

本文档记录 AOF 的 Bridge 层模块。传统 Cognee 适配能力与 P0–P2 受治理语义运行时并存；企业知识发布和消费以受治理路径为准。

## 模块概览

| 模块 | 功能 | 状态 |
|------|------|------|
| `enhanced_search.py` | 多搜索类型语义检索 | ✅ 完成 |
| `url_ingestion.py` | URL 网络数据摄取 | ✅ 完成 |
| `s3_ingestion.py` | S3 云存储摄取 | ✅ 完成 |
| `incremental_loader.py` | 增量更新支持 | ✅ 完成 |
| `data_sync.py` | 数据同步与调度 | ✅ 完成 |
| `graph_analytics.py` | 图谱指标分析 | ✅ 完成 |
| `dataset_manager.py` | Cognee 数据集管理 | ✅ 完成 |
| `graph_visualizer.py` | 知识图谱可视化 | ✅ 完成 |
| `batch_ingestion.py` | 批量目录摄取 | ✅ 完成 |
| `memify_feedback_loop.py` | Memify 反馈循环 | ✅ 完成 |
| `database_schema_extractor.py` | 数据库模式提取 | ✅ 完成 |
| `semantic_core/` | 语义资源、release、编译、查询/动作回执、连续摄入 | ✅ P0–P2 |
| `document_parser/` | 受白名单约束的文档解析、标准化与来源上下文 | ✅ P1 |
| `ontology_governance/` | OWL/SKOS/SHACL、草案/发布、版本化 Datalog | ✅ P2 |
| `decision_provenance.py` | 决策与证据的 append-only 哈希链 | ✅ P0 |

## 受治理运行时（P0–P2）

规范生命周期为：`source snapshot -> SemanticResource -> proposal -> validate -> approve/waiver -> compile -> signed release -> release-pinned query/action receipt`。图、向量、SQL、Skill、RAG 与 MCP 均为同一 release 的投影或能力。

该路径由 `bridge.semantic_core.identity.SignedPrincipalVerifier` 验证签名主体，按租户和角色实施职责分离；请求体中的 actor 不能作为授权来源。传统模块（下文）可生成候选资产，但不能绕过这条发布门禁。

---

## 传统集成模块（兼容）

---

## Enhanced Search (`enhanced_search.py`)

提供完整的 Cognee 搜索能力封装，支持 12+ 种搜索类型。

### 支持的搜索类型

```python
# 基础搜索
GRAPH_COMPLETION          # 自然语言问答，使用完整图谱上下文
RAG_COMPLETION            # 传统 RAG，使用文档块
CHUNKS                    # 原始文本段检索
SUMMARIES                 # 分层内容摘要

# 高级搜索
GRAPH_COMPLETION_COT      # Chain-of-Thought 推理搜索
GRAPH_COMPLETION_CONTEXT_EXTENSION  # 上下文扩展的图谱搜索
GRAPH_SUMMARY_COMPLETION  # 基于图谱摘要的搜索
TRIPLET_COMPLETION        # 三元组补全
TEMPORAL                  # 时序感知搜索
CODING_RULES              # 代码特定搜索
CYPHER                    # 原生 Cypher 图查询
NATURAL_LANGUAGE          # 自然语言处理搜索
CHUNKS_LEXICAL            # 词汇级块搜索
FEELING_LUCKY             # 自动选择最佳搜索类型
```

### 使用示例

```python
from bridge.enhanced_search import search_with_intent, SearchType

# 自动选择搜索类型
result = await search_with_intent(
    query="What are the main concepts in the document?",
    user_intent="analytics",  # 可选：analytics, debugging, temporal, code
    top_k=10,
)

# 指定搜索类型
result = await search_with_intent(
    query="Show me all relationships",
    search_type=SearchType.GRAPH_COMPLETION_COT,
)
```

### API 端点

- `POST /v1/semantic/search/enhanced` - 获取搜索配置
- `POST /v1/semantic/search/execute` - 执行搜索

---

## URL Ingestion (`url_ingestion.py`)

支持从 URL 下载并摄取网络内容到 Cognee。

### 功能特性

- ✅ HTTP/HTTPS URL 内容下载
- ✅ 自动 MIME 类型检测
- ✅ 支持多种内容类型：HTML, PDF, JSON, XML, Markdown, 代码文件等
- ✅ 批量 URL 并发摄取
- ✅ 下载历史记录
- ✅ 统计信息收集
- ✅ 临时文件自动清理

### 支持的内容类型

```python
# 文本
text/plain, text/html, text/markdown, text/csv

# 文档
application/pdf, application/msword, application/vnd.openxmlformats-officedocument.wordprocessingml.document

# 数据
application/json, application/jsonl, application/yaml, application/xml

# 代码
text/x-python, text/javascript, application/javascript
```

### 使用示例

```python
from bridge.url_ingestion import URLIngester, ingest_url

# 便捷函数 - 单个 URL
result = await ingest_url(
    url="https://example.com/article",
    dataset_name="my_dataset",
    timeout=30,
)

# 批量摄取
results = await ingest_urls(
    urls=["https://a.com", "https://b.com"],
    max_concurrent=5,
)

# 高级用法
ingester = URLIngester()
result = await ingester.ingest(url="https://example.com")

# 获取统计和历史
stats = ingester.get_statistics()
history = ingester.get_history(limit=50)

# 清理临时文件
deleted = ingester.cleanup_temp_files(max_age_hours=24)
```

### API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/ingest/url` | POST | 摄取单个 URL |
| `/v1/ingest/url/batch` | POST | 批量摄取 URL |
| `/v1/ingest/url/history` | GET | 获取历史 |
| `/v1/ingest/url/stats` | GET | 获取统计 |
| `/v1/ingest/url/cleanup` | DELETE | 清理临时文件 |

---

## S3 Ingestion (`s3_ingestion.py`)

支持从 AWS S3 或 S3-compatible 服务（MinIO, Ceph 等）摄取数据。

### 功能特性

- ✅ AWS S3 原生支持
- ✅ S3-compatible 服务（MinIO, Ceph, Wasabi 等）
- ✅ 单文件/批量前缀摄取
- ✅ 增量同步（基于 ETag）
- ✅ 流式下载（支持大文件）
- ✅ 并发下载控制
- ✅ 文件名模式匹配

### 支持的内容类型

```python
# 文档
.txt, .md, .pdf, .doc, .docx, .rst

# 数据
.json, .jsonl, .csv, .tsv, .yaml, .yml, .xml

# 代码（37+ 种语言）
.py, .js, .ts, .java, .c, .cpp, .go, .rs, .rb, .php, .swift, .kt...

# 配置
.toml, .ini, .cfg, .conf
```

### 使用示例

```python
from bridge.s3_ingestion import S3Ingester, S3Config, ingest_s3_file, ingest_s3_prefix

# 从环境变量配置
config = S3Config.from_env()

# 或显式配置
config = S3Config(
    endpoint_url="https://minio.example.com",  # S3-compatible 服务
    region="us-east-1",
    access_key="AKIA...",
    secret_key="...",
)

# 摄取单个文件
result = await ingest_s3_file(
    bucket="my-bucket",
    key="documents/report.pdf",
    dataset_name="reports",
    config=config,
)

# 批量摄取前缀
result = await ingest_s3_prefix(
    bucket="my-bucket",
    prefix="data/2024/",
    pattern="*.json",
    dataset_name="json_data",
    max_concurrent=5,
    max_files=100,
)

# 增量同步（只下载变化的文件）
result = await ingester.sync_prefix(
    bucket="my-bucket",
    prefix="logs/",
    dataset_name="logs",
    incremental=True,
)
```

### API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/ingest/s3/file` | POST | 摄取单个 S3 文件 |
| `/v1/ingest/s3/prefix` | POST | 批量摄取前缀 |
| `/v1/ingest/s3/sync` | POST | 增量同步前缀 |
| `/v1/ingest/s3/buckets` | GET | 列出所有桶 |
| `/v1/ingest/s3/objects` | GET | 列出对象 |

### 环境变量配置

```bash
# AWS S3
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

# S3-compatible 服务（如 MinIO）
export S3_ENDPOINT_URL=https://minio.example.com
```

---

## Incremental Loader (`incremental_loader.py`)

实现智能差异检测，只处理变化的文件，大幅提升重复摄取效率。

### 功能特性

- ✅ 文件内容指纹（Blake2b 哈希）
- ✅ 变化类型检测（新增/修改/删除/重命名）
- ✅ 大文件采样哈希（避免内存问题）
- ✅ 状态持久化（JSON 格式）
- ✅ 预览模式（dry run）
- ✅ 自动忽略临时文件

### 变更类型

```python
ADDED      = "added"       # 新增文件
MODIFIED   = "modified"    # 内容修改
DELETED    = "deleted"     # 文件删除
UNCHANGED  = "unchanged"   # 未变化
RENAMED    = "renamed"     # 重命名（检测到的）
```

### 使用示例

```python
from bridge.incremental_loader import IncrementalLoader, ingest_incremental

# 初始化加载器
loader = IncrementalLoader(dataset_name="my_docs")

# 检测变化（不执行摄取）
changes = await loader.detect_changes("/path/to/docs")
print(f"Added: {len(changes.added)}")
print(f"Modified: {len(changes.modified)}")
print(f"Deleted: {len(changes.deleted)}")

# 执行增量摄取
result = await loader.ingest_incremental(
    directory="/path/to/docs",
    recursive=True,
    dry_run=False,  # 设为 True 预览不执行
)

# 便捷函数
result = await ingest_incremental(
    directory="/path/to/docs",
    dataset_name="my_docs",
    recursive=True,
)

# 重置状态（强制下次全量）
loader.reset_state()

# 查看统计
stats = loader.get_stats()
print(f"Tracked files: {stats['tracked_files']}")
```

### API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/ingest/incremental` | POST | 执行增量摄取 |
| `/v1/ingest/incremental/detect` | POST | 仅检测变化 |
| `/v1/ingest/incremental/stats` | GET | 获取统计 |
| `/v1/ingest/incremental/state` | DELETE | 重置状态 |

### 状态文件位置

```
~/.aof/incremental_state/{dataset_name}_fingerprints.json
```

---

## Data Sync (`data_sync.py`)

实现本地与 Cognee 之间的双向同步，支持定时调度和冲突解决。

### 功能特性

- ✅ 双向同步（本地 ↔ Cognee）
- ✅ 多种冲突策略（本地优先、云端优先、合并、手动）
- ✅ 增量/全量同步
- ✅ 定时任务调度
- ✅ 同步历史记录

### 同步方向

```python
TO_COGNEE = "to_cognee"         # 本地 → Cognee（上传）
FROM_COGNEE = "from_cognee"     # Cognee → 本地（导出）
BIDIRECTIONAL = "bidirectional" # 双向同步
```

### 冲突策略

```python
LOCAL_FIRST = "local_first"     # 本地版本优先
COGNEE_FIRST = "cognee_first"   # Cognee 版本优先
MERGE = "merge"                 # 尝试自动合并
MANUAL = "manual"               # 记录冲突，手动解决
```

### 使用示例

```python
from bridge.data_sync import DataSync, sync_to_cognee, SyncDirection, SyncStrategy

# 创建同步器
sync = DataSync(
    dataset_name="my_docs",
    strategy=SyncStrategy.MERGE,
)

# 同步本地到 Cognee
result = await sync.sync_to_cognee(
    local_path="/path/to/docs",
    incremental=True,
)

# 导出从 Cognee
result = await sync.export_from_cognee(
    export_dir="/path/to/export",
    format="json",
)

# 双向同步
result = await sync.sync_bidirectional("/path/to/docs")

# 定时同步调度
from bridge.data_sync import SyncScheduler

scheduler = SyncScheduler()
task_id = scheduler.add_task(
    local_path="/path/to/docs",
    dataset_name="my_docs",
    direction=SyncDirection.TO_COGNEE,
    interval_minutes=60,  # 每小时同步
)

# 列出定时任务
tasks = scheduler.list_tasks()

# 移除任务
scheduler.remove_task(task_id)
```

### API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/sync` | POST | 执行数据同步 |
| `/v1/sync/scheduled` | POST | 添加定时任务 |
| `/v1/sync/scheduled` | GET | 列出定时任务 |
| `/v1/sync/scheduled/{id}` | DELETE | 移除定时任务 |
| `/v1/sync/status` | GET | 获取同步状态 |

---

## Graph Analytics (`graph_analytics.py`)

提供知识图谱的深度分析能力，包括 PageRank、社区检测、中心性分析等。

### 功能特性

- ✅ PageRank 节点重要性
- ✅ 社区检测（Louvain, Label Propagation）
- ✅ 中心性分析（度/介数/接近/特征向量）
- ✅ 图谱统计（节点/边数、密度、直径等）
- ✅ 最短路径分析
- ✅ 导出 GEXF（Gephi 格式）

### 社区检测算法

```python
LOUVAIN = "louvain"                    # Louvain 算法
LABEL_PROPAGATION = "label_propagation" # 标签传播
GREEDY_MODULARITY = "greedy_modularity" # 贪心模块度
```

### 中心性类型

```python
DEGREE = "degree"           # 度中心性（连接数）
BETWEENNESS = "betweenness" # 介数中心性（桥梁作用）
CLOSENESS = "closeness"     # 接近中心性（距离和）
EIGENVECTOR = "eigenvector" # 特征向量中心性（邻居重要性）
PAGERANK = "pagerank"       # PageRank（谷歌算法）
```

### 使用示例

```python
from bridge.graph_analytics import (
    GraphAnalytics, 
    CommunityAlgorithm,
    CentralityType,
    analyze_graph,
)

# 创建分析器
analyzer = GraphAnalytics(dataset_name="my_docs")

# 计算所有指标
metrics = await analyzer.compute_all_metrics()
print(f"节点数: {metrics.statistics.node_count}")
print(f"边数: {metrics.statistics.edge_count}")
print(f"密度: {metrics.statistics.density}")

# PageRank Top 节点
pagerank = await analyzer.pagerank(top_k=20)
for node in pagerank[:5]:
    print(f"{node.node_id}: {node.pagerank:.4f}")

# 社区检测
communities = await analyzer.detect_communities(
    algorithm=CommunityAlgorithm.LOUVAIN
)
for comm in communities[:5]:
    print(f"Community {comm.community_id}: {comm.node_count} nodes")

# 中心性分析
degree = await analyzer.compute_centrality(CentralityType.DEGREE)
betweenness = await analyzer.compute_centrality(CentralityType.BETWEENNESS)

# 最短路径
path = await analyzer.find_shortest_path("node_a", "node_z")
if path.exists:
    print(f"Path length: {path.length}")
    print(f"Path: {' -> '.join(path.path)}")

# 导出指标
files = analyzer.export_metrics(metrics, Path("./output"))
print(f"Exported: {files}")

# 便捷函数
metrics = await analyze_graph("my_docs", compute_communities=True)
```

### API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/analytics/metrics` | POST | 计算完整指标 |
| `/v1/analytics/pagerank` | POST | PageRank 分析 |
| `/v1/analytics/communities` | POST | 社区检测 |
| `/v1/analytics/centrality` | POST | 中心性分析 |
| `/v1/analytics/shortest_path` | POST | 最短路径 |
| `/v1/analytics/statistics` | GET | 基础统计 |

### 依赖安装

```bash
pip install networkx
pip install python-louvain  # 可选，用于 Louvain 社区检测
```

---

## Dataset Manager (`dataset_manager.py`)

提供 Cognee 数据集的 CRUD 操作。

### 功能

- 列出所有数据集
- 获取数据集处理状态
- 删除/清空数据集
- 删除特定数据项
- 列出数据集内的数据项

### API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/datasets` | GET | 列出数据集 |
| `/v1/datasets/{id}/status` | GET | 获取状态 |
| `/v1/datasets/{id}/data` | GET | 列出数据项 |
| `/v1/datasets/{id}` | DELETE | 清空/删除数据集 |

---

## Graph Visualizer (`graph_visualizer.py`)

生成知识图谱的 HTML 可视化。

### 功能

- 生成交互式 HTML 可视化
- 支持多种布局算法
- 浏览器直接查看
- 可视化历史管理

### API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/visualize/generate` | POST | 生成可视化 |
| `/v1/visualize/list` | GET | 列出生成的文件 |
| `/v1/visualize/open/{filename}` | GET | 在浏览器中打开 |

---

## Batch Ingestion (`batch_ingestion.py`)

批量摄取目录中的文件。

### 功能

- 递归目录扫描
- 自动数据集发现
- 文件类型过滤
- 预览模式（dry run）
- 批量进度追踪

### API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/ingest/batch` | POST | 执行批量摄取 |
| `/v1/ingest/batch/preview` | POST | 预览模式 |
| `/v1/ingest/discover` | POST | 自动发现数据集 |

---

## Memify Feedback Loop (`memify_feedback_loop.py`)

用户反馈收集和本体改进建议生成。

### 功能

- 搜索反馈收集
- 反馈数据分析
- 改进建议生成
- 候选变更导出

### API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v1/feedback/search` | POST | 提交搜索反馈 |

---

## Database Schema Extractor (`database_schema_extractor.py`)

从数据库提取模式并转换为 OWL 本体。

### 功能

- 支持 PostgreSQL, MySQL, SQLite
- 表/列/外键提取
- 自动类型映射
- 增量更新策略
- 冲突解决

### 使用示例

```python
from bridge.database_schema_extractor import extract_and_update_ontology

result = await extract_and_update_ontology(
    db_config={
        "type": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "mydb",
        "user": "user",
        "password": "pass",
    },
    ontology_file=Path("ontology.owl"),
    mode="merge",  # merge, replace, diff
)
```

---

## 配置参数

### URL Ingestion

```python
URLIngester(
    temp_dir=Path("/tmp/aof_url_ingest"),  # 临时文件目录
)

# 类级别常量
MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024  # 50MB
DEFAULT_TIMEOUT = 30  # 秒
```

### S3 Ingestion

```python
S3Ingester(
    config=S3Config(
        endpoint_url="https://minio.example.com",  # S3-compatible
        region="us-east-1",
        access_key="...",
        secret_key="...",
    ),
    temp_dir=Path("/tmp/aof_s3_ingest"),
)

# 类级别常量
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
STREAMING_THRESHOLD = 10 * 1024 * 1024  # 10MB
```

### Incremental Loader

```python
IncrementalLoader(
    dataset_name="my_docs",
    state_dir=Path("~/.aof/incremental_state"),
    ignore_patterns={"*.tmp", "*.swp", ".git"},
)
```

### Enhanced Search

```python
IntentClassifier(
    provider="deepseek",
    model="deepseek-chat",
)
```

---

## 错误处理

所有模块都遵循统一的错误处理模式：

```python
try:
    result = await some_operation()
except Exception as e:
    # 返回包含错误信息的结果对象
    return ResultType(
        success=False,
        error=str(e),
    )
```

API 层统一使用 HTTPException 返回 500 状态码和错误详情。

---

## 性能优化

### URL Ingestion

- 支持并发下载（默认 max_concurrent=5）
- 临时文件自动清理
- 大文件限制（50MB）

### S3 Ingestion

- 流式下载大文件（>10MB）
- 并发下载控制
- 增量同步（避免重复下载）
- ETag 变化检测

### Incremental Loader

- 大文件采样哈希（避免内存问题）
- 指纹缓存（避免重复计算）
- 状态持久化（JSON）

### Data Sync

- 增量同步（基于文件指纹）
- 后台定时调度
- 异步执行

### Graph Analytics

- 延迟加载（需要时才构建图）
- 图缓存（避免重复加载）
- 采样计算（大图的中心性）
- 导出多种格式（JSON, CSV, GEXF）

### Enhanced Search

- 异步执行
- 结果缓存（计划）
- 智能搜索类型选择

### Batch Ingestion

- 递归目录扫描优化
- 文件类型预过滤
- 预览模式避免重复工作
