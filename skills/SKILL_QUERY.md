# SKILL: AOF 查询与检索

> 本技能描述如何在 AOF 知识图谱中高效、准确地检索信息。
>
> **核心原则**：不是所有问题都适合同一种搜索方式。意图决定策略。

## 何时使用本技能

- 用户说"查一下..." / "搜索..." / "找出..."
- 用户问了一个关于知识库内容的问题
- 用户需要基于图谱生成回答
- 用户要求"用 Cypher 查询..."

## 搜索类型速查表

AOF（通过 Cognee）支持 14 种搜索类型。作为 Agent，你要根据用户意图选择最合适的：

| 用户意图 | 推荐搜索类型 | 说明 |
|----------|--------------|------|
| "XXX 是什么？" / "介绍一下 YYY" | `GRAPH_COMPLETION` | 标准图补全，最常用 |
| "给我一段关于 ZZZ 的原文" | `RAG_COMPLETION` | 基于向量检索的原文片段 |
| "YYY 和 ZZZ 有什么关系？" | `GRAPH_COMPLETION_COT` | 需要推理的关系查询 |
| "最近关于 XXX 的讨论" | `TEMPORAL` | 时序感知搜索 |
| "这段代码怎么工作？" | `CODING_RULES` | 代码规则/实现搜索 |
| "帮我写个 Cypher" | `CYPHER` | 原生 Cypher 查询 |
| "随便给我点相关的" | `FEELING_LUCKY` | 智能选择最佳类型 |
| "给我原始 chunks" | `CHUNKS` | 未加工的文本块 |
| "给我摘要" | `SUMMARIES` | 节点/文档摘要 |

## 3 层查询策略

### 第 1 层：意图感知搜索（默认）

让 AOF 自动选择搜索类型：

```python
from bridge.enhanced_search import search_with_intent

result = await search_with_intent(
    query="Garry Tan 和 YC 有什么关系？",
    user_intent="reasoning",  # 或 None 让系统自动分类
    top_k=10,
)

for item in result.results:
    print(item)
```

### 第 2 层：指定搜索类型

当你明确知道需要什么时，直接指定类型：

```python
from bridge.enhanced_search import EnhancedSemanticSearch, SearchType

search = EnhancedSemanticSearch()
result = await search.search(
    query="Garry Tan",
    search_type=SearchType.GRAPH_COMPLETION,
    top_k=10,
)
```

### 第 3 层：原生 Cypher 图查询

当问题涉及复杂图模式（多跳关系、路径、聚合）时，使用 Cypher：

```python
from bridge.cypher_query import execute_cypher

result = await execute_cypher("""
    MATCH (a:Person)-[:WORKS_AT]->(b:Organization)
    WHERE a.name CONTAINS 'Garry'
    RETURN a.name, b.name
    LIMIT 10
""")

for record in result.records:
    print(record)
```

## 搜索类型详解

### GRAPH_COMPLETION（默认）

**适合**：实体定义、属性查询、直接关系

```python
result = await search.search(
    query="Garry Tan",
    search_type=SearchType.GRAPH_COMPLETION,
)
# 返回：关于 Garry Tan 的实体信息和直接关系
```

### RAG_COMPLETION

**适合**：需要原始文本证据、引用的场景

```python
result = await search.search(
    query="startup growth strategies",
    search_type=SearchType.RAG_COMPLETION,
)
# 返回：原始文档中的相关段落
```

### GRAPH_COMPLETION_COT（Chain-of-Thought）

**适合**：需要多步推理的复杂问题

```python
result = await search.search(
    query="Who should I invite who knows both Pedro and Diana?",
    search_type=SearchType.GRAPH_COMPLETION_COT,
)
# 返回：带有推理过程的答案
```

### TEMPORAL

**适合**：时间相关的问题

```python
result = await search.search(
    query="最近三个月关于 AI 的决策",
    search_type=SearchType.TEMPORAL,
)
```

### CYPHER

**适合**：结构化图查询，特别是涉及聚合、路径、条件过滤

```python
# 查找共同邻居
result = await search.search(
    query="""
        MATCH (a:Person {name: 'Pedro'})-[:KNOWS]->(b:Person)<-[:KNOWS]-(c:Person {name: 'Diana'})
        RETURN b.name
    """,
    search_type=SearchType.CYPHER,
)
```

## 查询最佳实践

### 1. 先搜索，再总结

**不要**在没有搜索的情况下直接回答用户。AOF 的存在就是为了避免幻觉。

```python
# 好的做法
results = await search_with_intent(query=user_question)
answer = synthesize_results(results)

# 坏的做法
answer = llm.generate(user_question)  # 可能幻觉
```

### 2. 处理"无结果"的情况

如果搜索返回空结果，明确告诉用户：

```
"AOF 知识库中没有找到关于 '[查询词]' 的信息。"
"可能原因：1) 数据尚未摄取；2) 使用了不同的术语；3)  Cognify 尚未完成。"
```

### 3. 多查询扩展（高价值问题）

对于模糊或复杂的问题，可以生成多个查询变体并行搜索：

```python
queries = [
    "startup fundraising",
    "how to raise venture capital",
    "seed round best practices",
]

results = []
for q in queries:
    r = await search_with_intent(query=q)
    results.extend(r.results)
```

### 4. 结合 Graph Analytics 做探索式查询

当用户说"分析一下这个领域"时，不要只搜索：

```python
# 先跑分析
pagerank = await get_pagerank("main_dataset", top_k=20)
communities = await detect_communities("main_dataset")

# 再基于分析结果查询关键节点
for node in pagerank[:5]:
    result = await search_with_intent(query=node.label)
```

## 常见陷阱

| 陷阱 | 后果 | 避免方法 |
|------|------|----------|
| 所有问题都用 `RAG_COMPLETION` | 错过图结构中的关系洞察 | 按意图选择类型 |
| 查询词太宽泛 | 结果噪音大 | 添加限定词和上下文 |
| 忽略 `top_k` 参数 | 返回过多或过少 | 探索用 20-50，精准用 5-10 |
| 不检查搜索结果为空 | 幻觉回答 | 总是检查结果长度 |
| Cypher 查询不 LIMIT | 大图可能超时 | 始终加 LIMIT |

## 黄金法则

1. **意图第一**：先理解用户要什么，再选搜索类型
2. **图优先**：如果问题涉及"关系"，优先用图搜索而非纯 RAG
3. **证据说话**：所有回答必须基于搜索结果，不编造
4. **分层查询**：简单问题快速答，复杂问题分解答
