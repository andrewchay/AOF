# Agentic Ontology Factory (AOF) v2.0

AOF 是一个**企业级知识工程平台**，支持从多源数据中自动构建、管理和分析知识图谱。基于 Cognee 知识图谱引擎，提供完整的数据摄取、本体构建、智能搜索、图谱分析和**企业级安全**能力。

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

---

## 🔐 企业级功能（v2.0 新增）

### 身份与访问管理（IAM）
- **RBAC 权限模型** - 4 级角色（admin/owner/editor/viewer）
- **资源级授权** - 支持 dataset/ontology/system/audit 细粒度控制
- **JWT + API Key** 双认证模式
- **请求签名验证** - HMAC-SHA256 防篡改

### 多租户隔离
- **租户级数据隔离** - GraphSpace 级别隔离（NebulaGraph）
- **资源配额管理** - 数据集数量、图谱节点数、并发查询限制
- **租户状态管理** - active/suspended/deleted 生命周期

### 审计与合规
- **全链路审计日志** - 5 级日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- **敏感数据脱敏** - 自动识别 password/token/api_key 并脱敏
- **审计报告生成** - 支持 JSON/CSV/HTML/PDF 格式导出
- **异常行为检测** - 登录异常、高频失败操作监控

---

## ⚡ 性能与高可用（v2.0 新增）

### 多级缓存体系
| 层级 | 实现 | 适用场景 |
|------|------|----------|
| L1 | 进程内 LRU | 热数据、单机部署 |
| L2 | Redis | 分布式共享、跨进程 |
| L3 | 物化视图 | 预计算结果（PageRank/社区统计） |

### 弹性保护机制
- **限流** - 令牌桶（允许突发）/ 滑动窗口（精确计数）
- **熔断器** - CLOSED/OPEN/HALF_OPEN 三态自动切换
- **重试策略** - 指数退避 + 抖动，支持条件重试

### 异步任务队列
- **优先级队列** - 4 级优先级（CRITICAL/HIGH/NORMAL/LOW）
- **状态追踪** - pending/queued/running/success/failure/cancelled
- **进度回调** - 实时进度更新（0-100%）
- **自动重试** - 失败任务自动重试（可配置次数）

---

## 🏗️ 架构演进

### 整体架构（v2.0）

```
┌─────────────────────────────────────────────────────────────────┐
│                        API Gateway 层                            │
│  Auth (JWT/API Key) → Rate Limit → Circuit Breaker → Router      │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                        API 层 (51 端点)                          │
├─────────────────────────────────────────────────────────────────┤
│  Ingest │ Sync │ Analytics │ Search │ Visualize │ Tasks │ Audit │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                      Bridge 层（模块化）                          │
├─────────────────────────────────────────────────────────────────┤
│  auth/          RBAC 权限、租户管理                               │
│  audit/         审计日志、合规报告                                │
│  cache/         L1/L2 缓存、物化视图                              │
│  resilience/    限流、熔断、重试                                  │
│  storage/       存储抽象层（Cognee/NebulaGraph）                  │
│  tasks/         异步任务队列                                      │
│  tenant/        多租户隔离                                        │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                      存储后端（可插拔）                           │
├─────────────────────────────────────────────────────────────────┤
│  Cognee (默认)  │  NebulaGraph (分布式)                          │
│  - 单机/轻量    │  - 水平扩展                                     │
│  - 快速原型     │  - 亿级节点支持                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 存储层抽象

```python
from bridge.storage import StorageFactory, StorageConfig

# 切换存储后端
config = StorageConfig(backend_type="nebula")  # 或 "cognee"
backend = StorageFactory.create(config)

# 统一接口
await backend.add_triples(triples)
result = await backend.pagerank(top_k=100)
```

---

## 📦 模块清单

### 核心模块

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

### 企业级模块（v2.0）

| 模块 | 功能 | 状态 |
|------|------|------|
| `auth/rbac.py` | RBAC 权限管理 | ✅ |
| `auth/models.py` | 用户/角色/权限模型 | ✅ |
| `audit/logger.py` | 审计日志记录 | ✅ |
| `audit/query.py` | 审计查询与报告 | ✅ |
| `tenant/manager.py` | 租户管理 | ✅ |
| `tenant/context.py` | 租户上下文 | ✅ |
| `tenant/middleware.py` | 租户识别中间件 | ✅ |
| `cache/local_cache.py` | L1 LRU 缓存 | ✅ |
| `cache/redis_cache.py` | L2 Redis 缓存 | ✅ |
| `cache/manager.py` | 多级缓存管理 | ✅ |
| `cache/materialized_view.py` | 物化视图 | ✅ |
| `resilience/rate_limiter.py` | 限流器 | ✅ |
| `resilience/circuit_breaker.py` | 熔断器 | ✅ |
| `resilience/retry.py` | 重试策略 | ✅ |
| `storage/base.py` | 存储抽象接口 | ✅ |
| `storage/factory.py` | 存储工厂 | ✅ |
| `storage/nebula_backend.py` | NebulaGraph 后端 | ✅ |
| `storage/ngql_builder.py` | nGQL 构建器 | ✅ |
| `tasks/queue.py` | 异步任务队列 | ✅ |
| `tasks/models.py` | 任务数据模型 | ✅ |

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
uv pip install redis        # 缓存支持（可选）

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

# 存储后端切换（可选，默认 Cognee）
export STORAGE_BACKEND="nebula"
export NEBULA_HOST="127.0.0.1"
export NEBULA_PORT="9669"
export NEBULA_USER="root"
export NEBULA_PASSWORD="nebula"
export NEBULA_SPACE="aof_default"

# Redis 缓存（可选）
export REDIS_URL="redis://localhost:6379/0"
```

### 3. 启动服务

```bash
# 开发模式
services/semantic_middle_layer_api/run_api.sh

# 生产模式（Docker）
docker build -t aof:latest .
docker run -p 8787:8787 aof:latest

# Kubernetes 部署
kubectl apply -f k8s/
```

---

## 📖 使用指南

### 认证示例

```python
from bridge.auth import RBACManager, RoleType

# 初始化 RBAC
rbac = RBACManager(db_session)

# 创建用户
user = await rbac.create_user(
    username="john",
    email="john@example.com",
    tenant_id="acme_corp"
)

# 授权角色
await rbac.grant_role(
    user_id=user.id,
    role_id=f"{tenant_id}_admin",
    resource_type=ResourceType.DATASET,
    resource_id="dataset_123"
)

# 检查权限
has_access = await rbac.check_permission(
    user_id=user.id,
    resource_type=ResourceType.DATASET,
    action=Action.READ,
    resource_id="dataset_123"
)
```

### 缓存使用

```python
from bridge.cache import CacheManager

# 创建缓存管理器
cache = CacheManager(
    l1_cache=LocalCache(max_size=10000),
    l2_cache=await RedisCache.from_url("redis://localhost")
)

# 读取或计算
result = await cache.get_or_compute(
    key="pagerank:dataset_123",
    compute_func=lambda: compute_pagerank(dataset),
    l1_ttl=300,   # L1 缓存 5 分钟
    l2_ttl=1800   # L2 缓存 30 分钟
)
```

### 异步任务

```python
from bridge.tasks import TaskQueue, Task, TaskPriority

# 启动队列
queue = TaskQueue()
await queue.start()

# 提交任务
task = Task(
    task_type="pagerank",
    dataset_name="my_graph",
    priority=TaskPriority.HIGH,
    parameters={"top_k": 100}
)
task_id = await queue.submit(task)

# 查询状态
status = await queue.get_status(task_id)

# 获取结果
result = await queue.get_result(task_id)
```

### 限流与熔断

```python
from bridge.resilience import RateLimiter, CircuitBreaker

# 限流 - 每分钟 100 次
limiter = RateLimiter(rate=100, per=60)
if await limiter.allow("user_123"):
    # 处理请求
    pass

# 熔断器
breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30.0
)

try:
    result = await breaker.call(
        fetch_external_api,
        fallback=default_value
    )
except CircuitBreakerOpen:
    # 熔断中，使用降级逻辑
    pass
```

---

## 🧪 测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行特定模块测试
python -m pytest tests/test_auth_rbac.py -v
python -m pytest tests/test_cache.py -v
python -m pytest tests/test_resilience.py -v
python -m pytest tests/test_storage.py -v
python -m pytest tests/test_tasks.py -v

# 覆盖率报告
python -m pytest tests/ --cov=bridge --cov-report=html
```

**当前测试状态**: 203 tests ✅ 全部通过

---

## 📊 性能指标

| 指标 | 数值 |
|------|------|
| API 端点 | 51 |
| Bridge 模块 | 20+ |
| 搜索类型 | 14 |
| 社区算法 | 3 |
| 中心性类型 | 5 |
| 质量层级 | L1-L5 |
| 测试覆盖率 | 203 tests |
| 支持存储后端 | 2 (Cognee/NebulaGraph) |
| 缓存层级 | 3 (L1/L2/物化视图) |

---

## 🏭 生产部署

### Docker Compose

```yaml
version: '3.8'
services:
  aof-api:
    image: aof:latest
    ports:
      - "8787:8787"
    environment:
      - STORAGE_BACKEND=nebula
      - NEBULA_HOST=nebula-graphd
    depends_on:
      - nebula-graphd
      - redis
  
  nebula-graphd:
    image: vesoft/nebula-graphd:v3.8.0
    # ...
  
  redis:
    image: redis:7-alpine
```

### Kubernetes

```bash
# 一键部署
kubectl apply -f k8s/

# 查看状态
kubectl get pods -n aof
kubectl get svc -n aof

# 水平扩缩容
kubectl scale deployment aof-api --replicas=5 -n aof
```

---

## 📚 更多文档

- [API 参考](docs/internal/api_reference.md) - 完整 API 文档
- [模块文档](docs/internal/bridge_modules.md) - Bridge 层详解
- [架构设计](docs/architecture/bridge-design.md) - 桥接层技术架构
- [部署指南](k8s/README.md) - K8s 部署说明

---

## 📄 License

[Your License Here]

---

## 🙏 致谢

- 知识图谱引擎：[cognee](https://github.com/topoteretes/cognee) (Apache-2.0)
- 分布式图数据库：[NebulaGraph](https://github.com/vesoft-inc/nebula)
- 图谱分析：[NetworkX](https://networkx.org/)
- 搜索与 LLM 集成：OpenAI / DeepSeek
