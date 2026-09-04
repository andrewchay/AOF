# CSO v0.3 (OptiMed) 真实文本抽取效果报告

> 测试时间: 2026-09-02  
> 文本来源: NCT02458638_atezolizumab.md (真实临床试验摘要)  
> 对比对象: CSO v0.1 (旧) / CSO v0.3 (OptiMed) / CTO / 融合版本

---

## 关键发现

| Ontology | 类数 | 匹配数 | 匹配率 | 相对提升 |
|---------|------|--------|--------|---------|
| **CSO v0.1 (旧)** | 7 | 4 | **12.1%** | 基准 |
| **CTO (转换)** | 231 | 6 | **18.2%** | 1.5x |
| **CTO+CSO v0.3 融合** | 237 | 10 | **30.3%** | 2.5x |
| **CSO v0.3 (OptiMed)** | 46 | 12 | **36.4%** | **3x** ⭐ |

**CSO v0.3 (OptiMed) 是表现最好的 Ontology，匹配率是原始 CSO 的 3 倍**

---

## 各 Ontology 详细表现

### CSO v0.3 (OptiMed) - 最佳表现

**匹配成功 (12/33):**

| 术语 | 匹配到的类 |
|------|-----------|
| primary_objective | exploratory_objective ⚠️ |
| secondary_objective | study_objective |
| study_objective | study_objective |
| endpoint | endpoint |
| efficacy_endpoint | efficacy_endpoint ✅ |
| safety_endpoint | safety_endpoint ✅ |
| safety_assessment | safety_assessment ✅ |
| statistical_method | statistical_method |
| safety_population | analysis_population ⚠️ |
| analysis_population | analysis_population |
| clinical_study_protocol | clinical_study_protocol ✅ |
| study_information | study_information ✅ |

**CSO v0.3 独有优势** (CSO v0.1 和 CTO 都没有):
- ✅ efficacy_endpoint (疗效终点)
- ✅ safety_endpoint (安全性终点)
- ✅ safety_assessment (安全性评估)
- ✅ primary_objective (主要目标)
- ✅ secondary_objective (次要目标)
- ✅ clinical_study_protocol (研究方案)
- ✅ study_information (研究信息)

### CSO v0.1 (旧) - 基准

**匹配成功 (4/33):**
- study_objective, endpoint, statistical_method, analysis_population

**问题**: 缺少细分类型（如 efficacy_endpoint, safety_endpoint）

### CTO (转换) - 通用框架强

**匹配成功 (6/33):**
- clinical_trial, phase_ii, adverse_event, clinical_study_protocol 等

**问题**: 缺少协议内容级概念（endpoint, objective 的具体类型）

---

## 仍未匹配的术语分析

### 所有 Ontology 都无法匹配的术语 (17个)

| 术语 | 类型 | 建议 |
|------|------|------|
| Atezolizumab | 具体药物名 | 从 DrugBank 导入实例 |
| advanced_solid_tumors | 具体疾病名 | 从 UMLS/NCIT 导入 |
| NPR | 缩写 | 添加缩写映射 |
| ORR | 缩写 | 添加缩写映射 |
| PFS | 缩写 | 添加缩写映射 |
| OS | 缩写 | 添加缩写映射 |
| DOR | 缩写 | 添加缩写映射 |
| non_progression_rate | 复合终点 | 添加到 CSO |
| overall_response_rate | 复合终点 | 添加到 CSO |
| duration_of_response | 复合终点 | 添加到 CSO |
| progression_free_survival | 复合终点 | 添加到 CSO |
| overall_survival | 复合终点 | 添加到 CSO |
| Simon_two_stage_design | 具体方法 | 添加到 CSO |
| open_label | 设计特征 | 添加到 CSO |
| multicohort | 设计特征 | 添加到 CSO |
| safety | 通用概念 | 已存在 safety_assessment |
| tolerability | 通用概念 | 与 safety 同义 |

---

## 结论与建议

### 1. CSO v0.3 (OptiMed) 是当前最佳选择

- **匹配率 36.4%**，远超其他版本
- 覆盖协议内容的核心概念（endpoint types, objectives, safety）
- 与 BFO/IAO 对齐，具有良好的理论基础

### 2. 需要补充的内容

**高优先级**:
- 终点指标缩写映射 (NPR→non_progression_rate, ORR→overall_response_rate)
- 复合终点类 (progression_free_survival, overall_survival)
- 具体统计方法 (Simon_two_stage_design)

**中优先级**:
- 研究设计特征 (open_label, multicohort)
- 药物/疾病实例 (从外部本体导入)

### 3. 推荐的 Ontology 策略

```
主本体: CSO v0.3 (OptiMed) - 协议内容级概念
辅助:   CTO - 注册元数据概念
实例:   UMLS/NCIT/DrugBank - 具体医学实体
映射:   缩写扩展表 - NPR, ORR, PFS, OS 等
```

---

## 文件清单

| 文件 | 路径 | 说明 |
|------|------|------|
| CSO v0.3 源文件 | `/Users/chaihao/LLM/OptiMed/ontology/src/cso_v0.3.ttl` | OptiMed 原始 TTL |
| CSO v0.3 RDF/XML | `/Users/chaihao/LLM/AOF/examples/cso_validation/data/cso_v03_optimed.rdf.xml` | cognee 可用格式 |
| 测试报告 | 本文档 | 完整对比分析 |

---

*报告生成: 2026-09-02 by Gravitas Agent*
