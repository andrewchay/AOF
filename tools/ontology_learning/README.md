# AOF Ontology Learning（ontology 迭代自举引擎）

> 用 AOF/cognee 从领域文本**自动迭代完善 ontology**：seed ontology → AOF 建图谱 →
> 提取实例 → 归纳新类 → 合并 → 收敛判断 → 下一轮。

该模块来源于 CSO 验证 POC（`examples/cso_validation/iterative_poc`），已在真实 cognee
上验证（seed 7 类 → 13 → 18 类，概念饱和度 0.46 → 0.28 递减）。

## 模块构成

| 文件 | 作用 |
|------|------|
| `engine.py` | `OntologyLearner` 核心引擎，编排迭代循环 |
| `convergence.py` | 收敛判据：概念饱和度 / 类稳定 / 概念增长减速 / MaxIter |
| `instance_to_class.py` | 用 cognee 图谱 `is_a` 边做"实例 → 类"归纳 |
| `owl_writer.py` | 生成/更新 OWL |
| `self_test.py` | 不依赖 cognee 的引擎自测（用模拟 graph_builder）|

## 核心用法

```python
from tools.ontology_learning import OntologyLearner

# 1. 提供一个 graph_builder：接受 (owl_text, round_n) -> (nodes, edges)
#    真实场景用 AOF/cognee:
async def cognee_builder(owl_text, round_n):
    # 注入 owl -> cognee.add(text) -> cognee.cognify() -> load_graph_nodes_edges()
    ...

# 2. 实例化并在 seed owl 上学习
learner = OntologyLearner(graph_builder=cognee_builder)
result = learner.learn(
    seed_owl_path="seed.owl",
    max_iterations=5,
    out_owl_path="learned.owl",
)
print(result.final_classes)
print(result.iterations)  # 每轮 sat/stab/decay 收敛日志
```

## 收敛终点（三判据 + 硬性上限）

| 判据 | 含义 | 默认阈值 |
|------|------|---------|
| **概念饱和度** (saturation) | 新增类 / 总类数 | ≤ 0.10 止 |
| **类结构稳定** (stability) | 连续两轮子类关系差异比例 | ≤ 0.20 止 |
| **概念增长减速** (decay) | 本轮新增 / 上轮新增 | ≤ 0.30 止 |
| **MaxIterations** | 硬性最大轮数 | 兜底防发散 |

任一触发即停止。各判据可独立开关（传 None 关闭）。

## 实例 → 类 归纳（关键，已在 CSO 验证）

依赖 cognee 图谱的 `is_a` 边把实体实例映射到其领域类：

```
atezolizumab          --is_a--> drug
non-progression rate  --is_a--> endpoint
simon two-stage       --is_a--> statisticalmethod
```

**实现细节（踩坑记录）：**
- cognee 实体名字在 `name` 字段（不是 `label`，`label` 恒为 None）
- 具体实例节点 `type == "Entity"`，领域类节点 `type == "EntityType"`
- 需过滤派生节点：`DocumentChunk/TextDocument/TextSummary/EntityType`
- `cognify` 后需等待索引就绪，`load_graph_nodes_edges` 需重试

## 运行自测

```bash
cd /Users/chaihao/LLM/AOF
.venv/bin/python tools/ontology_learning/self_test.py
```

自测用模拟 graph_builder，不依赖 cognee/LLM，验证收敛逻辑正确性。

## 复用价值

这是一个**通用 ontology learning 模板**，适用于任何领域：
- 只有种子 ontology（少量类）+ 领域文本
- 想让 ontology 随文本自动扩展、按收敛判据自动停止

可复用于：taxonomy 归纳、schema 发现、领域建模、数据库 → ontology 迁移等。
