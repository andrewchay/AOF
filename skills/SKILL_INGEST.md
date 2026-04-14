# SKILL: AOF 数据摄取

> 本技能描述如何正确、高效、可复现地将数据导入 AOF。
> 
> **核心原则**：摄取不是"扔进去就行"，而是知识工程的第一步。质量差的输入会产生腐烂的图谱。

## 何时使用本技能

- 用户说"把这份文档/数据导入 AOF"
- 用户说"新增一个数据源"
- 用户说"同步某个目录到 AOF"
- 用户说"批量导入"

## 摄取前必做的 3 件事

### 1. 确认数据集（Dataset）

AOF 中所有数据都属于某个 dataset。如果没有明确指定，先确认 dataset 名称或 ID。

```python
from bridge.dataset_manager import list_all_datasets

datasets = await list_all_datasets()
print([d.name for d in datasets])
```

### 2. 检查数据格式

AOF 支持的数据类型：
- `.md`, `.txt`, `.pdf`（文档）
- `.py`, `.js`, `.ts`, `.sql`（代码）
- `.csv`, `.json`, `.xml`, `.yaml`（结构化数据）
- `.ipynb`（Jupyter Notebook）
- 数据库 Schema（PostgreSQL / MySQL / SQLite）
- S3 对象、URL 网页内容

**不支持或需要预处理的**：
- 二进制可执行文件
- 加密文档
- 超过 50MB 的单个文件（建议分片）

### 3. 运行 Preflight 检查

不要直接运行 `aof_add.py`，先确保环境正确：

```python
from bridge.preflight import ensure_preflight_for_add

ensure_preflight_for_add(
    spec=spec_dict,
    data_paths=["./my_data/"],
    require_api_key=True,
)
```

## 标准摄取流程

### 场景 A：单个文件或文本

```python
from bridge.cognee_add_runner import run_add_from_spec

spec = {
    "dataset": "main_dataset",
    "cognee": {"root": "/Users/chaihao/LLM/cognee"},
}

# 文本数据
result = await run_add_from_spec(spec=spec, data="这是要摄取的文本内容")

# 文件路径
result = await run_add_from_spec(spec=spec, data="/path/to/file.md")
```

### 场景 B：批量目录摄取

```python
from bridge.batch_ingestion import BatchIngestor

ingestor = BatchIngestor(dataset_name="main_dataset")
report = await ingestor.ingest_directory("/path/to/documents/")

print(f"成功: {report.success_count}, 失败: {report.failure_count}")
```

### 场景 C：增量同步（推荐用于定时任务）

```python
from bridge.data_sync import DataSync

sync = DataSync(dataset_name="main_dataset")
result = await sync.sync_directory("/path/to/documents/")

print(f"新增: {result.added}, 更新: {result.updated}, 删除: {result.deleted}")
```

### 场景 D：S3 摄取

```python
from bridge.s3_ingestion import S3Ingestor

ingestor = S3Ingestor(
    bucket="my-bucket",
    prefix="documents/",
    dataset_name="main_dataset",
)
result = await ingestor.ingest()
```

### 场景 E：URL 网络内容

```python
from bridge.url_ingestion import URLIngestor

ingestor = URLIngestor(dataset_name="main_dataset")
result = await ingestor.ingest_urls([
    "https://example.com/article1",
    "https://example.com/article2",
])
```

### 场景 F：数据库 Schema

```python
from bridge.database_schema_extractor import extract_schema

schema_docs = await extract_schema(
    db_url="postgresql://user:pass@localhost/db",
    dataset_name="main_dataset",
)
```

## 摄取后的必要步骤

### 步骤 1：运行 Cognify（关键！）

`add` 只是把原始数据放入 Cognee，`cognify` 才是提取实体、关系、向量的过程。

```python
from bridge.cognee_runner import run_cognify_from_spec

spec = {
    "dataset": "main_dataset",
    "cognee": {"root": "/Users/chaihao/LLM/cognee"},
    "ontology": {
        "file": "/path/to/ontology.owl",
        "matching_cutoff": 0.8,
    },
}

result = await run_cognify_from_spec(spec)
```

### 步骤 2：验证数据集状态

```python
from bridge.dataset_manager import check_dataset_status

status = await check_dataset_status("main_dataset")
print(f"状态: {status.status}, 进度: {status.progress}%")
```

### 步骤 3：运行快速搜索验证

```python
from bridge.enhanced_search import search_with_intent

result = await search_with_intent(
    query="test",
    user_intent="exploration",
    top_k=5,
)
print(f"找到 {len(result.results)} 条结果")
```

## 幂等性与增量更新

AOF 的增量加载器（`incremental_loader.py`）使用 **Blake2b 内容指纹** 检测变更：

- **未修改的文件**：自动跳过，不会重复 cognify
- **修改的文件**：重新处理
- **删除的文件**：可选从 dataset 中移除（取决于 `sync_mode`）

**建议**：对于定时同步任务，始终使用 `DataSync` 或 `IncrementalLoader`，而不是直接 `add`。

## 常见错误与避免方法

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| "Cognee 未安装" | `sys.path` 中没有 cognee root | 确认 `spec["cognee"]["root"]` 正确 |
| "ontology.file 不存在" | 本体文件路径错误 | 检查 OWL 文件路径 |
| cognify 进度卡在 0% | 数据集为空 | 先确认 add 成功，再运行 cognify |
| 大量重复节点 | 同一文档被多次 add | 使用增量同步代替全量 add |
| 实体抽取质量差 | 缺少 ontology 指导 | 提供领域本体文件 |

## 黄金法则

1. **先 add，后 cognify**：没有 cognify 的数据只是原始文件堆
2. **增量优于全量**：重复全量导入会造成图谱污染
3. **验证再交付**：每次摄取后至少做一次搜索验证
4. **保留原始数据位置**：不要把本地文件删除后就认为 AOF "备份"了它
