# CSO 场景反例 — 用 AOF 验证"领域 Ontology 的价值"

> 这是一个**能力验证场景**，演示：**AOF/cognee 默认只能抽取通用命名实体，必须注入领域 ontology 才能做领域语义抽取。**

## 为什么叫"反例"

多数 AOF 用法是"**用已有 ontology 约束抽取**"（正向使用）。
这个场景反过来：**不注入任何 ontology 时，AOF 会抽取成什么样？注入 CSO 后又变成什么样？**
对比两者，证明 **领域 ontology 是 AOF 做领域语义抽取的前提条件**——这是 AOF 能力边界的关键认知。

## 核心发现（已实测验证，含修正）

> ⚠️ **重要修正**：初版验证发现"无 ontology 只识别 sarah_chen/company"，后经排查，
> 其中有部分是**跨 dataset 残留污染**（`load_graph_nodes_edges(dataset_name=...)` 偶发
> 带出了其他测试者的旧数据）。用**带时间戳隔离的干净 dataset** 重测后结论更准确：

| 维度 | 无 ontology | 注入 CSO ontology |
|------|-----------|------------------|
| **识别的 CSO 领域实体** | 15 个（cognee 的 LLM 抽取能识别部分语义，如 NPR/ORR/Simon） | 16 个 |
| **实体归一化** | `safety population`（文本短语）、混入 `date`/`phase ii` 等噪声 | `analysispopulation`/`safetypopulation`（**对齐 ontology 类名**） |
| **实体类型一致性** | 较弱，实体名不统一（文本 vs 概念混用） | **强，实体被约束到 ontology 定义的类体系** |
| **语义方向** | 半通用、含噪声 | **贴近 CSO 临床研究语义结构** |

**修正后的真实结论**：
- cognee 的 **LLM 抽取本身有较强的通用语义识别能力**（无 ontology 也能抽出部分领域实体）
- **ontology 的价值不在于"从无到有抽取"，而在于"约束/规范化"**：让实体类型统一、对齐领域类体系、减少噪声
- 这也解释了一个架构事实：**AOF + 领域 ontology = 通用抽取能力 + 领域规范化约束**

## 文件说明

```
examples/cso_validation/
├── README.md                      # 本文档
├── build_graph.py                 # 可复现的验证脚本
├── data/
│   └── cso_oncology.owl           # CSO v0.3 转成的 cognee 可消费 OWL
└── README_aof_validation.md       # 完整验证记录（含技术细节）
```

## 如何运行

```bash
# 进入 AOF 虚拟环境
cd /Users/chaihao/LLM/AOF

# 方式一：无 ontology（看通用抽取）
.venv/bin/python examples/cso_validation/build_graph.py

# 方式二：注入 CSO ontology（看领域抽取）
.venv/bin/python examples/cso_validation/build_graph.py --with-ontology
```

运行前提：
- Ollama 已启动（embedding，端口 11434）
- cognee 可用（AOF venv 内已装，本地 lancedb + cognee_db）
- 无需 NebulaGraph（默认本地存储）

## 关键环境变量（注入 ontology 用）

| 变量 | 值 | 说明 |
|------|-----|------|
| `ONTOLOGY_FILE_PATH` | `examples/cso_validation/data/cso_oncology.owl` | 领域 ontology 路径 |
| `ONTOLOGY_RESOLVER` | `rdflib` | 使用 rdflib 解析 OWL |
| `ONTOLOGY_MATCHING_STRATEGY` | `fuzzy` | 模糊匹配策略 |

## 技术要点（复现时注意）

1. **cognee 0.5.5 的 search 签名**是 `search(query_text=..., datasets=[...])`，不是 `query=`
2. **`cognee.add` 后需显式 `cognee.cognify`** 才会构建图谱实体（否则只有文本 chunks）
3. **ontology 通过环境变量加载** (`get_ontology_env_config()`)，需在建图**前**设置
4. **图谱 nodes 用 `bridge.graph_retrieval.load_graph_nodes_edges()`** 提取
5. **dataset 隔离**：`load_graph_nodes_edges(dataset_name=...)` 偶有跨 dataset 残留，对比时以 nodes 为准

## 复用价值

这个场景模板**不限于临床研究**。任何领域只要：
1. 定义 `domain.owl`（类+实例+关系）
2. 有领域真实文本

即可用 `build_graph.py` 验证：
"AOF 从通用图谱 → 领域图谱" 的跃迁是否发生，从而判断领域 ontology 是否定义正确。
