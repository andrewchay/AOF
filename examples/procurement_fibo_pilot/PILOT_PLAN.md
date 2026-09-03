# 采购 + Order-to-Cash FIBO 映射试点

> **目标**: 用 FIBO 重新建模 AOF 采购口径治理示例，验证 FIBO 对财务域本体的语义增强价值
> **基准**: `examples/procurement_caliber_governance/` (15 个历史争议案例 + 语义资源定义)
> **参考**: `references/fibo/FND/Agreements/`, `references/bfo-2020/MAPPING_GUIDE.md`

## 1. 现有模型的问题（AOF 原生 YAML 语义资源）

当前 `semantic_resources.yaml` 中的概念是**平铺命名**的：

```yaml
# 当前：无本体骨架，纯命名约定
- resource_id: aof://acme/finance/metric/monthly_procurement_spend
  kind: Metric
  name: monthly_procurement_spend
  
- resource_id: aof://acme/finance/action-type/propose-caliber-change
  kind: ActionType
  name: propose-caliber-change
  
- resource_id: aof://acme/finance/workflow/caliber-change-approval
  kind: Workflow
```

**问题**:
- `Metric`、`ActionType`、`Workflow` 之间无语义关系（都是 `kind: X`，但 X 是什么？）
- `monthly_procurement_spend` 是"指标"还是"承诺"还是"义务"？语义模糊
- 跨域互操作时，另一个系统的"Contract"和这里的"framework_agreement"能否对齐？
- LLM 抽取时，"紧急采购"可能被识别为 `emergency_purchase`（名词/物体）还是 `emergency_procurement`（过程）？

## 2. FIBO 增强后的语义模型

### 2.1 顶层骨架（BFO + FIBO）

```
BFO entity
├── continuant
│   ├── generically dependent continuant
│   │   ├── fibo:Agreement          ← 协议（信息模式）
│   │   │   └── fibo:Contract      ← 合同（有法律约束力的协议）
│   │   │       └── aof:PurchaseOrder       ← 采购订单
│   │   │       └── aof:FrameworkAgreement  ← 框架协议
│   │   │       └── aof:EmergencyPurchasePolicy  ← 紧急采购政策
│   │   ├── fibo:Commitment        ← 承诺（合同中的义务）
│   │   │   └── aof:PaymentCommitment       ← 付款承诺
│   │   │   └── aof:DeliveryCommitment      ← 交货承诺
│   │   └── aof:MetricDefinition   ← 指标定义（信息模式）
│   │       └── aof:ProcurementSpendMetric  ← 采购支出指标
│   │       └── aof:BudgetExecutionRate     ← 预算执行率
│   ├── specifically dependent continuant
│   │   └── role
│   │       ├── fibo:ContractParty  ← 合同当事人角色
│   │       │   ├── aof:BuyerRole           ← 买方
│   │       │   ├── aof:SupplierRole        ← 供应商
│   │       │   └── aof:ApproverRole        ← 审批人
│   │       ├── fibo:Obligor       ← 义务承担者
│   │       │   └── aof:PaymentObligor      ← 付款义务人
│   │       └── fibo:Obligee       ← 权利享有者
│   │           └── aof:PaymentObligee      ← 收款权利人
│   └── independent continuant
│       └── fibo:LegalEntity       ← 法人实体
│           └── aof:Department     ← 部门
│           └── aof:Supplier       ← 供应商
└── occurrent
    └── process
        ├── fibo:ContractExecution  ← 合同执行过程
        │   └── aof:ProcurementProcess        ← 采购流程
        │       ├── aof:RequisitionPhase      ← 请购阶段
        │       ├── aof:ApprovalPhase         ← 审批阶段
        │       ├── aof:PurchasePhase         ← 采购阶段
        │       ├── aof:PaymentPhase          ← 付款阶段
        │       └── aof:ReceiptPhase          ← 收货阶段
        ├── aof:ExceptionHandling   ← 例外处理
        │   └── aof:EmergencyProcurement      ← 紧急采购
        │   └── aof:BreachOfContract          ← 违约处理
        └── aof:MetricCalculation   ← 指标计算
            └── aof:MonthlyProcurementSpendCalculation
            └── aof:BudgetVarianceCalculation
```

### 2.2 关键映射：从 YAML 到 FIBO 本体

| AOF YAML 概念 | FIBO 类 | BFO 挂点 | 语义澄清 |
|---|---|---|---|
| `monthly_procurement_spend` (Metric) | `fibo:Commitment` 的量化表达 | `generically dependent continuant` | 不是"数字"，而是"对付款义务的承诺的量化" |
| `framework_agreement` | `fibo:Contract` | `generically dependent continuant` | 有法律约束力的协议，区别于普通 Agreement |
| `emergency_purchase` | `aof:EmergencyProcurement` (process) | `occurrent` | 是"发生中的事"，不是"东西" |
| `procurement_manager` (role) | `fibo:ContractParty` + `aof:ApproverRole` | `role` | 是依附于人的角色，不是人本身 |
| `cfo` (decided_by) | `fibo:ContractParty` + `aof:DecisionMakerRole` | `role` | 决策角色，可追踪到具体法人 |
| `approval_log` | `aof:ApprovalRecord` (document) | `generically dependent continuant` | 审批记录是信息模式，依附于载体 |
| `breach_of_contract` | `fibo:BreachOfContract` | `process` | 违约是发生的事件，不是状态 |
| `prepayment` | `aof:Prepayment` (financial instrument) | `generically dependent continuant` | 预付款是金融工具，不是费用 |

### 2.3 Order-to-Cash 流程的 FIBO 建模

```
Order-to-Cash (O2C) 端到端流程
├── 采购侧 (Procure-to-Pay)
│   ├── Requisition 创建
│   │   └── 触发: aof:ProcurementNeed (disposition 的识别)
│   │   └── 产出: aof:PurchaseRequisition (generically dependent continuant)
│   ├── 审批流程
│   │   └── aof:ApprovalProcess (process)
│   │   └── 参与者: aof:ApproverRole (role, inheres_in 具体人)
│   │   └── 条件: aof:ApprovalThreshold (disposition: >50K 需总监)
│   ├── 合同/订单签订
│   │   └── 产出: aof:PurchaseOrder (subClassOf fibo:Contract)
│   │   └── 义务: aof:PaymentCommitment (subClassOf fibo:Commitment)
│   │   └── 权利: aof:DeliveryCommitment (subClassOf fibo:Commitment)
│   ├── 收货
│   │   └── aof:ReceiptProcess (process)
│   │   └── 产出: aof:GoodsReceipt (document)
│   └── 付款
│       └── aof:PaymentProcess (process)
│       └── 触发: aof:PaymentObligation (disposition 的实现)
│       └── 产出: aof:PaymentConfirmation (document)
│
└── 争议/例外处理
    ├── 口径争议 (如 PRC-2025-001)
    │   └── aof:MetricDispute (process)
    │   └── 涉及: aof:MetricDefinition (generically dependent continuant)
    │   └── 解决: aof:CaliberChangeDecision (process)
    ├── 紧急采购例外 (如 PRC-2025-002)
    │   └── aof:EmergencyProcurement (process)
    │   └── 触发: aof:EmergencyCondition (disposition)
    │   └── 约束: aof:EmergencySLA (generically dependent continuant)
    └── 违约处理 (如 PRC-2025-014)
        └── fibo:BreachOfContract (process)
        └── 触发: aof:ComplianceViolation (disposition)
```

## 3. 具体案例的 FIBO 重述

### 案例 PRC-2025-002: 紧急采购审批阈值

**原始描述**:
> 标准采购审批阈值：>50K 需总监审批，>200K 需VP审批。紧急采购有 23 笔超过 50K，其中 15 笔走了事后补批。

**FIBO 语义重述**:

```turtle
@prefix aof: <http://aof.example.org/ontology/> .
@prefix fibo-ctr: <https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/> .
@prefix fibo-agr: <https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Agreements/> .

# 标准采购合同
aof:StandardPurchaseContract a owl:Class ;
    rdfs:subClassOf fibo-ctr:Contract ;
    rdfs:label "标准采购合同"@zh .

# 紧急采购合同（特殊类型）
aof:EmergencyPurchaseContract a owl:Class ;
    rdfs:subClassOf fibo-ctr:Contract ;
    rdfs:label "紧急采购合同"@zh ;
    skos:definition "因紧急情况绕过标准审批流程的采购合同"@zh .

# 审批阈值作为合同的 disposition（倾向/条件）
aof:ApprovalThreshold a owl:Class ;
    rdfs:subClassOf [ owl:intersectionOf (
        [ rdf:type owl:Restriction ;
          owl:onProperty <http://purl.obolibrary.org/obo/BFO_0000196> ; # disposition
          owl:someValuesFrom fibo-ctr:Contract ]
    ) ] ;
    rdfs:label "审批阈值"@zh .

aof:threshold_50k a aof:ApprovalThreshold ;
    aof:thresholdAmount "50000"^^xsd:decimal ;
    aof:currency "CNY" ;
    aof:requiredRole aof:DirectorRole ;
    fibo-agr:isObligationOf aof:ProcurementDepartment .

aof:threshold_200k a aof:ApprovalThreshold ;
    aof:thresholdAmount "200000"^^xsd:decimal ;
    aof:currency "CNY" ;
    aof:requiredRole aof:VPRole ;
    fibo-agr:isObligationOf aof:ProcurementDepartment .

# 紧急采购 SLA（时间约束）
aof:EmergencySLA a owl:Class ;
    rdfs:subClassOf fibo-ctr:ContractualCommitment ;
    rdfs:label "紧急采购服务级别协议"@zh .

aof:emergency_sla_48h a aof:EmergencySLA ;
    aof:maxCompletionTime "PT48H"^^xsd:duration ;
    aof:appliesTo aof:EmergencyPurchaseContract ;
    fibo-agr:isObligationOf aof:ProcurementManager .

# 事后补批 = 违约后的补救过程
aof:RetroactiveApproval a owl:Class ;
    rdfs:subClassOf fibo-ctr:BreachOfContract ;  # 先违约，后补救
    rdfs:label "事后补批"@zh ;
    skos:definition "未在约定时间内完成审批，事后补充的审批动作"@zh .

# 具体案例实例
aof:case_PRC_2025_002 a aof:MetricDispute ;
    aof:disputeType aof:ApprovalThresholdViolation ;
    aof:involvesContract aof:emergency_purchase_contract_001 ;
    aof:baselineValue "0.05"^^xsd:decimal ;
    aof:actualValue "0.65"^^xsd:decimal ;
    aof:resolution [
        a aof:CaliberChangeDecision ;
        aof:decidedByRole aof:CFORole ;
        aof:newPolicy aof:emergency_sla_48h ;
        aof:rationale "保留紧急采购灵活性，但增加时间约束和追溯要求"
    ] .
```

### 案例 PRC-2025-004: 框架协议承诺 vs 执行

**FIBO 语义重述**:

```turtle
# 框架协议 = 一种特殊合同
aof:FrameworkAgreement a owl:Class ;
    rdfs:subClassOf fibo-ctr:Contract ;
    rdfs:label "框架协议"@zh ;
    skos:definition "约定一定期间内采购总量和条件的框架性合同"@zh .

# 承诺口径 = 合同签订时的 Commitment
aof:FrameworkCommitment a owl:Class ;
    rdfs:subClassOf fibo-agr:Commitment ;
    rdfs:label "框架协议承诺金额"@zh ;
    skos:definition "框架协议约定的年度/期间总金额"@zh .

# 执行口径 = 实际发生的 process 的量化
aof:FrameworkExecution a owl:Class ;
    rdfs:subClassOf aof:MetricCalculation ;
    rdfs:label "框架协议执行金额"@zh ;
    skos:definition "框架协议下实际下单并完成的金额"@zh .

# 关键区分：commitment 是 continuant（承诺持续存在），execution 是 occurrent 的量化
aof:framework_commitment_2025 a aof:FrameworkCommitment ;
    aof:commitmentAmount "5000000"^^xsd:decimal ;
    aof:currency "CNY" ;
    fibo-agr:isObligationOf aof:BuyerRole ;
    fibo-agr:hasObligation aof:SupplierRole .

aof:framework_execution_2025_H1 a aof:FrameworkExecution ;
    aof:executionAmount "3200000"^^xsd:decimal ;
    aof:currency "CNY" ;
    aof:calculatedFrom aof:PurchaseOrderProcess_2025_H1 .
```

## 4. 验证方案

### 4.1 实验设计

| 实验组 | 输入 | 本体 | 预期输出 |
|---|---|---|---|
| 对照组 | `historical_cases.yaml` | 无（AOF 原生 YAML） | 平铺实体：`metric`、`action_type`、`workflow` |
| 实验组 A | `historical_cases.yaml` | `references/fibo/FND/Agreements/Agreements.rdf` | 实体挂到 FIBO Agreement/Commitment |
| 实验组 B | `historical_cases.yaml` | `references/fibo/FND/Agreements/Contracts.rdf` | 实体挂到 FIBO Contract/ContractParty |
| 实验组 C | `historical_cases.yaml` | FIBO + BFO 联合 | 完整 continuant/occurrent 骨架 + 金融中层 |

### 4.2 评估指标

1. **抽取准确率**: LLM 是否正确识别"框架协议"是 `Contract` 而非 `Agreement`
2. **关系完整性**: 是否正确识别 `Obligor`/`Obligee` 关系
3. **跨案例一致性**: 15 个案例中的"审批人"是否统一识别为 `role`（而非 `person`）
4. **推理可及性**: 能否回答"谁是 PRC-2025-002 的义务承担者？"（需要 `isObligationOf` 推理）

### 4.3 执行步骤

```bash
# 1. 准备 FIBO 增强的 spec
# 2. 运行 aof_add.py + aof_doctor.py
# 3. 对比 cognify 输出
# 4. 记录到本目录的 RESULTS.md
```

## 5. 预期发现

| 假设 | 验证方式 |
|---|---|
| FIBO 能减少"contract vs agreement"的混淆 | 检查 15 个案例中"框架协议"的归类一致性 |
| FIBO 能区分"role"和"person" | 检查"cfo""procurement_manager"是否被识别为 role |
| FIBO 能区分"commitment"和"execution" | 检查 PRC-2025-004 的双口径是否正确建模 |
| BFO+FIBO 联合比单独 FIBO 更好 | 对比实验组 B 和实验组 C 的 occurrent 识别率 |

## 6. 下一步

- [ ] 创建 `procurement_fibo_pilot/procurement_o2c_fibo.owl` —— 用 FIBO 重新建模的采购本体
- [ ] 创建 `procurement_fibo_pilot/cases_fibo_rdf/` —— 15 个案例的 FIBO 语义化实例
- [ ] 运行 cognify 对照实验
- [ ] 撰写 `RESULTS.md`
