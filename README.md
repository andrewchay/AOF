# Agentic Ontology Factory (AOF) v2.1

AOF 是**企业知识的 "Agent-Ready 资产化引擎"**：一次摄取企业过往的内外知识，自动产出**知识图谱、RAG 向量库、LLM Wiki / OKF、Agent 训练数据**四种形态资产，并通过 **REST API / MCP Server / Web 控制台**三种方式消费，让 Agent 开箱即懂、即调即用。

基于 Cognee 知识图谱引擎，提供多源数据摄取、增量同步、混合检索、图谱分析、知识治理与**企业级安全**能力。

---

## 🌟 核心特性

### 决策级溯源（v2.2）
- 🧭 **决策一等公民** — 记录 Agent 的结论、理由、输入证据、前序决策、输出实体与适用策略；模型为轻量 **PROV-O 风格**（Decision/Agent/Entity）。
- 🔗 **因果、先例与影响** — 通过 `/v1/decisions` 记录，按决策追溯 ancestors/descendants，查相似先例，并评估下游受影响决策和实体。
- 🧾 **可核验合规轨迹** — 追加 JSONL 账本以 SHA-256 前序哈希链防篡改；`/v1/decisions/{id}/audit-trail` 输出 JSON-LD、证据/理由完整性、策略引用和完整性校验。
- 🤖 **多入口消费** — REST、MCP（`aof_record_decision` / `aof_decision_audit_trail` / `aof_find_decision_precedents`）和 `AOFClient` 同步支持。

### 本体治理与确定性推理（v2.2）
- 🧱 **SHACL 发布门禁** — 校验类、关系、必填、基数、数据类型、值域及跨实体约束；未知约束同样阻断，不能静默跳过。
- 🗃️ **OWL + SKOS 版本仓** — 分离可计算语义与业务词汇，发布产生 ontology/shape/SKOS 版本、变更集、审批及发布决策。
- 🧑‍⚖️ **治理编辑器** — `/ontology` 支持草稿、校验、冲突审查、不可变豁免、请求修改、审批、发布、差异与影响预览。
- ⚙️ **确定性推理** — 版本化分层 Datalog 固定点推理、逐事实证明链及只读 SPARQL 快照查询；Rete 保留为有真实在线增量负载后的扩展层。

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

### RAG 混合检索（v2.1 新增）
针对知识问答场景的统一检索，**多路召回 + 命中溯源**：

- 🛰️ **三路并行召回** - 关键词（lexical）+ 向量（vector）+ 图谱路径（graph）
- 🔀 **RRF 融合** - 无参数排序融合，跨路结果合并打分
- 🧹 **4 层去重** - 来源 top3 / 文本相似度 / 类型多样性 / 单页上限
- 🔍 **命中溯源** - 每条结果携带 `provenance`（来源路 / 数据集 / 种子实体 / 图谱路径 / 关系），回答"为什么命中"
- 🌐 **查询扩展** - 可选基于图谱实体的查询扩展

### 知识资产导出（四形态）
- 📝 **Markdown 导出** - 将知识图谱导出为 GBrain 风格的 `compiled_truth + timeline` 页面
- 🔄 **图模式/Dataset 模式双回退** - 优先从图数据导出，失败时回退到原始数据
- 📁 **MECE 目录结构** - 按实体类型自动组织（person/company/concept/...）
- 🧠 **LLM Wiki / OKF 导出（v2.1 新增）** - 编译为原子化 Markdown 知识包：标准 YAML Frontmatter（type/title/description/tags）、概念间交叉链接、`index.md`（渐进式披露目录，Agent 可低成本消费）、`log.md`（导出审计）
- 🧰 **OKF 知识 Lint（v2.1 新增）** - 知识包结构体检：断链检测、重复概念、口径冲突
- 🛰️ **RAG 向量库** - 供混合检索消费（见上）
- 🎓 **Agent 训练数据** - SFT / RAG-Eval / Agent-Tool（见下）

### 🤖 训练数据生成（AI/Agent 训练数据集）
基于知识图谱和文档自动生成结构化训练数据，支持三种类型：

| 类型 | 用途 | 输出格式 |
|------|------|---------|
| **SFT** | LLM 监督微调 | OpenAI chat completions (messages) |
| **RAG Eval** | 检索增强生成评估 | question-answer-context 三元组 |
| **Agent Tool** | Agent 工具调用训练 | OpenAI function calling |

**生成策略**：
- **实体问答** - 基于节点属性生成 instruction-response 对
- **关系推理** - 基于三元组生成关联问题
- **文档摘要** - 基于文本 chunks 生成摘要任务
- **多轮对话** - 基于子图路径模拟连贯对话
- **工具调用** - 基于策略本体生成 function calling 样本

**质量控制**：
- BLAKE2b 去重（与增量同步一致）
- 长度过滤、多样性评分
- 跨生成器全局去重
- 可选 train/val/test 拆分

### Agent 化接口（v2.1 新增）
- 🤖 **MCP Server** - 12 个知识工具，可被 Dify / LangGraph / Claude / 任意 MCP 客户端直接接入
- 🗂️ **OKF 知识目录服务** - 动态目录 + 渐进式披露 API，Agent 先读 `index.md` 再按需取概念
- 🛰️ **RAG 统一检索 API** - `/v1/rag/retrieve` 多路召回 + 溯源，Agent 知识问答前的事实检索
- 🌐 **Web 管理控制台（v2.1 新增）** - Vue3 前端：Dashboard / OKF 浏览 / vis-network 图谱可视化 / Agent 聊天测试台 / 批量 + URL 摄取向导，`run_web.sh` 一键启动（SPA 由后端同端口托管）

### Agent Playbook (Skills)
- 🤖 **Markdown Skills** - 纯文本的 Agent 操作手册（ingest/query/analytics/maintain/training-data）
- 📖 **零代码技能系统** - Agent 读取即会用的 playbook，无需修改二进制

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

### 整体架构（v2.1）

```
┌─────────────────────────────────────────────────────────────────┐
│                         消费端 (v2.1)                            │
│  Web 控制台 (Vue3) │ MCP Server (12 工具) │ REST Client │ Agent  │
└─────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│                        API Gateway 层                            │
│  Auth (JWT/API Key) → Rate Limit → Circuit Breaker → Router      │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────────┐
│                         API 层 (79 端点)                          │
├─────────────────────────────────────────────────────────────────┤
│  Ingest │ Sync │ Analytics │ Search │ Visualize │ Graph         │
│  OKF │ RAG │ TrainingData │ Ops │ Tasks │ Audit                 │
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
│  search/        混合检索（hybrid_search + graph_retrieval）      │
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
| `hybrid_search.py` | 混合搜索（关键词+向量+RRF+4 层去重） | ✅ v2.1 |
| `graph_retrieval.py` | 图谱路径召回（实体提取/种子匹配/路径扩展） | ✅ v2.1 |
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
| `exporters/markdown_exporter.py` | 图谱 → Markdown 导出 | ✅ |
| `exporters/okf_exporter.py` | 图谱 → LLM Wiki / OKF 知识包导出 | ✅ v2.1 |
| `exporters/okf_service.py` | OKF 共享消费服务（目录/概念/搜索/Lint） | ✅ v2.1 |
| `exporters/rag_service.py` | RAG 统一检索服务（多路召回+命中溯源） | ✅ v2.1 |
| `exporters/training_data_exporter.py` | 图谱 → AI 训练数据集 | ✅ |
| `tools/knowledge_lint.py` | 知识库 Lint（断链/重复/一致性）CLI | ✅ v2.1 |
| `training_data/generators/` | SFT/RAG/Agent 数据生成 | ✅ |
| `training_data/pipeline.py` | 生成流水线编排 | ✅ |
| `training_data/quality.py` | 质量过滤与去重 | ✅ |
| `mcp_server.py` | MCP Server（12 个知识工具） | ✅ v2.1 |
| `web/` | 知识管理控制台（Vue3 + Element Plus） | ✅ v2.1 |
| `skills/` | Agent Playbook (纯 Markdown) | ✅ |

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
# 开发模式（API）
services/semantic_middle_layer_api/run_api.sh

# 轻量版一键启动（构建前端 + 后端托管 SPA，访问 http://localhost:8787）
services/semantic_middle_layer_api/run_web.sh

# 生产模式（Docker）
docker build -t aof:latest .
docker run -p 8787:8787 aof:latest

# Kubernetes 部署
kubectl apply -f k8s/
```

### 4. 启动 MCP Server（Agent 接入）

```bash
# 以 MCP Server 模式运行（stdio 协议，可被 Claude / Dify / LangGraph 接入）
.venv/bin/python mcp_server.py
# 或配置到 MCP 客户端：
#   npx 方式 / 直接注册命令：AOF_ROOT/.venv/bin/python AOF_ROOT/mcp_server.py
```

### 5. 环境变量补充（v2.1）

```bash
# OKF 知识包默认目录（消费 OKF 工具时使用）
export AOF_OKF_DIR="okf_bundle"

# 可选：RAG 混合检索的图谱路开关（默认开启）
# export AOF_RAG_INCLUDE_GRAPH="1"
```

---

## 📖 使用指南

### RAG 混合检索（使用规范）

**何时用**：Agent 或应用做知识问答前的事实检索。比纯关键词召回更准、比纯向量召回更可溯源。

```bash
# REST 方式
curl -X POST http://localhost:8787/v1/rag/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query": "Ganyu 与 Qixing 的关系", "dataset_name": "genshin_ultimate_kg", "limit": 10}'
# include_graph=false 可关闭图谱路；expansion=true 开启查询扩展
```

```python
# Python 方式（共享服务，REST 与 MCP 同源）
from exporters.rag_service import rag_retrieve

result = await rag_retrieve(
    query="Ganyu 与 Qixing 的关系",
    dataset_name="genshin_ultimate_kg",
    limit=10,
    include_graph=True,
)
for r in result.results:
    print(r.score, r.type, r.source, r.provenance)  # provenance 为命中溯源链路
```

### OKF 知识包（导出与消费规范）

**导出**（一次导出生成原子化 Markdown 知识包）：

```bash
# REST 方式：把数据集导出为 OKF 知识包
curl -X POST http://localhost:8787/v1/okf/export \
  -H 'Content-Type: application/json' \
  -d '{"dataset_name": "genshin_ultimate_kg", "output_dir": "okf_bundle"}'

# CLI 方式：对既有知识包运行 Lint 体检
.venv/bin/python -m tools.knowledge_lint okf_bundle
```

**消费**（推荐顺序：先索引 → 再按需取概念 → 检索 → Lint）：

| 动作 | REST 端点 | MCP 工具 |
|------|-----------|----------|
| 列出知识包 | `GET /v1/okf/bundles` | - |
| 读渐进式披露目录 | `GET /v1/okf/bundles/{name}/index` | `aof_okf_index` |
| 读单个 Concept | `GET /v1/okf/bundles/{name}/concept?path=person/alice.md` | `aof_okf_get_concept` |
| 搜索 Concept | `GET /v1/okf/bundles/{name}/search?query=...&type=...` | `aof_okf_search_concepts` |
| 结构体检 | `POST /v1/okf/bundles/{name}/lint` | `aof_okf_lint` |

> **Agent 消费约定**：优先调用 `aof_okf_index` 获取全局索引，再按需读取具体概念，避免一次性加载全部文档；引用概念时保留其 `path` 作为知识来源锚点。

### MCP Server（Agent 接入规范）

以 stdio 协议提供 12 个工具，可直接接入 Dify / LangGraph / Claude / Cursor 等 MCP 客户端：

| 工具 | 能力 |
|------|------|
| `aof_hybrid_search` | 混合搜索（关键词+向量+RRF+去重） |
| `aof_enhanced_search` | 增强语义搜索（按意图选类型） |
| `aof_rag_retrieve` | RAG 多路召回检索（带溯源） |
| `aof_okf_index` / `aof_okf_get_concept` / `aof_okf_search_concepts` / `aof_okf_lint` | OKF 知识包消费四件套 |
| `aof_export_markdown` | 图谱 → Markdown 导出 |
| `aof_graph_analytics` | 图谱分析（PageRank/社区/中心性） |
| `aof_dataset_status` | 数据集 cognify 状态 |
| `aof_list_datasets` | 数据集列表 |
| `aof_health_check` | Graph Doctor 健康检查 |

```bash
# 注册到 MCP 客户端（Claude Desktop / Cursor 等）
# { "mcpServers": { "aof": { "command": "<AOF_ROOT>/.venv/bin/python", "args": ["<AOF_ROOT>/mcp_server.py"] } } }
```

### Web 管理控制台

`services/semantic_middle_layer_api/run_web.sh` 一键启动，访问 `http://localhost:8787`：

- **Dashboard** - 数据集与系统概览
- **OKF 浏览** - 知识包目录 / 概念阅读 / Lint 报告
- **图谱可视化** - vis-network 交互式图谱
- **Agent 测试台** - 基于 OKF 知识检索的对话测试
- **摄取向导** - 批量文件 / URL 摄取

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

### Markdown 导出

```python
from exporters.markdown_exporter import export_dataset_to_markdown

result = await export_dataset_to_markdown(
    dataset_id="my_dataset_uuid",
    output_dir="./brain_mirror/",
)

print(f"导出 {result.pages_exported} 页到 {result.output_dir}")
```

### 训练数据生成

```python
from exporters.training_data_exporter import export_dataset_to_training_data

# 生成 SFT + RAG 评估数据
result = await export_dataset_to_training_data(
    dataset_id="my_dataset",
    output_dir="./training_data/",
    generators=["sft", "rag_eval"],
    max_samples=1000,
)

print(f"生成 {result.total_samples} 条样本")
print(f"类型分布: {result.samples_by_type}")
print(f"输出文件: {result.output_files}")

# 支持 train/val/test 拆分
result = await export_dataset_to_training_data(
    dataset_id="my_dataset",
    output_dir="./training_data/",
    generators=["sft", "rag_eval", "agent_tool"],
    max_samples=5000,
    enable_split=True,
)
```

### Agent Skills 使用

AOF 的 `skills/` 目录包含纯 Markdown 的 Agent 操作手册。Agent 在操作前应读取对应的 skill：

```
skills/SKILL_INGEST.md      # 数据摄取指南
skills/SKILL_QUERY.md       # 查询检索指南
skills/SKILL_ANALYTICS.md   # 图谱分析指南
skills/SKILL_MAINTAIN.md    # 维护治理指南
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
- [架构设计](ARCHITECTURE.md) - 系统架构设计
- [部署指南](k8s/README.md) - K8s 部署说明
- [Agent Skills](skills/) - Agent 操作手册

---

## 📄 License

[Your License Here]

---

## 🙏 致谢

- 知识图谱引擎：[cognee](https://github.com/topoteretes/cognee) (Apache-2.0)
- 分布式图数据库：[NebulaGraph](https://github.com/vesoft-inc/nebula)
- 图谱分析：[NetworkX](https://networkx.org/)
- 搜索与 LLM 集成：OpenAI / DeepSeek
