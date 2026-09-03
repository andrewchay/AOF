# CTO 与 AOF 集成调研报告

> 调研时间: 2026-09-02  
> 调研范围: ClinicalTrialOntology/CTO GitHub 仓库、AOF ontology 适配代码、cognee RDFLibOntologyResolver

---

## 一、CTO 概览

### 1.1 基本信息

| 属性 | 值 |
|------|-----|
| **全称** | Core Ontology of Clinical Trials |
| **GitHub** | https://github.com/ClinicalTrialOntology/CTO/ |
| **命名空间** | `http://purl.obolibrary.org/obo/cto.owl` |
| **顶级本体** | BFO (Basic Formal Ontology) |
| **所属联盟** | OBO Foundry |
| **许可证** | CC BY 4.0 |
| **文件格式** | OWL/XML (非 RDF/XML) |
| **文件大小** | ~1MB (20,063 行) |

### 1.2 规模统计

| 实体类型 | 数量 |
|---------|------|
| **Classes** | 298 |
| **ObjectProperties** | 18 |
| **DataProperties** | 0 |
| **AnnotationProperties** | 67 |
| **NamedIndividuals** | 6 |
| **SubClassOf axioms** | 306 |
| **EquivalentClasses** | 4 |
| **Declarations** | 392 |

### 1.3 命名空间结构

CTO 是一个**合并本体 (merged ontology)**，包含多个导入的外部本体：

| 前缀 | 来源本体 | 用途 |
|------|---------|------|
| `obo:BFO_` | Basic Formal Ontology | 顶级实体/过程/关系框架 |
| `obo:CTO_` | CTO 核心 (0000001-0000220) | 临床试验核心术语 |
| `obo:OBI_` | Ontology for Biomedical Investigations | 生物医学调查方法 |
| `obo:IAO_` | Information Artifact Ontology | 信息实体 |
| `obo:NCIT_C` | NCI Thesaurus | 癌症/医学术语 |
| `obo:OGMS_` | Ontology for General Medical Science | 通用医学 |
| `obo:OPMI_` | Ontology of Precision Medicine and Investigation | 精准医学 |
| `obo:OAE_` | Ontology of Adverse Events | 不良事件 |
| `obo:VO_` | Vaccine Ontology | 疫苗相关 |
| `obo:CHEBI_` | Chemical Entities of Biological Interest | 化学实体 |

### 1.4 与 AOF CSO 的对比

| 维度 | AOF CSO (当前) | 真实 CTO |
|------|---------------|----------|
| **规模** | 7 类 + 4 关系 | 298 类 + 18 关系 + 306 继承关系 |
| **命名空间** | `http://example.org/cso/ontology#` | `http://purl.obolibrary.org/obo/cto.owl` |
| **格式** | RDF/XML | OWL/XML |
| **顶级本体** | 无 | BFO |
| **外部导入** | 无 | 10+ 个 OBO 本体 |
| **用途** | AOF 验证演示 | 临床试验标准化 (ClinicalTrials.gov / WHO) |
| **维护状态** | 内部手动维护 | 社区活跃维护 (ICBO 会议发表) |

---

## 二、AOF Ontology 适配机制分析

### 2.1 数据流

```
spec.ontology.file
    ↓
bridge/ontology_adapter/adapter.py::apply_ontology()
    ↓
cognee.modules.ontology.rdf_xml.RDFLibOntologyResolver
    ↓
rdflib.Graph.parse(ontology_file, format="xml")  ← 要求 RDF/XML
    ↓
build_lookup() → classes + individuals 字典
    ↓
FuzzyMatchingStrategy(cutoff=0.8) 模糊匹配
```

### 2.2 关键代码

**AOF 适配层** (`bridge/ontology_adapter/adapter.py`):
```python
def apply_ontology(spec: dict[str, Any]) -> dict[str, Any] | None:
    ontology = spec.get("ontology") or {}
    ontology_file = ontology.get("file")
    if not ontology_file:
        return None
    
    from cognee.modules.ontology.rdf_xml.RDFLibOntologyResolver import RDFLibOntologyResolver
    from cognee.modules.ontology.matching_strategies import FuzzyMatchingStrategy
    
    cutoff = float(ontology.get("matching_cutoff", 0.8))
    strategy = FuzzyMatchingStrategy(cutoff=cutoff)
    resolver = RDFLibOntologyResolver(ontology_file=ontology_file, matching_strategy=strategy)
    return {"ontology_config": {"ontology_resolver": resolver}}
```

**cognee Resolver** (`RDFLibOntologyResolver.py`):
- 使用 `rdflib.Graph.parse(file_path)` 解析 OWL
- 仅支持 **RDF/XML** 格式 (`format="xml"`)
- 构建 `classes` + `individuals` 查找表
- 通过 `FuzzyMatchingStrategy` (difflib.get_close_matches) 做实体匹配

### 2.3 匹配策略

```python
class FuzzyMatchingStrategy:
    def __init__(self, cutoff: float = 0.8):
        self.cutoff = cutoff  # 默认 0.8
    
    def find_match(self, name: str, candidates: List[str]) -> Optional[str]:
        # 1. 精确匹配
        if name in candidates:
            return name
        # 2. difflib 模糊匹配
        best_match = difflib.get_close_matches(name, candidates, n=1, cutoff=self.cutoff)
        return best_match[0] if best_match else None
```

---

## 三、集成障碍与解决方案

### 3.1 核心障碍：格式不兼容 ❌

| 问题 | 详情 |
|------|------|
| **CTO 格式** | OWL/XML ( Manchester OWL Syntax 的 XML 序列化) |
| **cognee 要求** | RDF/XML |
| **rdflib 支持** | 仅 RDF/XML, Turtle, N-Triples 等，**不支持 OWL/XML** |
| **结果** | `rdflib.exceptions.ParserError` |

**验证结果**:
```
❌ Parse failed: ParserError
   Error: Repeat node-elements inside property elements: http://www.w3.org/2002/07/owl#Literal
```

### 3.2 解决方案对比

| 方案 | 描述 | 复杂度 | 维护成本 | 推荐度 |
|------|------|--------|---------|--------|
| **A. 格式转换** | 用 OWL API / Protégé 将 CTO 转为 RDF/XML | 低 | 低 | ⭐⭐⭐⭐⭐ |
| **B. 子集提取** | 从 CTO 提取核心类+关系，生成简化 RDF/XML | 中 | 中 | ⭐⭐⭐⭐ |
| **C. 扩展 Resolver** | 在 cognee 中增加 OWL/XML 解析支持 | 高 | 高 | ⭐⭐ |
| **D. 使用 OBO 格式** | 下载 CTO 的 OBO 格式，再转 RDF/XML | 低 | 低 | ⭐⭐⭐⭐ |

---

## 四、推荐集成方案

### 方案 A: CTO → RDF/XML 格式转换 (推荐)

#### 步骤 1: 下载并转换 CTO

```bash
# 方法 1: 使用 ROBOT (命令行 OWL 工具)
brew install robot  # 或从 https://robot.obolibrary.org/ 下载

# 转换为 RDF/XML
robot convert --input cto.owl --output cto.rdf.xml --format rdfxml

# 方法 2: 使用 Protégé 桌面应用
# 1. 打开 cto.owl
# 2. File → Save As → RDF/XML

# 方法 3: 使用 OWL API (Java)
# 参见: https://github.com/owlcs/owlapi
```

#### 步骤 2: 验证转换后的文件

```python
from rdflib import Graph

g = Graph()
g.parse('cto.rdf.xml', format='xml')
print(f"Triples: {len(g)}")  # 应 > 10000
```

#### 步骤 3: 配置 AOF spec

```json
{
  "knowledge_repo": "/path/to/clinical_trial_docs",
  "dataset": "cto_clinical_trials",
  "ontology": {
    "file": "/path/to/cto.rdf.xml",
    "matching_cutoff": 0.75
  }
}
```

#### 步骤 4: 调整 matching_cutoff

CTO 的类名使用 OBO ID (如 `CTO_0000001`)，标签在 `rdfs:label` 中：

```python
# 测试不同 cutoff 的匹配效果
from cognee.modules.ontology.rdf_xml.RDFLibOntologyResolver import RDFLibOntologyResolver
from cognee.modules.ontology.matching_strategies import FuzzyMatchingStrategy

resolver = RDFLibOntologyResolver(
    ontology_file='cto.rdf.xml',
    matching_strategy=FuzzyMatchingStrategy(cutoff=0.75)
)

# 测试术语
test_terms = ['clinical trial', 'intervention', 'endpoint', 'adverse event', 'eligibility criterion']
for term in test_terms:
    match = resolver.find_closest_match(term, 'classes')
    print(f"'{term}' -> {match}")
```

### 方案 B: 提取 CTO 核心子集

如果完整 CTO 过大或匹配效果不佳，可以提取核心类：

```python
# extract_cto_core.py
from rdflib import Graph, Namespace, RDF, RDFS, OWL

# 加载转换后的 CTO
g = Graph()
g.parse('cto.rdf.xml', format='xml')

CTO = Namespace("http://purl.obolibrary.org/obo/")

# 提取核心 CTO 类 (排除 BFO/IAO 等上层本体)
core_classes = []
for s in g.subjects(RDF.type, OWL.Class):
    if 'CTO_' in str(s):
        labels = list(g.objects(s, RDFS.label))
        label = labels[0] if labels else str(s)
        core_classes.append((str(s), label))

print(f"Core CTO classes: {len(core_classes)}")
# 生成简化 OWL
# ... (类似 cso_oncology.owl 的格式)
```

---

## 五、集成后的预期效果

### 5.1 实体抽取能力提升

| 领域概念 | AOF 无 Ontology | AOF + CSO | AOF + CTO |
|---------|----------------|-----------|-----------|
| 临床试验阶段 | `phase`, `study` | `clinicaltrial` | `CTO_0000001` (clinical trial) |
| 干预措施 | `treatment`, `drug` | `drug` | `CTO_0000007` (clinical intervention) |
| 终点指标 | `endpoint` | `endpoint` | `CTO_0000012` (clinical trial endpoint) |
| 不良事件 | (通用实体) | - | `CTO_0000016` (adverse event) |
| 纳入标准 | `criterion` | - | `CTO_0000018` (eligibility criterion) |
| 随机化 | `randomization` | - | `CTO_0000023` (randomization process) |

### 5.2 关系抽取能力提升

CTO 的 18 个 ObjectProperty 包括：
- `has_participant` (OBI)
- `has_specified_input` (OBI)
- `has_specified_output` (OBI)
- `realizes` (BFO)
- `part_of` (BFO)
- ...

这些关系可以约束图谱中的实体连接方式。

---

## 六、实施建议

### 6.1 短期 (1-2 天)

1. **转换 CTO 格式**: 使用 ROBOT 或 Protégé 将 `cto.owl` 转为 RDF/XML
2. **验证解析**: 确认 rdflib 能正确解析转换后的文件
3. **测试匹配**: 用 AOF 的 CSO 验证脚本测试 CTO 的匹配效果

### 6.2 中期 (1 周)

1. **调整 matching_cutoff**: 根据测试结果调整模糊匹配阈值
2. **提取核心子集**: 如果完整 CTO 匹配效果不佳，提取核心类
3. **对比实验**: 对比 "无 ontology" / "CSO" / "CTO" 三种配置的抽取效果

### 6.3 长期 (1 月)

1. **Ontology Governance**: 将 CTO 纳入 AOF 的 ontology governance 流程
2. **版本管理**: 跟踪 CTO 的更新，建立自动化转换流水线
3. **领域扩展**: 基于 CTO 扩展其他医学领域本体 (如 OAE 不良事件本体)

---

## 七、风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| CTO 类名是 OBO ID，不直观 | 抽取结果的实体名难读 | 在展示层映射到 `rdfs:label` |
| CTO 包含大量 BFO 上层类 | 可能引入无关的哲学实体 | 过滤只保留 CTO_/OBI_/NCIT_ 等核心类 |
| 模糊匹配可能误匹配 | 实体类型错误 | 调低 cutoff 或改用精确匹配 |
| 文件体积大 (~1MB) | 加载时间增加 | 提取子集或缓存 lookup |
| CTO 更新频繁 | 维护成本 | 建立自动化转换 + 版本锁定 |

---

## 八、参考资源

- **CTO GitHub**: https://github.com/ClinicalTrialOntology/CTO/
- **CTO 论文**: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9389640/
- **OBO Foundry**: https://obofoundry.org/
- **ROBOT 工具**: https://robot.obolibrary.org/
- **OWL API**: https://github.com/owlcs/owlapi
- **AOF CSO 验证**: `/Users/chaihao/LLM/AOF/examples/cso_validation/`

---

*报告生成: 2026-09-02 by Gravitas Agent*
