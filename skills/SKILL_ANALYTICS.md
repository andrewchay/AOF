# SKILL: AOF 图谱分析

> 本技能描述如何对 AOF 知识图谱进行深度分析，并从中提取有价值的洞察。
>
> **核心原则**：分析不是跑算法看数字，而是回答业务问题。

## 何时使用本技能

- 用户说"分析一下这个图谱"
- 用户说"找出最关键的人/公司/概念"
- 用户说"这个领域有哪些圈子/派系？"
- 用户说"A 和 B 之间最短路径是什么？"
- 用户说"给我一份图谱健康报告"

## 分析类型速查表

| 业务问题 | 推荐分析 | AOF 工具 |
|----------|----------|----------|
| "谁/什么最重要？" | PageRank / 中心性 | `graph_analytics.pagerank()` |
| "有哪些群落/派系？" | 社区检测 | `graph_analytics.detect_communities()` |
| "A 和 B 怎么连上的？" | 最短路径 | `graph_analytics.find_shortest_path()` |
| "这个网络整体密度如何？" | 图谱统计 | `graph_analytics.compute_statistics()` |
| "综合给我所有指标" | 全量分析 | `graph_analytics.compute_all_metrics()` |

## 标准分析流程

### 步骤 1：获取基础统计

了解图谱的规模和大致结构：

```python
from bridge.graph_analytics import GraphAnalytics

analyzer = GraphAnalytics(dataset_name="main_dataset")
stats = await analyzer.compute_statistics()

print(f"节点数: {stats.node_count}")
print(f"边数: {stats.edge_count}")
print(f"密度: {stats.density}")
print(f"连通分量: {stats.connected_components}")
```

### 步骤 2：识别关键节点（PageRank）

```python
top_nodes = await analyzer.pagerank(top_k=20)

for node in top_nodes:
    print(f"{node.label}: {node.pagerank:.4f} (degree={node.degree})")
```

**如何解读 PageRank**：
- 高 PageRank = 被很多重要节点指向
- 在企业知识库中，通常是：核心人物、关键项目、高频概念

### 步骤 3：发现社区结构

```python
from bridge.graph_analytics import CommunityAlgorithm

communities = await analyzer.detect_communities(
    algorithm=CommunityAlgorithm.LOUVAIN,
    resolution=1.0,
)

for comm in communities[:10]:
    print(f"社区 {comm.community_id}: {comm.node_count} 个节点")
```

**如何解读社区**：
- 每个社区是一组紧密连接的实体
- 在企业场景中，可能对应：不同业务线、不同项目团队、不同主题领域
- `resolution > 1` 会得到更多小社区；`< 1` 会得到更少大社区

### 步骤 4：中心性多维度分析

PageRank 只是其中一种。根据场景选择：

```python
from bridge.graph_analytics import CentralityType

# 度中心性：谁连接最多
degree = await analyzer.compute_centrality(CentralityType.DEGREE, top_k=20)

# 介数中心性：谁是信息桥梁
betweenness = await analyzer.compute_centrality(CentralityType.BETWEENNESS, top_k=20)

# 接近中心性：谁最能快速触达其他节点
closeness = await analyzer.compute_centrality(CentralityType.CLOSENESS, top_k=20)
```

**解读指南**：

| 中心性 | 含义 | 典型高值节点 |
|--------|------|--------------|
| Degree | 社交活跃者 | 连接器、枢纽 |
| Betweenness | 信息守门人 | 跨部门协调者 |
| Closeness | 影响力传播者 | 核心决策者 |
| Eigenvector | 精英网络成员 | 与重要人物连接者 |

### 步骤 5：路径分析

```python
path = await analyzer.find_shortest_path(
    source="node-a-id",
    target="node-b-id",
)

if path.exists:
    print(f"路径长度: {path.length}")
    print(" -> ".join(path.path))
else:
    print("两节点之间不存在路径")
```

### 步骤 6：一键全量分析（推荐用于定期报告）

```python
metrics = await analyzer.compute_all_metrics()

# 导出为 JSON/CSV/GEXF
files = analyzer.export_metrics(metrics, output_dir="./analytics_reports/")
print(f"报告已导出: {files}")
```

## 场景化分析 playbook

### 场景 A：社交网络分析（找出关键影响者）

```python
# 1. PageRank 找出核心人物
people = await analyzer.pagerank(top_k=20)

# 2. 介数中心性找出桥梁人物
bridges = await analyzer.compute_centrality(CentralityType.BETWEENNESS, top_k=20)

# 3. 交叉分析：既是核心又是桥梁的人
people_ids = {p.node_id for p in people}
bridge_ids = {b.node_id for b in bridges}
key_influencers = people_ids & bridge_ids
```

### 场景 B：主题聚类（知识库有哪些领域）

```python
communities = await analyzer.detect_communities(algorithm=CommunityAlgorithm.LOUVAIN)

for comm in communities:
    top_labels = [n.label for n in comm.top_nodes[:5] if n.label]
    print(f"主题 {comm.community_id}: {', '.join(top_labels)}")
```

### 场景 C：异常检测（孤立节点、断链）

```python
stats = await analyzer.compute_statistics()

if stats.connected_components > 10:
    print(f"警告：图谱过于碎片化，有 {stats.connected_components} 个孤立子图")

# 度为 0 的节点是孤儿页
all_degrees = [n.degree for n in await analyzer.pagerank(top_k=999999)]
orphan_count = sum(1 for d in all_degrees if d == 0)
print(f"孤儿节点数: {orphan_count}")
```

## 如何向用户汇报分析结果

### 不要这样做

```
"节点数是 1247，边数是 3421，密度是 0.004，PageRank 最高的是 node_483..."
```

### 要这样做

```
"分析发现您的知识库中有 3 个明显的知识群落：

1. **YC 投资网络**（482 节点）- 核心人物是 Garry Tan、Dalton Caldwell
2. **AI 产品技术**（367 节点）- 核心概念是 RAG、Agent、MCP
3. **个人生活社交**（198 节点）- 与家人、朋友的连接

关键洞察：
- Garry Tan 是连接投资网络和 AI 技术圈的桥梁人物
- 有 23 个孤立节点没有任何关系，建议检查是否是导入错误或遗漏了 cognify
```

## 常见陷阱

| 陷阱 | 后果 | 避免方法 |
|------|------|----------|
| 对小于 50 个节点的图跑复杂分析 | 结果无统计意义 | 小图只做基础统计和可视化 |
| 忽略社区命名 | 用户看不懂 "Community 3" | 用 Top 节点标签给社区命名 |
| 只给数字不给解读 | 用户无法行动 | 每个数字后面跟一句业务含义 |
| 不导出可视化 | 结果难以传播 | 用 `graph_visualizer` 生成 HTML |

## 与搜索结合的黄金流程

对于"分析一下 XXX"这种模糊需求，按这个顺序做：

1. **搜索** → 用 `search_with_intent` 获取上下文
2. **分析** → 跑 PageRank / 社区检测
3. **再搜索** → 针对分析发现的关键节点做深度查询
4. **可视化** → 生成图谱 HTML 或导出 Markdown
5. **总结** → 用自然语言给出可行动的洞察
