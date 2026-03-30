# Agentic Ontology Factory (AOF)

AOF 是一个**企业级知识工程平台**，支持从多源数据中自动构建、管理和分析知识图谱。基于 Cognee 知识图谱引擎，提供完整的数据摄取、本体构建、智能搜索和图谱分析能力。

---

## 🌟 核心特性

### 数据摄取（5 大来源）
- 📁 **本地文件** - 批量目录摄取，自动发现数据集
- 🌐 **URL 网络** - HTTP/HTTPS 内容下载，自动 MIME 检测
- ☁️ **S3 云存储** - AWS S3 / MinIO / Ceph 等兼容服务
- 🗄️ **数据库** - PostgreSQL / MySQL / SQLite Schema 提取
- 📝 **混合文档** - Jupyter, Markdown, XML, YAML 代码+标注

### 智能同步
- 🔄 **增量更新** - 智能差异检测，只处理变化文件（Blake2b 指纹）
- ⏰ **定时同步** - 后台调度，支持多种冲突策略
- 📊 **变更追踪** - 新增/修改/删除/重命名检测

### 知识图谱分析
- 📈 **PageRank** - 节点重要性分析
- 🏘️ **社区检测** - Louvain / 标签传播 / 贪心模块度
- 🎯 **中心性分析** - 度/介数/接近/特征向量中心性
- 🔍 **路径分析** - 最短路径查找
- 📉 **统计指标** - 密度、直径、聚类系数等

### 智能搜索（14 种类型）
```
基础搜索:    graph_completion, rag_completion, chunks, summaries
高级搜索:    graph_completion_cot, temporal, code, cypher
探索搜索:    feeling_lucky, natural_language, triplet_completion
```

### 企业级功能
- ✅ **5 层质量门** - L1 文本 → L2 语法 → L3 数据 → L4 逻辑 → L5 对齐
- 📊 **语义中间层** - 统一处理文档、元数据和反馈
- 🔄 **反馈闭环** - Memify 用户反馈分析，自动生成本体改进建议
- 📈 **可视化** - 交互式 HTML 图谱可视化

---

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         API 层 (51 端点)                         │
├─────────────────────────────────────────────────────────────────┤
│  Ingest  │  Sync  │  Analytics  │  Search  │  Visualize  │ ... │
│  (20)    │  (5)   │   (6)       │   (6)    │    (3)      │     │
├─────────────────────────────────────────────────────────────────┤
│                      Bridge 层 (11 模块)                         │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│ incremental_ │   s3_        │   data_      │  graph_            │
│ loader.py    │ ingestion.py │ sync.py      │  analytics.py      │
├──────────────┼──────────────┼──────────────┼────────────────────┤
│ url_         │   batch_     │   enhanced_  │  graph_            │
│ ingestion.py │ ingestion.py │ search.py    │  visualizer.py     │
├──────────────┴──────────────┴──────────────┴────────────────────┤
│                      Cognee 知识图谱引擎                         │
└─────────────────────────────────────────────────────────────────┘
```

### 模块清单

| 模块 | 功能 | 状态 |
|------|------|------|
| `enhanced_search.py` | 14 种搜索类型语义检索 | ✅ |
| `incremental_loader.py` | 智能差异检测，增量更新 | ✅ |
| `url_ingestion.py` | URL 网络数据摄取 | ✅ |
| `s3_ingestion.py` | S3 云存储摄取 | ✅ |
| `data_sync.py` | 双向同步，定时调度 | ✅ |
| `graph_analytics.py` | PageRank, 社区检测, 中心性分析 | ✅ |
| `dataset_manager.py` | Cognee 数据集 CRUD | ✅ |
| `graph_visualizer.py` | 知识图谱可视化 | ✅ |
| `batch_ingestion.py` | 批量目录摄取 | ✅ |
| `memify_feedback_loop.py` | 用户反馈分析 | ✅ |
| `database_schema_extractor.py` | 数据库 Schema 提取 | ✅ |

---

## 🚀 快速开始

### 1. 环境准备

```bash
# 创建虚拟环境
cd /path/to/AOF
uv venv --python 3.13 .venv
source .venv/bin/activate

# 安装依赖
uv pip install -e /path/to/cognee
uv pip install boto3        # S3 支持（可选）
uv pip install networkx     # 图谱分析（可选）
uv pip install aiohttp      # URL 摄取（可选）

# 环境检查
python aof_doctor.py --spec aof_spec.example.json
```

### 2. 配置

```bash
# LLM 配置（必需）
export LLM_API_KEY="your-key"
export LLM_PROVIDER="custom"
export LLM_MODEL="deepseek/deepseek-chat"
export LLM_ENDPOINT="https://api.deepseek.com/v1"

# S3 配置（可选）
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
export S3_ENDPOINT_URL="https://minio.example.com"  # S3-compatible
```

---

## 📖 使用指南

### CLI 工具

#### 1. 数据摄取

```bash
# 增量摄取本地文档
python aof_add.py \
  --spec aof_spec.example.json \
  --data-path /path/to/docs \
  --skip-preflight

# 批量摄取目录
python -c "
import asyncio
from bridge.batch_ingestion import BatchIngestor

async def main():
    ingestor = BatchIngestor()
    result = await ingestor.ingest_directory('/data/docs', 'my_dataset')
    print(f'Ingested: {result.success_count}/{result.total_files}')

asyncio.run(main())
"
```

#### 2. 本体构建

```bash
# 从 SQL 生成本体
export LLM_API_KEY="your-key"

python tools/ontology_factory/build_testdata_ontology_factory.py \
  --input tools/data_adapter/samples/inventory.sql \
  --topic inventory \
  --max-iterations 4

# 从数据库提取 Schema
export DATABASE_URL="postgresql://user:pass@localhost/mydb"

python tools/extract_db_schema.py \
  --db-url "$DATABASE_URL" \
  --output ontologies/db_schema.owl \
  --mode merge
```

#### 3. 构建知识图谱

```bash
# 执行 cognify（构建图谱）
python aof_run.py \
  --spec aof_spec.example.json \
  --run-cognify \
  --skip-preflight

# 质量检查
python aof_run.py \
  --spec aof_spec.example.json \
  --run-quality
```

---

### API 服务

启动 FastAPI 服务：

```bash
services/semantic_middle_layer_api/run_api.sh

# 或使用 uvicorn 直接启动
uvicorn services.semantic_middle_layer_api.app:app \
  --host 0.0.0.0 \
  --port 8787 \
  --reload
```

访问文档：`http://127.0.0.1:8787/docs`

---

## 🔌 API 参考

### 数据摄取（20 端点）

```bash
# 增量摄取
POST /v1/ingest/incremental
{
  "directory": "/data/docs",
  "dataset_name": "my_docs",
  "recursive": true,
  "dry_run": false
}

# S3 摄取
POST /v1/ingest/s3/prefix
{
  "bucket": "my-bucket",
  "prefix": "data/2024/",
  "pattern": "*.json",
  "dataset_name": "json_data"
}

# URL 摄取
POST /v1/ingest/url
{
  "url": "https://example.com/doc.html",
  "dataset_name": "web_docs"
}
```

### 数据同步（5 端点）

```bash
# 执行同步
POST /v1/sync
{
  "local_path": "/data/docs",
  "dataset_name": "my_docs",
  "direction": "to_cognee",
  "strategy": "merge",
  "incremental": true
}

# 定时同步
POST /v1/sync/scheduled
{
  "local_path": "/data/docs",
  "dataset_name": "my_docs",
  "interval_minutes": 60
}
```

### 图谱分析（6 端点）

```bash
# 完整分析
POST /v1/analytics/metrics
{"dataset_name": "my_docs"}

# PageRank
POST /v1/analytics/pagerank
{
  "dataset_name": "my_docs",
  "top_k": 20
}

# 社区检测
POST /v1/analytics/communities
{
  "dataset_name": "my_docs",
  "algorithm": "louvain"
}

# 最短路径
POST /v1/analytics/shortest_path
{
  "dataset_name": "my_docs",
  "source": "node_a",
  "target": "node_z"
}
```

### 智能搜索（6 端点）

```bash
# 执行搜索
POST /v1/semantic/search/execute
{
  "topic": "my_topic",
  "query": "主要概念是什么？",
  "search_type": "auto",
  "user_intent": "analytics",
  "top_k": 10
}

# 语义检索
POST /v1/semantic/retrieve
{
  "topic": "my_topic",
  "query": "revenue by quarter",
  "top_k": 10
}
```

**完整 API 文档**: [docs/internal/api_reference.md](docs/internal/api_reference.md)

---

## 📚 使用案例

### 案例 1：企业知识库构建

```python
import asyncio
from bridge.incremental_loader import IncrementalLoader
from bridge.s3_ingestion import S3Ingester
from bridge.data_sync import DataSync

async def build_knowledge_base():
    # 1. 从 S3 摄取历史文档
    s3 = S3Ingester()
    await s3.ingest_prefix(
        bucket="company-docs",
        prefix="knowledge-base/",
        dataset_name="enterprise_kb"
    )
    
    # 2. 增量更新本地文档
    loader = IncrementalLoader("enterprise_kb")
    result = await loader.ingest_incremental("/data/local-docs")
    print(f"Changes: +{len(result.change_set.added)} ~{len(result.change_set.modified)}")
    
    # 3. 设置定时同步
    sync = DataSync("enterprise_kb")
    # 每小时同步一次

asyncio.run(build_knowledge_base())
```

### 案例 2：智能问答系统

```python
import asyncio
from bridge.enhanced_search import search_with_intent

async def qa_system():
    # 用户问题
    query = "过去30天最活跃的5个用户是谁？"
    
    # 自动选择搜索类型
    result = await search_with_intent(
        query=query,
        user_intent="analytics",  # analytics/debugging/temporal/code
        top_k=5
    )
    
    print(f"Search type: {result.search_type_used}")
    for item in result.results:
        print(f"- {item}")

asyncio.run(qa_system())
```

### 案例 3：图谱分析与可视化

```python
import asyncio
from bridge.graph_analytics import GraphAnalytics
from bridge.graph_visualizer import GraphVisualizer

async def analyze_graph():
    # 1. 分析图谱
    analyzer = GraphAnalytics("my_dataset")
    metrics = await analyzer.compute_all_metrics()
    
    print(f"Nodes: {metrics.statistics.node_count}")
    print(f"Communities: {len(metrics.communities)}")
    
    # Top 重要节点
    for node in metrics.top_nodes[:5]:
        print(f"  {node.label}: PR={node.pagerank:.4f}")
    
    # 2. 生成可视化
    viz = GraphVisualizer()
    result = await viz.visualize_for_topic("my_dataset")
    print(f"Visualization: {result.html_path}")

asyncio.run(analyze_graph())
```

### 案例 4：数据管道自动化

```bash
#!/bin/bash
# 自动化数据管道

DATASET="sales_data"
LOCAL_DIR="/data/sales"
S3_BUCKET="company-sales"

# 1. 从 S3 同步新数据
curl -X POST http://localhost:8787/v1/ingest/s3/sync \
  -H "Content-Type: application/json" \
  -d "{
    \"bucket\": \"$S3_BUCKET\",
    \"prefix\": \"2024/\",
    \"dataset_name\": \"$DATASET\",
    \"incremental\": true
  }"

# 2. 同步本地补充数据
curl -X POST http://localhost:8787/v1/sync \
  -H "Content-Type: application/json" \
  -d "{
    \"local_path\": \"$LOCAL_DIR\",
    \"dataset_name\": \"$DATASET\",
    \"direction\": \"to_cognee\",
    \"incremental\": true
  }"

# 3. 执行分析
curl -X POST http://localhost:8787/v1/analytics/metrics \
  -H "Content-Type: application/json" \
  -d "{\"dataset_name\": \"$DATASET\"}"

# 4. 生成可视化
curl -X POST http://localhost:8787/v1/visualize/generate \
  -H "Content-Type: application/json" \
  -d "{\"topic\": \"$DATASET\"}"
```

---

## ⚙️ 配置详解

### Spec 文件

```json
{
  "project_root": "/path/to/AOF",
  "dataset": "my_dataset",
  "runtime": {
    "run_in_background": false,
    "incremental_loading": true,
    "data_per_batch": 20,
    "retries": 2
  },
  "ontology": {
    "file": "/path/to/ontology.owl",
    "matching_cutoff": 0.8
  },
  "cognee": {
    "root": "/path/to/cognee"
  }
}
```

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `LLM_API_KEY` | LLM 服务 API 密钥 | ✅ 执行时 |
| `LLM_PROVIDER` | `openai` / `custom` | ✅ 执行时 |
| `LLM_MODEL` | 模型名称 | ✅ 执行时 |
| `LLM_ENDPOINT` | 自定义端点 | provider=custom 时 |
| `AWS_ACCESS_KEY_ID` | AWS 访问密钥 | S3 时 |
| `AWS_SECRET_ACCESS_KEY` | AWS 密钥 | S3 时 |
| `AWS_REGION` | AWS 区域 | S3 时 |
| `S3_ENDPOINT_URL` | S3 兼容端点 | MinIO 等 |

---

## 📁 项目结构

```
AOF/
├── bridge/                    # Bridge 层（11 模块）
│   ├── enhanced_search.py     # 增强搜索
│   ├── incremental_loader.py  # 增量加载
│   ├── url_ingestion.py       # URL 摄取
│   ├── s3_ingestion.py        # S3 摄取
│   ├── data_sync.py           # 数据同步
│   ├── graph_analytics.py     # 图谱分析
│   ├── dataset_manager.py     # 数据集管理
│   ├── graph_visualizer.py    # 图谱可视化
│   ├── batch_ingestion.py     # 批量摄取
│   ├── memify_feedback_loop.py # 反馈循环
│   └── database_schema_extractor.py # 数据库提取
├── docs/
│   ├── internal/
│   │   ├── api_reference.md   # API 完整文档
│   │   └── bridge_modules.md  # 模块文档
│   └── ...
├── services/
│   └── semantic_middle_layer_api/  # FastAPI 服务
│       └── app.py             # 51 端点
├── tools/
│   ├── ontology_factory/      # 本体工厂
│   ├── data_adapter/          # 数据适配
│   ├── quality_gate/          # 质量门 L1-L5
│   └── middle_layer/          # 语义中间层
├── tests/                     # 测试套件
├── ontologies/                # 生成的本体
└── logs/                      # 运行日志
```

---

## 🧪 开发

### 运行测试

```bash
# 全部测试
python -m pytest tests/ -v

# 特定测试
python -m pytest tests/test_api_integration.py -v

# 覆盖率
python -m pytest tests/ --cov=bridge --cov-report=html
```

### 代码质量

```bash
# 运行质量门
python tools/quality_gate/run_all_lints.py

# L1-L5 检查
python tools/quality_gate/lint_l1_text_integrity.py
python tools/quality_gate/lint_l2_syntax.py
python tools/quality_gate/lint_l3_data_integrity.py
```

---

## 📈 性能指标

| 指标 | 数值 |
|------|------|
| API 端点 | 51 |
| Bridge 模块 | 11 |
| 搜索类型 | 14 |
| 社区算法 | 3 |
| 中心性类型 | 5 |
| 质量层级 | L1-L5 |
| 测试覆盖率 | 65/65 (100%) |

---

## 📖 更多文档

- [API 参考](docs/internal/api_reference.md) - 完整 API 文档
- [模块文档](docs/internal/bridge_modules.md) - Bridge 层详解
- [方法论体系](docs/internal/methodology/) - AOF 方法规范
- [架构设计](docs/architecture/bridge-design.md) - 技术架构
- [通用本体抽提 SOP](docs/AOF-通用本体抽提SOP.md) - 操作手册
- [生产发布清单](docs/生产发布清单.md) - 部署检查

---

## 🤝 贡献

欢迎提交 Issue 和 PR！

---

## 📄 License

[Your License Here]

---

## 🙏 致谢

- 知识图谱引擎：[cognee](https://github.com/topoteretes/cognee) (Apache-2.0)
- 图谱分析：[NetworkX](https://networkx.org/)
- 搜索与 LLM 集成：OpenAI / DeepSeek
