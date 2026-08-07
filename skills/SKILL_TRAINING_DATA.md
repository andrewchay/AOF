# SKILL: AOF 训练数据生成

> 版本: v1.0
> 适用范围: 基于 AOF 知识图谱和文档生成 AI/Agent 训练数据集

---

## 概述

AOF 训练数据生成器（Training Data Generator）基于已构建的知识图谱和文档，自动生成三种类型的结构化训练数据：

| 类型 | 用途 | 输出格式 |
|------|------|---------|
| **SFT** | LLM 监督微调 | OpenAI chat completions (messages) |
| **RAG Eval** | 检索增强生成评估 | question-answer-context 三元组 |
| **Agent Tool** | Agent 工具调用训练 | OpenAI function calling |

---

## 快速开始

### 1. 通过 API 生成训练数据

```bash
curl -X POST http://localhost:8787/v1/training-data/generate \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_name": "my_dataset",
    "generators": ["sft", "rag_eval"],
    "max_samples": 500,
    "enable_deduplication": true
  }'
```

返回:
```json
{
  "status": "success",
  "job_id": "uuid",
  "result": {
    "total_samples": 420,
    "samples_by_type": {"sft": 200, "rag_eval": 220},
    "output_files": ["/path/to/training_data.jsonl"],
    "duration_seconds": 12.5
  }
}
```

### 2. 查询任务状态

```bash
curl http://localhost:8787/v1/training-data/jobs/{job_id}
```

### 3. 下载结果

```bash
curl http://localhost:8787/v1/training-data/jobs/{job_id}/download \
  -o training_data.jsonl
```

---

## 生成器选择指南

### 何时使用 SFT 生成器？

- 目标：微调一个领域知识问答助手
- 输入：实体丰富的知识图谱 + 文档
- 产出：instruction-response 对话对
- 适用框架：OpenAI fine-tuning、vLLM、Axolotl、LLaMA-Factory

### 何时使用 RAG Eval 生成器？

- 目标：评估 RAG 系统的检索和生成质量
- 输入：结构化的知识图谱（节点/边/路径清晰）
- 产出：带 ground truth 的 QA 对
- 适用场景：检索评测、答案准确性评估

### 何时使用 Agent Tool 生成器？

- 目标：训练 Agent 调用业务工具
- 输入：包含策略/操作概念的知识图谱
- 产出：function calling 样本
- 适用框架：OpenAI function calling、LangChain tools

---

## 生成策略详解

### SFT 生成策略

| 策略 | 数据源 | 说明 |
|------|--------|------|
| 实体问答 | 图谱节点 | 基于实体属性生成 "什么是 X？" 类样本 |
| 关系推理 | 三元组 | 基于关系生成 "A 和 B 有什么关系？" 类样本 |
| 文档摘要 | 文档 chunks | 基于文本生成摘要/要点提取任务 |
| 多轮对话 | 邻居子图 | 基于子图路径模拟连贯对话 |

### RAG Eval 生成策略

| 策略 | 问题类型 | 难度 |
|------|---------|------|
| 事实型 | 基于单个实体/属性 | Easy |
| 关系型 | 需要连接两个实体 | Medium |
| 聚合型 | 需要汇总多个节点 | Medium |
| 推理型 | 需要多跳路径推理 | Hard |

### Agent Tool 生成策略

| 策略 | 工具类型 | 说明 |
|------|---------|------|
| 实体查询 | query_knowledge_graph | 基于实体生成查询调用 |
| 关系分析 | query_knowledge_graph | 基于关系生成分析调用 |
| 策略执行 | execute_strategy | 基于策略节点生成执行调用 |
| 报告生成 | generate_report | 基于主题生成报告调用 |

---

## 质量控制

### 内置过滤规则

- **去重**：基于 BLAKE2b 哈希的内容去重
- **长度过滤**：问题/答案最小最大长度限制
- **多样性评分**：基于 n-gram Jaccard 相似度
- **工具一致性**：验证 tool_calls 引用的工具是否存在于 schemas 中

### 配置选项

```python
QualityConfig(
    enable_deduplication=True,      # 启用去重
    min_question_length=5,          # 最小问题长度
    max_question_length=500,        # 最大问题长度
    min_answer_length=10,           # 最小答案长度
    max_answer_length=8000,         # 最大答案长度
    min_diversity_score=0.3,        # 最低多样性评分
    max_duplicate_ratio=0.1,        # 最大重复率
)
```

---

## 常见失败模式与解决方案

### 1. 生成样本数为 0

**原因**：图谱为空或节点没有 name/description 属性
**解决**：
- 检查图谱数据是否已正确加载
- 确认节点属性中包含 `name` 或 `title` 字段
- 使用 `graph_analytics.py` 验证图谱健康度

### 2. 样本质量不高

**原因**：属性值太短或描述不够丰富
**解决**：
- 启用 `llm_enhance` 进行 LLM 润色（需配置 LLM API）
- 调整 `quality_threshold` 过滤阈值
- 增加原始文档的 chunks 数量

### 3. 跨生成器重复

**原因**：SFT 和 RAG Eval 基于相同实体生成了相似样本
**解决**：
- 启用 `enable_deduplication=True`
- 使用全局去重（流水线自动处理）
- 减少单个生成器的 max_samples

### 4. Agent Tool 样本工具不匹配

**原因**：图谱中缺少策略/操作类节点
**解决**：
- 在图谱中为策略节点添加 `type: strategy` 或 `label: Action`
- 提供外部 tool_schema_source JSON 文件
- 使用默认通用工具模板

---

## 与现有流程的集成

```
Ingest → Cognify → [Training Data Generation] → Export
                ↓
           同时可用于:
           - Query/Analytics
           - Maintain
           - Markdown Export
```

训练数据生成**不修改**现有图谱数据，是只读操作。

---

## 参考

- 模块: `bridge/training_data/`
- 导出器: `exporters/training_data_exporter.py`
- API: `/v1/training-data/*`
- 测试: `tests/test_training_data_*.py`
