# SKILL: AOF 维护与治理

> 本技能描述如何保持 AOF 知识图谱的健康、清洁和长期可用。
>
> **核心原则**：知识图谱会腐烂。不维护的图谱比没有图谱更糟糕。

## 何时使用本技能

- 用户说"检查一下系统健康"
- 用户说"清理旧数据"
- 用户说"为什么搜索不到 XXX？"
- 用户说"图谱好像有问题"
- 定时运维任务（cron / 后台任务）

## 维护工作清单

按频率分类：

| 频率 | 任务 | 工具/方法 |
|------|------|-----------|
| 每日 | 检查数据集 cognify 状态 | `dataset_manager.get_dataset_status()` |
| 每周 | 运行系统健康检查 | `aof_doctor.py` / 自定义 GraphDoctor |
| 每月 | 清理孤立节点和过期数据 | `DatasetManager.prune_all_data()` |
| 每季度 | 审计日志审查、权限复核 | `bridge/audit/` |

## 每日检查：数据集状态

### 检查所有数据集

```python
from bridge.dataset_manager import list_all_datasets, check_dataset_status

datasets = await list_all_datasets()
for ds in datasets:
    status = await check_dataset_status(ds.id)
    if status.status != "completed":
        print(f"⚠️ {ds.name}: {status.status} (进度 {status.progress}%)")
```

### 处理卡住的 Cognify

如果进度长时间卡在同一个值：

1. 查看 Cognee 日志
2. 检查是否有超大文件阻塞
3. 考虑分批重新 cognify

## 每周检查：系统健康

### 运行 AOF Doctor

```bash
python aof_doctor.py --spec aof_spec.json
```

`aof_doctor.py` 会检查：
- Cognee 是否可导入
- 数据集是否存在且非空
- 图数据库连接状态
- API Key 和预飞条件

### 运行自定义 Graph Health Check

```python
from bridge.graph_analytics import GraphAnalytics

analyzer = GraphAnalytics(dataset_name="main_dataset")
stats = await analyzer.compute_statistics()

# 健康指标
health_score = 100

if stats.connected_components > 20:
    health_score -= 20
    print("❌ 图谱过于碎片化")

if stats.density < 0.001 and stats.node_count > 100:
    health_score -= 15
    print("⚠️ 图谱密度过低，可能存在大量孤立节点")

print(f"健康评分: {health_score}/100")
```

### 检查内容新鲜度

```python
from bridge.data_sync import DataSync

sync = DataSync(dataset_name="main_dataset")
changes = await sync.detect_changes("/path/to/source/")

print(f"源目录有 {len(changes.modified)} 个文件已变更，尚未同步到 AOF")
```

## 每月清理：数据治理

### 清理孤立节点

孤立节点（没有任何关系的节点）通常是 cognify 失败的产物：

```python
from bridge.graph_analytics import GraphAnalytics

analyzer = GraphAnalytics(dataset_name="main_dataset")
# 获取所有节点并找出度为 0 的
all_nodes = await analyzer.pagerank(top_k=999999)
orphans = [n for n in all_nodes if n.degree == 0]

print(f"发现 {len(orphans)} 个孤立节点")
```

### 清空并重建数据集（谨慎！）

当数据集严重污染时，考虑清空重建：

```python
from bridge.dataset_manager import DatasetManager

manager = DatasetManager()

# 只清空数据，保留数据集元数据
await manager.empty_dataset(dataset_id="main_dataset")

# 然后重新 add + cognify
```

### 完全重置（最危险！）

```python
# 删除所有数据和元数据
await manager.prune_all_data(metadata=True)
```

**警告**：此操作不可逆。执行前必须：
1. 导出备份（`exporters/markdown_exporter.py`）
2. 确认原始数据源仍然可用
3. 获得用户明确授权

## 审计与合规

### 查看审计日志

AOF 的 `bridge/audit/` 模块记录了所有操作：

```python
from bridge.audit import AuditLogger

logger = AuditLogger()
logs = await logger.query(
    start_time="2024-01-01T00:00:00Z",
    end_time="2024-12-31T23:59:59Z",
    action="ingest",
)
```

### 敏感数据检查

确保没有 API Key、密码等信息意外进入图谱：

```python
from bridge.enhanced_search import search_with_intent

suspicious = await search_with_intent(
    query="password api_key secret token",
    user_intent="debugging",
    top_k=50,
)

for item in suspicious.results:
    # 检查是否需要脱敏
    pass
```

## 性能监控

### 检查缓存命中率

```python
from bridge.cache import CacheManager

cache = CacheManager()
stats = cache.get_stats()
print(f"L1 命中率: {stats.l1_hit_rate:.2%}")
print(f"L2 命中率: {stats.l2_hit_rate:.2%}")
```

### 检查任务队列积压

```python
from bridge.tasks import TaskQueue

queue = TaskQueue()
pending = await queue.list_tasks(status="pending")
running = await queue.list_tasks(status="running")
failed = await queue.list_tasks(status="failure")

print(f"任务状态: {len(pending)} pending, {len(running)} running, {len(failed)} failed")
```

## 维护决策树

```
用户说"AOF 有问题"
    │
    ▼
运行 aof_doctor.py
    │
    ├── Cognee 未安装 → 检查 Python 环境和 sys.path
    ├── 数据集为空 → 检查 add 是否成功
    ├── 图数据库连接失败 → 检查 NebulaGraph / Cognee 后端
    └── 全部通过 → 继续排查
                │
                ▼
        检查数据集 cognify 状态
                │
                ├── 卡住的/失败的 → 查看日志，考虑重跑 cognify
                ├── 已完成的 → 检查搜索是否有结果
                └── 搜索也无结果 → 可能是 ontology 不匹配
```

## 黄金法则

1. **先导出，再清理**：任何删除操作前，先用 `exporters/markdown_exporter.py` 备份
2. **小步快跑**：不要一次性清理太多，分批进行
3. **日志驱动**：所有维护操作都要记录审计日志
4. **预防胜于治疗**：设置定时 sync 和 health check，不要等问题爆发
5. **保持源数据完整**：AOF 是索引层，原始文件应该在别处安全保存

## 推荐的 Cron 任务

```bash
# 每日：检查健康
0 9 * * * cd /path/to/aof && python aof_doctor.py --spec aof_spec.json >> logs/doctor.log 2>&1

# 每周：同步源数据
0 2 * * 0 cd /path/to/aof && python -c "import asyncio; from bridge.data_sync import DataSync; asyncio.run(DataSync('main_dataset').sync_directory('/data/docs/'))" >> logs/sync.log 2>&1

# 每月：导出 Markdown 备份
0 3 1 * * cd /path/to/aof && python -c "import asyncio; from exporters.markdown_exporter import export_dataset_to_markdown; asyncio.run(export_dataset_to_markdown(dataset_name='main_dataset', output_dir='/backups/brain_mirror/'))" >> logs/export.log 2>&1
```
