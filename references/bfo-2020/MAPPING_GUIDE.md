# AOF 领域本体挂接 BFO 的映射指南（v0.1 草稿）

> **状态**: 设计期对齐参考，非强制规范。所有草稿本体在审阅阶段可选地参照本指南挂接 BFO。
> **依据**: BFO 2020 (`references/bfo-2020/bfo-core.owl`)，CC BY 4.0，ISO/IEC 21838-2

## 1. 为什么需要这份指南

AOF 的语义抽提（`cognify`）默认产出**平铺本体**——所有顶层类都是 `owl:Thing` 的直接子类，实体/关系命名通用（如 `person`、`is_a`、`is_part_of`）。已验证结论：注入领域本体后抽取质量显著提升（见 `ontologies/README_aof_validation.md` 的 CSO 对照实验）。

引入 BFO 顶层骨架的价值：

| 价值 | 说明 |
|---|---|
| 跨领域互操作 | 财务、供应链、医疗域本体共享同一顶层二分（continuant / occurrent），Agent 跨域推理时语义可对齐 |
| 抽提质量校验 | BFO 的核心区分（role vs disposition、object vs process）是一组强分类学检查，可暴露"什么都挂 Entity"的偷懒建模 |
| 推理语义复用 | `inheres_in`、`participates_in`、`occurs_in` 等关系语义由 ISO 标准化定义，避免自定义歧义 |

**但不强制**：BFO 学习成本高，且偏物理/科学世界观。本指南采用**"设计期对齐，运行期不强依赖"**策略。

## 2. BFO 顶层结构速览

```
entity
├── continuant（持续体：时间中持续存在）
│   ├── independent continuant（独立持续体）
│   │   ├── material entity（物体 / 物质实体）
│   │   │   ├── object（个体对象）
│   │   │   ├── fiat object part（约定划分出的部分）
│   │   │   └── object aggregate（对象集合）
│   │   └── immaterial entity（非实体：地点、空间区域、边界）
│   ├── specifically dependent continuant（专依持续体：依赖特定载体）
│   │   ├── quality（质量：颜色、形状）
│   │   ├── relational quality（关系质量）
│   │   └── realizable entity（可实现体）
│   │       ├── disposition（倾向：在条件下会发生的性质）
│   │       ├── role（角色：由语境赋予的功能位）
│   │       └── function（功能：被设计来实现的目的）
│   └── generically dependent continuant（通依持续体：可复制、可传承的模式）
│       └── （信息内容实体——BFO 本身不在 core 中定义，CCO 补充）
└── occurrent（发生体：在时间里展开）
    ├── process（过程）
    │   └── history（历史：一个对象的全部过程总和）
    ├── process boundary（过程边界）
    ├── temporal region（时间区域）
    │   ├── temporal instant（时间点）
    │   └── temporal interval（时间段）
    └── spatiotemporal region（时空区域）
```

**核心区分（最易混淆的三组）**：

| 区分 | 判别方法 | 例子 |
|---|---|---|
| continuant vs occurrent | 问"它是否在时间中保持同一 identity？" | 合同（continuant）vs 签约过程（occurrent） |
| disposition vs role | 问"移除语境后性质是否还在？" | 药的"退烧倾向"（disposition）vs 某人作为"法定代表"（role，脱离语境即失效） |
| object vs process | 问"它是名词（东西）还是动词（发生）？" | 采购订单（object 的信息承载）vs 采购执行流程（process） |

## 3. AOF 业务概念的常见映射

### 3.1 财务控制平面试点域（AOF 首个业务闭环）

| AOF 概念 | 建议 BFO 挂点 | 关系 |
|---|---|---|
| 合同 / 订单 / 发票 | `generically dependent continuant`（信息模式，需载体） | `generically depends on` → 纸质/电子文件 |
| 付款义务 / 应收权利 | `role` 或 `disposition`（视语境） | `inheres in` → 合同或法人实体 |
| 指标口径 / 语义定义 | `generically dependent continuant` | `is concretized by` → 文档或数据库字段 |
| 财务例外事件 | `disposition`（被识别出的倾向） | `inheres in` → 数据集/流程 |
| 审批动作 | `process` | `has participant` → 审批人（role）；`occurs in` → 时间区域 |
| 行动包 / Plan | `generically dependent continuant`（计划模式） | `is realized in` → 执行过程 |
| 审批人 / 责任人 | `role`（非"人"本身——人是 `object`，角色是依附于人的 role） | `inheres in` → person object |
| 财务数据集 | `generically dependent continuant` | — |
| 决策记录 | `generically dependent continuant`（证据/记录模式） | — |
| 补偿 / 回滚 | `process`（含 `precedes` 关系） | — |

### 3.2 通用判别流程

```
新概念 X 需要挂接 BFO 时，按顺序问：

1. X 是"发生中的事"吗？
   → 是：occurrent
      → 有明确起止 → process
      → 只是一个时间点/时段 → temporal region
   → 否：continuant

2. X 是"依赖某个特定东西才存在"吗？
   → 是：specifically dependent continuant
      → 是某物被赋予的职能/身份 → role
      → 是某物内在的倾向/能力 → disposition
      → 是可感知的属性 → quality
   → 否：继续

3. X 是"可复制、可存在于多个载体中的模式"吗？
   （如：定义、规范、计划、数字文件内容）
   → 是：generically dependent continuant
   → 否：independent continuant
      → 是物质的东西 → material entity
      → 是空间/地点/边界 → immaterial entity
```

## 4. 挂接的实现方式

### 4.1 OWL 挂接（领域本体文件内）

```xml
<!-- 在领域本体中 import bfo-core -->
<owl:Ontology rdf:about="http://aof.example.org/finance-ontology">
    <owl:imports rdf:resource="http://purl.obolibrary.org/obo/bfo/2020/bfo-core.owl"/>
</owl:Ontology>

<!-- 领域类挂到 BFO 顶层 -->
<owl:Class rdf:about="http://aof.example.org/finance-ontology/PurchaseOrder">
    <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000031"/>
    <!-- BFO_0000031 = generically dependent continuant -->
    <rdfs:label xml:lang="zh">采购订单</rdfs:label>
</owl:Class>
```

### 4.2 cognee 集成注意事项

- **ontology.file 建议**：cognee 的 `matching_cutoff` 是 embedding 匹配驱动的。BFO 顶层类定义极抽象，直接参与 embedding 匹配容易挂错。**建议**：
  - 领域本体挂接 BFO 后，将 `matching_cutoff` 提高（≥0.85）或改用基于 label 精确匹配策略
  - 在 cognify 阶段优先匹配领域类，BFO 挂点作为后验校验（由 `aof_doctor.py` 或审阅环节执行）

- **推理成本**：`bfo-core.owl` 的 OWL 表达已弱化为可判定子集，但完整 import 仍会增加推理时间。建议只在**审阅/验证阶段**加载 BFO，生产查询用裁剪后的领域本体（BFO 挂点以 `rdfs:subClassOf` 断言保留，不加载完整 BFO 公理）。

## 5. 质控检查（SHACL 约束草案）

以下为设计期审阅用的 SHACL 形状，可纳入 `aof_doctor.py` 或治理流水线的审阅环节：

```turtle
@prefix bfo: <http://purl.obolibrary.org/obo/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .

# 每个顶层领域类应挂接到 BFO（非 owl:Thing 直接子类）
aof:TopLevelClassMustAttachBFO a sh:NodeShape ;
    sh:targetClass owl:Class ;
    sh:sparql [
        sh:message "顶层类应挂载到 BFO continuant 或 occurrent，而非直接继承 owl:Thing" ;
        sh:ask """
            PREFIX owl: <http://www.w3.org/2002/07/owl#>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            PREFIX bfo: <http://purl.obolibrary.org/obo/>
            ASK {
                $this rdfs:subClassOf owl:Thing .
                FILTER NOT EXISTS {
                    $this rdfs:subClassOf ?parent .
                    FILTER (?parent != owl:Thing &&
                            ?parent != bfo:BFO_0000002 &&  # continuant
                            ?parent != bfo:BFO_0000003)    # occurrent
                }
            }
        """ ;
    ] .

# 财务域：合同/订单/发票应挂为 generically dependent continuant
aof:FinancialDocMustBeGDC a sh:NodeShape ;
    sh:targetClass aof:Contract, aof:PurchaseOrder, aof:Invoice ;
    sh:property [
        sh:path rdfs:subClassOf ;
        sh:hasValue bfo:BFO_0000031 ;  # generically dependent continuant
    ] .
```

## 6. 下一步行动

- [ ] 选 1 个财务域试点本体，按 §3.1 映射挂接，验证 cognee 抽取质量是否提升
- [ ] 把 §5 的 SHACL 约束接入 `aof_doctor.py` 的审阅流水线
- [ ] 对比"挂 BFO vs 不挂 BFO"的跨域查询可理解性（Agent 能否正确推理"审批人"是 role 而非 person）
- [ ] 若试点有效，再考虑是否将 BFO 挂接作为 `aof_add.py` 的可选 flag（`--bfo-align`）

## 7. 参考资料

- BFO 2020 GitHub: https://github.com/BFO-ontology/BFO-2020
- BFO 官方文档: https://basic-formal-ontology.org/
- Common Core Ontologies (CCO，BFO 中层扩展): https://github.com/CommonCoreOntology/CommonCoreOntologies
- Industrial Ontologies Foundry (IOF): https://github.com/iofoundry
- Building Ontologies with Basic Formal Ontology (Arp, Smith, Spear, 2015)
- AOF 本体验证记录: `ontologies/README_aof_validation.md`
