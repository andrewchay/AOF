# Agent Harness Trainer 设计文档

> 日期: 2026-08-29
> 目标: 将"迭代式 Agent 能力驯化"模式产品化——用测试问题驱动 Agent，迭代资产直到满意，并产出可训练数据。
> 核心选择: **精细归因** + **Agent-Tool + 资产引用** 训练数据

## 背景

用户描述了一个通用模式：
1. 用测试问题（无标准答案，如商业分析案例）问 Agent
2. 根据回答质量迭代资产（ontology、playbook、知识库）
3. 每次迭代做 git 记录
4. 聊天 trace 记录
5. 直到满意
6. 产出训练数据

这个模式适用于几乎所有"需要让 Agent 在特定领域/风格上表现更好"的场景。

## 核心设计决策（已确认）

| # | 决策点 | 选择 | 说明 |
|---|--------|------|------|
| 1 | 归因方式 | **精细版** | 记录 Agent 每次回答实际使用了哪些资产，对比满意 vs 不满意的资产使用差异 |
| 2 | 训练数据形态 | **Agent-Tool + 资产引用** | 记录 Agent 调用了哪些"资产查询工具"，训练"遇到问题先查哪些资产"的能力 |
| 3 | 资产追踪实现 | **C: 混合** | MVP 用隐式推断（文本匹配），长期升级到 function calling 显式声明 |
| 4 | 评分维度权重 | **场景自定义** | 商业分析(structure+reasoning 权重高) / 客服(style+completeness 权重高) / 法律(accuracy+structure 权重高) |
| 5 | 满意标准 | **A: 单轮达标** | overall >= threshold 即满意，简单快速 |
| 6 | 归因混杂处理 | **标注置信度** | MVP 不做控制实验，归因报告标注置信度（如"基于 3 轮迭代，置信度 60%"） |
| 7 | 跨问题复用 | **是，按 pattern 类型** | 同类问题（如"区域库存分析"）共享训练数据，HarnessSession 需标记 pattern_type |

### 1. 归因: 精细版

**不是**简单统计"哪类资产改动与满意度相关"，而是：
- 记录 Agent **每次回答时实际使用了哪些资产**（ontology 类、playbook 步骤、知识库条目）
- 对比**满意迭代 vs 不满意迭代**的资产使用差异
- 精确归因到具体资产改动："加了 `inventory_turnover` 指标定义后，Agent 开始主动计算周转天数"

### 2. 训练数据: Agent-Tool + 资产引用

**不是**简单的 SFT 多轮对话，而是：
- 记录 Agent 回答时**调用了哪些"资产查询工具"**
- 训练目标: 让 Agent 学会"遇到这类问题时，应该先查哪些资产"
- 产出格式: OpenAI function calling（messages + tools + tool_calls）

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent Harness Trainer                     │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Session   │  │  Iteration  │  │   Attribution       │  │
│  │   Manager   │→ │   Engine    │→ │   Engine            │  │
│  │             │  │             │  │                     │  │
│  │ - 创建会话   │  │ - 执行问答   │  │ - 资产使用差异分析   │  │
│  │ - 管理生命周期│  │ - 记录 trace │  │ - 改动→效果归因     │  │
│  │ - 满意度判定 │  │ - 专家评分   │  │ - 推荐下一步改动     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│         │                │                    │              │
│         ▼                ▼                    ▼              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Training Data Extractor                 │   │
│  │                                                      │   │
│  │  从满意迭代中提取:                                    │   │
│  │  - Agent-Tool 样本 (问题 → 资产查询 → 回答)           │   │
│  │  - 资产引用链 (回答中引用了哪些知识节点)               │   │
│  │  - 推理轨迹 (Agent 的思考步骤)                        │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                  │
│                           ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              AOF 核心能力调用层                      │   │
│  │                                                      │   │
│  │  - decision_provenance: 记录 Agent 决策链             │   │
│  │  - RawTrajectory: 对话 trace → 训练样本              │   │
│  │  - ontology governance: 资产版本管理                  │   │
│  │  - graph retrieval: 资产使用追踪                      │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## 数据模型

### HarnessSession（驯化会话）

```python
@dataclass
class HarnessSession:
    id: str
    problem_statement: str          # 测试问题/任务描述
    domain: str                     # 领域标签（如"商业分析"、"客服"）
    created_at: datetime
    status: SessionStatus           # active | satisfied | abandoned
    satisfaction_threshold: float   # 满意度阈值（默认 4.0/5.0）
    
    # 资产快照
    initial_assets: AssetSnapshot   # 初始资产版本（git hash + ontology 状态）
    current_assets: AssetSnapshot   # 当前资产版本
    
    # 迭代历史
    iterations: list[Iteration]
    
    # 产出
    training_dataset_path: Optional[str]
    attribution_report: Optional[AttributionReport]
```

### Iteration（单次迭代）

```python
@dataclass
class Iteration:
    number: int
    timestamp: datetime
    
    # 资产状态
    asset_version: str              # git commit hash
    asset_changes: list[AssetChange] # 本轮资产改动
    
    # Agent 回答
    agent_response: str             # 完整回答文本
    trace: ConversationTrace        # 完整对话 trace（含工具调用）
    
    # 资产使用追踪（精细归因核心）
    assets_used: list[AssetUsage]   # Agent 回答时使用了哪些资产
    
    # 评估
    expert_score: ExpertScore       # 专家评分（多维度）
    expert_feedback: str            # 专家文字反馈
    is_satisfactory: bool           # 是否达到满意阈值
```

### AssetUsage（资产使用记录）

```python
@dataclass
class AssetUsage:
    asset_type: AssetType           # ontology_class | playbook_step | knowledge_chunk | tool_schema
    asset_id: str
    asset_name: str
    asset_version: str
    
    # 使用上下文
    usage_context: UsageContext     # 回答中引用 | 推理中使用 | 工具调用参数
    usage_location: str             # 在回答中的位置（段落/句子）
    
    # 影响评估
    relevance_score: float          # 与问题的相关度（0-1）
    is_critical: bool               # 是否是回答的关键支撑
```

### ExpertScore（专家评分）

```python
@dataclass
class ExpertScore:
    overall: float                  # 总体评分 1-5
    
    # 多维度评分
    structure: float               # 结构清晰度 1-5
    accuracy: float                # 事实准确性 1-5
    completeness: float            # 完整性 1-5
    style: float                   # 风格符合度 1-5（是否符合领域专家口味）
    reasoning: float               # 推理链清晰度 1-5
    
    # 权重（可自定义）
    weights: dict[str, float]      # 各维度权重
```

### AssetChange（资产改动）

```python
@dataclass
class AssetChange:
    change_type: ChangeType         # add | modify | delete
    asset_type: AssetType
    asset_id: str
    
    # 改动详情
    before: Optional[str]          # 改动前（git diff 前）
    after: Optional[str]           # 改动后（git diff 后）
    diff_summary: str              # 改动摘要（LLM 生成）
    
    # 影响范围
    affected_queries: list[str]    # 可能影响哪些问题类型
```

### AttributionReport（归因报告）

```python
@dataclass
class AttributionReport:
    # 相关性矩阵
    change_type_correlation: dict[str, float]  # 哪种改动类型 → 满意度提升
    asset_type_correlation: dict[str, float]   # 哪种资产类型 → 满意度提升
    
    # 关键洞察
    key_insights: list[str]        # 如"增加 XX 指标定义后，回答质量 +30%"
    
    # 具体归因
    attribution_details: list[AttributionDetail]
    
    # 推荐
    recommended_next_changes: list[AssetChange]
    confidence: float              # 推荐置信度

@dataclass
class AttributionDetail:
    asset_change: AssetChange
    before_score: float            # 改动前平均满意度
    after_score: float             # 改动后平均满意度
    improvement: float             # 提升幅度
    evidence: list[str]            # 支撑证据（哪些迭代体现了提升）
```

## 关键算法

### 1. 资产使用追踪（Asset Usage Tracking）

**问题**: 怎么知道 Agent 回答时用了哪些资产？

**方案 A: 显式声明**（推荐）
- Agent 回答时，要求它在 `<assets>` XML 标签中声明引用了哪些资产
- 或者通过 function calling：Agent 必须先调用 `query_asset` 工具获取资产，再回答

**方案 B: 隐式推断**
- 用 AOF 的图谱检索，匹配回答文本中的实体与知识图谱节点
- 通过决策溯源（decision_provenance）的 evidence 字段追踪

**方案 C: 混合**
- 优先用显式声明（function calling 方式）
- fallback 用隐式推断（文本匹配 + 图谱检索）

### 2. 归因算法

**简单版（MVP）**:
```python
def attribute(iterations: list[Iteration]) -> AttributionReport:
    # 1. 按资产改动分组迭代
    change_groups = group_by_asset_change(iterations)
    
    # 2. 计算每组改动前后的满意度差异
    for change, group in change_groups:
        before = avg_score([i for i in group if i.number < change.iteration_number])
        after = avg_score([i for i in group if i.number >= change.iteration_number])
        improvement = after - before
    
    # 3. 排序，输出关键洞察
    return sorted_by_improvement
```

**精细版（后续）**:
- 控制变量：确保其他资产没有同时改动
- 时间窗口：观察改动后 N 轮迭代的满意度趋势
- 因果推断：用 do-calculus 或倾向得分匹配，排除混杂因素

### 3. 训练数据提取

从满意迭代中提取 Agent-Tool 样本：

```python
def extract_training_data(iteration: Iteration) -> AgentToolSample:
    # 1. 构建资产查询工具 schema
    tools = [build_asset_query_tool(asset) for asset in iteration.assets_used]
    
    # 2. 构建 messages
    messages = [
        {"role": "system", "content": f"你是一个{iteration.session.domain}专家助手..."},
        {"role": "user", "content": iteration.session.problem_statement},
        # Agent 的推理过程（从 trace 中提取）
        {"role": "assistant", "content": iteration.agent_response},
    ]
    
    # 3. 构建 tool_calls（资产查询记录）
    tool_calls = [
        {
            "id": f"call_{usage.asset_id}",
            "type": "function",
            "function": {
                "name": "query_asset",
                "arguments": json.dumps({
                    "asset_type": usage.asset_type,
                    "asset_id": usage.asset_id,
                    "query": usage.usage_context,
                }),
            },
        }
        for usage in iteration.assets_used
    ]
    
    return AgentToolSample(
        messages=messages,
        tools=tools,
        tool_calls=tool_calls,
        reasoning=extract_reasoning(iteration.trace),
    )
```

## 与 AOF 的集成点

| Harness Trainer 功能 | 调用的 AOF 能力 |
|---------------------|----------------|
| 记录 Agent 决策链 | `DecisionProvenanceStore.record()` |
| 对话 trace → 训练样本 | `RawTrajectoryGenerator` |
| 资产版本管理 | `ontology_governance`（OWL + SHACL 版本仓） |
| 资产使用追踪 | `graph_retrieval.load_graph_nodes_edges()` |
| 训练数据导出 | `TrainingDataPipeline` |
| 知识库查询 | `enhanced_search.search_with_intent()` |

## 待讨论的关键问题

### Q1: 资产使用追踪的实现方式

- **选项 A**: Agent 通过 function calling 显式查询资产（最精确，但需改造 Agent）
- **选项 B**: 通过文本匹配 + 图谱检索隐式推断（无需改造 Agent，但精度低）
- **选项 C**: 混合（优先显式，fallback 隐式）

**我的倾向**: 选项 C。MVP 用选项 B 快速验证，长期用选项 A 提升精度。

### Q2: 专家评分的维度与权重

当前设计 5 个维度（structure/accuracy/completeness/style/reasoning），但不同场景权重不同：
- 商业分析: structure + reasoning 权重高
- 客服: style + completeness 权重高
- 法律文书: accuracy + structure 权重高

**是否支持场景自定义权重？**

### Q3: 满意标准的判定

- **选项 A**: 单轮 overall >= threshold（简单，但可能波动）
- **选项 B**: 连续 N 轮 overall >= threshold（稳定，但耗时）
- **选项 C**: 多维度都 >= threshold（严格，但可能难以达到）

**我的倾向**: 选项 A 做 MVP，选项 B 做长期。

### Q4: 归因的置信度

简单归因（改动前后对比）容易受混杂因素影响（其他资产同时改动、Agent 随机性等）。

**是否需要引入控制实验？** 如：固定其他资产，只改动一个，观察效果。

### Q5: 训练数据的复用

一个 HarnessSession 产出的训练数据，是否可以用于**同类问题**（而不仅是原问题）？

例如：用"分析 Q3 华东区库存"驯化的 Agent，其训练数据是否可用于"分析 Q4 华南区库存"？

**我的判断**: 可以，但需要标记问题的"模式类型"（如"区域库存分析"），同类问题共享训练数据。

## 实施建议

### Phase 1: MVP（2-3 周）
1. `HarnessSession` + `Iteration` 数据模型
2. 简单的资产使用追踪（文本匹配）
3. 专家评分（单维度 overall）
4. 简单归因（改动前后对比）
5. 从满意迭代提取 SFT 样本（复用 RawTrajectory）

### Phase 2: 精细归因（2-3 周）
1. function calling 方式资产查询
2. 多维度专家评分
3. 控制实验（单变量改动）
4. 归因置信度评估

### Phase 3: 产品化（2-3 周）
1. Web UI：驯化会话管理、迭代可视化、归因报告
2. 训练数据自动导出到模型微调 pipeline
3. 跨会话训练数据复用（同类问题聚合）

## 产出物

- 设计文档（本文档）
- `bridge/harness_trainer/` 模块
- `tests/test_harness_trainer.py` 测试
- Web UI（可选 Phase 3）
