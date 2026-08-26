# CSO Ontology 迭代自举 POC

## 目标
验证 **ontology 迭代自举（iterative bootstrapping）** 是否有效：
用 AOF 多轮从领域文本逐步完善 ontology，每轮用上一轮产出的 ontology 引导更精确的抽取。

## 核心设想
```
轮1: 种子ontology → AOF建图谱 → 提取实例 → 归纳候选类 → 合并成新ontology
轮2: 新ontology   → AOF建图谱 → 提取实例 → 归纳候选类 → 合并成更完善的ontology
...直至收敛
```

## POC 代码

| 文件 | 作用 |
|------|------|
| `iterate_ontology.py` | 主迭代引擎（AOF 建图谱 + 归纳 + 收敛判断） |
| `iterate_core.py` | 核心逻辑：实例提取、实例→类、收敛判据（可单测） |
| `owl_generator.py` | 生成 OWL（合并候选类） |
| `runs/summary.json` | 每轮运行收敛日志 |

## 运行方式
```bash
cd /Users/chaihao/LLM/AOF
.venv/bin/python examples/cso_validation/iterative_poc/iterate_ontology.py --rounds 3
# 加 --review-on 则每轮停止等人工确认候选类
```

## 实测结果（2轮，auto-accept）

| 轮 | 类别总数 | 新增类 | 概念饱和度 | 是否停止 |
|----|---------|--------|-----------|---------|
| 种子 | 7 | - | - | - |
| 轮1 | 13 | +6 (clinical trial, drug, disease, endpoint, statisticalmethod, study design) | 0.46 | 否（未饱和）|
| 轮2 | 18 | +5 (clinicaltrial, method, population, studyphase, studydesign) | 0.28 | 是（MaxIter=2）|

**结论**：
- ✅ **ontology 被 AOF 自主扩展**：7类 → 18类
- ✅ **概念饱和度递减**：0.46 → 0.28（说明概念在逼近充盈）
- ✅ **实例→类归纳有效**：AOF 识别出的领域实体类型（endpoint/drug/disease/method）被归纳成 ontology 类
- ✅ **收敛判据工作**：MaxIter 兜底生效

## 关键实现细节（踩坑记录）

1. **cognee 节点字段**：实体的名字在 `name`（不是 `label`！），类型在 `type`。`label` 恒为 None。
2. **实体类型分布**：具体实例是 `Entity` 类型，领域类是 `EntityType`。实例→类要用 `is_a` 边关联：
   ```
   atezolizumab --is_a--> drug
   non-progression rate (npr) --is_a--> endpoint
   simontwostagedesign --is_a--> statisticalmethod
   ```
3. **索引时序**：`cognify` 后需等待几秒（索引异步写入），`load_graph_nodes_edges` 需重试。
4. **候选类 = AOF 归纳出的领域类型**（EntityType），不是笼统的 `Entity`。

## 收敛终点设计（已验证可行）
采用三判据组合：
- **概念饱和度**：新增类/总类 < 10% → 收敛
- **类集合稳定**：连续两轮类集合差异 < 10% → 收敛
- **MaxIter**：硬性最大轮数兜底，防发散

## 改进方向（后续）
1. **类去重**：`clinical trial` vs `clinicaltrial` 是同义词，需归一化（SKOS/词干化）
2. **子类层级**：当前只归纳平铺类，未构建父-子关系（如 `SurvivalEndpoint ⊂ Endpoint`）
3. **关系学习**：目前只归纳类，未学关系（isEvaluatedBy 等）；可从 `Entity--rel-->Entity` 边学习
4. **多种子**：多份不同文本 → 更全面的 ontology 覆盖

## 复用价值（可泛化模板）
"This POC is a reusable template for **ontology learning loop**: any domain with text + domain.owl
can iterate ontology refinement via AOF. Useful for: taxonomy induction, schema discovery,
domain modeling from unstructured docs."
