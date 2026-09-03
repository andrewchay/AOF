# FIBO 用于 AOF 财务域的集成指南（v0.1 草稿）

> **状态**: 设计期对齐参考，非强制规范。按需抽取，不克隆完整 FIBO 仓库。
> **来源**: EDM Council / OMG FIBO (MIT License)

## 1. 为什么选 FIBO

FIBO (Financial Industry Business Ontology) 是当前最权威的金融行业本体：

- **标准化**: OMG 标准化，EDM Council 维护，源自 2008 金融危机后的监管报告需求
- **共识**: 经过金融机构（银行、交易所、监管机构）多年审阅，代表行业共识
- **与 AOF 目标对齐**: 解决"金融数据跨系统歧义"、"合同义务机器可读"、"监管报告标准化术语"——与 AOF 财务控制平面的核心需求完全一致
- **基于 BFO**: FIBO 顶层继承 BFO 的 continuant/occurrent 骨架，与 `references/bfo-2020/` 的映射指南天然衔接

## 2. 已下载的 FIBO 子集

| 文件 | 大小 | 核心内容 |
|---|---|---|
| `FND/Agreements/Agreements.rdf` | 15KB | Agreement、Commitment、Obligor/Obligee、Bilateral/MultilateralAgreement |
| `FND/Agreements/Contracts.rdf` | 72KB | Contract、ContractParty、ContractualCommitment、ConditionPrecedent、BreachOfContract、CollateralAgreement 等 |

**命名空间**:
- `fibo-fnd-agr-agr:` = `https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Agreements/`
- `fibo-fnd-agr-ctr:` = `https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/`

## 3. AOF 财务域概念 → FIBO 映射

| AOF 概念 | FIBO 类 | FIBO 命名空间 |
|---|---|---|
| 合同 / 采购订单 | `Contract` | `fibo-fnd-agr-ctr:Contract` |
| 协议（广义） | `Agreement` | `fibo-fnd-agr-agr:Agreement` |
| 义务 / 承诺 | `Commitment` / `ContractualCommitment` | `fibo-fnd-agr-agr:Commitment` |
| 债务人 / 付款方 | `Obligor` | `fibo-fnd-agr-agr:Obligor` |
| 债权人 / 收款方 | `Obligee` | `fibo-fnd-agr-agr:Obligee` |
| 合同当事人 | `ContractParty` | `fibo-fnd-agr-ctr:ContractParty` |
| 先决条件 | `ConditionPrecedent` | `fibo-fnd-agr-ctr:ConditionPrecedent` |
| 违约 | `BreachOfContract` | `fibo-fnd-agr-ctr:BreachOfContract` |
| 抵押/担保协议 | `CollateralAgreement` | `fibo-fnd-agr-ctr:CollateralAgreement` |
| 合同里程碑 | `ContractMilestone` | `fibo-fnd-agr-ctr:ContractMilestone` |
| 审批人（角色） | `ContractParty` 的子类或 `fibo-fnd-pty-pty:PartyInRole` | 需扩展 |

## 4. 与 BFO 的衔接

FIBO 基于 BFO 构建，其顶层继承关系如下：

```
BFO entity
└── continuant
    └── independent continuant
        └── material entity
            └── object
                └── (FIBO 扩展) LegalEntity, Organization, Person
    └── generically dependent continuant
        └── (FIBO 扩展) Agreement, Contract, Commitment  ← 信息模式
    └── specifically dependent continuant
        └── realizable entity
            └── role
                └── (FIBO 扩展) Obligor, Obligee, ContractParty  ← 角色
└── occurrent
    └── process
        └── (FIBO 扩展) ContractExecution, BreachOfContract  ← 发生中的事
```

**关键衔接点**:
- `Agreement` / `Contract` → `generically dependent continuant`（可复制、可传承的信息模式）
- `Obligor` / `Obligee` / `ContractParty` → `role`（依附于特定法人的角色，脱离语境即失效）
- `BreachOfContract` → `process`（发生中的事件）

这与 `references/bfo-2020/MAPPING_GUIDE.md` §3.1 的映射完全一致，FIBO 恰好填补了 BFO 在金融域的空白。

## 5. 使用方式

### 5.1 设计期参考（推荐）

不 import 完整 FIBO，而是将 FIBO 的类定义作为**语义校验基准**：

```turtle
# AOF 财务本体中的合同类
aof:PurchaseOrder a owl:Class ;
    rdfs:subClassOf fibo-fnd-agr-ctr:Contract ;
    rdfs:label "采购订单"@zh ;
    skos:definition "企业向供应商发出的采购请求文档"@zh .
```

### 5.2 cognee 抽取锚点

类似 CSO oncology 实验，将 FIBO 类作为种子注入 cognee：

```json
{
  "ontology": {
    "file": "/Users/chaihao/LLM/AOF/references/fibo/FND/Agreements/Contracts.rdf",
    "matching_cutoff": 0.85,
    "matching_strategy": "fuzzy"
  }
}
```

**注意**: FIBO 使用 OMG Commons 本体（`cmns-*` 命名空间），cognee 的 `rdflib` resolver 需要能解析这些外部引用。建议先用 `Agreements.rdf`（较小、依赖少）做试点。

### 5.3 扩展方向

| AOF 需求 | 建议下载的 FIBO 模块 |
|---|---|
| 法人/组织/角色 | `FND/Organizations/`, `FND/Parties/` |
| 金融产品/服务 | `FBC/ProductsAndServices/` |
| 贷款/债务 | `LOAN/Loans/` |
| 证券/衍生品 | `SEC/Securities/`, `DER/Derivatives/` |
| 市场指数/利率 | `IND/Indicators/` |
| 业务流程 | `BP/Process/` |

## 6. 质控检查（SHACL 草案）

```turtle
@prefix fibo-ctr: <https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .

# 财务域的合同类应继承 FIBO Contract
aof:FinancialContractMustInheritFIBO a sh:NodeShape ;
    sh:targetClass aof:PurchaseOrder, aof:Contract, aof:Invoice ;
    sh:property [
        sh:path rdfs:subClassOf ;
        sh:hasValue fibo-ctr:Contract ;
    ] .

# 合同当事人应使用 FIBO 的 role 模型
aof:ContractPartyMustBeRole a sh:NodeShape ;
    sh:targetClass aof:ContractParty, aof:Approver, aof:Obligor ;
    sh:property [
        sh:path rdfs:subClassOf ;
        sh:hasValue fibo-ctr:ContractParty ;
    ] .
```

## 7. 下一步行动

- [ ] 用 `Agreements.rdf` + 财务例外场景文档跑 cognify 试点，对比"无 FIBO vs 有 FIBO"的抽取质量
- [ ] 验证 OMG Commons 外部引用在 cognee 中的解析兼容性
- [ ] 若试点有效，逐步按需下载 `FND/Organizations/`、`FND/Parties/` 等模块
- [ ] 起草 AOF 财务域本体 v0.1，以 FIBO 为骨架、以业务例外流程为血肉

## 8. 参考资料

- FIBO GitHub: https://github.com/edmcouncil/fibo
- FIBO Viewer (在线浏览): https://spec.edmcouncil.org/fibo/ontology/
- OMG FIBO 标准: https://www.omg.org/spec/FIBO/
- AOF BFO 映射指南: `references/bfo-2020/MAPPING_GUIDE.md`
- AOF 本体验证记录: `ontologies/README_aof_validation.md`
