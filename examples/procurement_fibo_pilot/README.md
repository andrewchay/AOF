# 采购 + Order-to-Cash FIBO 映射试点

> **目标**: 用 FIBO 重新建模 AOF 采购口径治理示例，验证 FIBO 对财务域本体的语义增强价值
> **基准**: `examples/procurement_caliber_governance/` (15 个历史争议案例 + 语义资源定义)
> **参考**: `references/fibo/FND/Agreements/`, `references/bfo-2020/MAPPING_GUIDE.md`

## 文件结构

```
procurement_fibo_pilot/
├── PILOT_PLAN.md              # 试点方案设计
├── procurement_o2c_fibo.owl   # FIBO 增强的采购本体（核心骨架）
├── README.md                  # 本文件
└── cases_fibo_rdf/            # 15 个历史案例的 FIBO 语义化实例
    ├── PRC-2025-001.rdf       # 差旅费是否计入采购支出
    ├── PRC-2025-002.rdf       # 紧急采购审批阈值
    ├── PRC-2025-003.rdf       # 供应商预付款是否计入当期
    ├── PRC-2025-004.rdf       # 框架协议承诺 vs 执行
    ├── PRC-2025-005.rdf       # 跨部门共享服务归属
    ├── PRC-2025-006.rdf       # 采购退货确认时点
    ├── PRC-2025-007.rdf       # 个人垫付报销归属
    ├── PRC-2025-008.rdf       # 外币采购汇率折算
    ├── PRC-2025-009.rdf       # 样品采购分类
    ├── PRC-2025-010.rdf       # 框架协议预算分摊
    ├── PRC-2025-011.rdf       # 采购折扣确认时点
    ├── PRC-2025-012.rdf       # 内部转移定价
    ├── PRC-2025-013.rdf       # 采购押金会计处理
    ├── PRC-2025-014.rdf       # 紧急采购合规性
    └── PRC-2025-015.rdf       # 预算口径 vs 核算口径
```

## 核心发现

### 现有 YAML 模型的问题
- `Metric`、`ActionType`、`Workflow` 是平铺命名，无语义关系
- `monthly_procurement_spend` 是"指标"还是"承诺"还是"义务"？语义模糊
- "紧急采购"是名词（东西）还是动词（过程）？LLM 抽取容易混淆

### FIBO 增强后的价值
- **合同语义**: `PurchaseOrder`、`FrameworkAgreement` 明确挂到 `fibo:Contract`
- **角色区分**: `ApproverRole`、`BuyerRole` 明确是 `role`（依附于人），不是人本身
- **承诺 vs 执行**: `Commitment`（continuant，持续存在）vs `Execution`（occurrent 的度量）
- **义务追踪**: `Obligor`/`Obligee` 关系可回答"谁是义务承担者？"

## 15 个案例的 FIBO 映射汇总

| 案例 | 主题 | FIBO 关键映射 | BFO 核心区分 |
|---|---|---|---|
| PRC-2025-001 | 差旅费是否计入采购支出 | `MetricDefinition` scope 争议 | 信息模式的范围界定 |
| PRC-2025-002 | 紧急采购审批阈值 | `EmergencyPurchaseContract` → `fibo:Contract`; `ApprovalThreshold` → `disposition` | 条件性倾向 vs 过程 |
| PRC-2025-003 | 供应商预付款是否计入当期 | `Prepayment` → `generically dependent continuant` (资产) | 资产 vs 费用 |
| PRC-2025-004 | 框架协议承诺 vs 执行 | `FrameworkAgreement` → `fibo:Contract`; 承诺 = `Commitment` (continuant); 执行 = 对 `PurchasePhase` 的度量 | continuant vs occurrent |
| PRC-2025-005 | 跨部门共享服务归属 | `SharedServiceAgreement` → `fibo:Contract`; 多 `ContractParty` | 单一 vs 多方合同 |
| PRC-2025-006 | 采购退货确认时点 | `ReturnProcess` → `process`; `ReturnInitiation` vs `RefundReceipt` → `process boundary` | 过程边界的确认时点 |
| PRC-2025-007 | 个人垫付报销归属 | `PettyCashProcurement` → `PurchaseOrder`; business substance over form | 实质重于形式 |
| PRC-2025-008 | 外币采购汇率折算 | `ForeignCurrencyContract` → `PurchaseOrder`; `FXVariance` → 独立 `MetricDefinition` | 风险隔离 |
| PRC-2025-009 | 样品采购分类 | `SampleProcurement` vs `RDProcurement` → `PurchaseOrder` 子类 | 目的维度分类 |
| PRC-2025-010 | 框架协议预算分摊 | `CommitmentSchedule` → 时间分布计划 | 承诺节奏 vs 消耗节奏 |
| PRC-2025-011 | 采购折扣确认时点 | `RebateAccrual` → price adjustment; matching principle | 受益期间匹配 |
| PRC-2025-012 | 内部转移定价 | `InternalTransferAgreement` → `fibo:Contract`; 关联交易隔离 | 外部 vs 内部交易 |
| PRC-2025-013 | 采购押金会计处理 | `Deposit` → `generically dependent continuant` (资产) | 资产 vs 费用 |
| PRC-2025-014 | 紧急采购合规性 | `SingleSourceProcurement` → `EmergencyPurchaseContract`; `ComplianceViolation` → `process` | 合规 vs 灵活性 |
| PRC-2025-015 | 预算口径 vs 核算口径 | `DualMetric`: `commitment_metric` (continuant) + `execution_metric` (occurrent) | continuant vs occurrent 的显式区分 |

## 新增本体类（超出核心骨架）

在 `procurement_o2c_fibo.owl` 基础上，案例语义化过程中新增了以下类：

| 类名 | 父类 | 案例 | 语义 |
|---|---|---|---|
| `Prepayment` | `generically dependent continuant` | PRC-2025-003 | 预付款是资产，不是费用 |
| `SharedServiceAgreement` | `fibo:Contract` | PRC-2025-005 | 覆盖多方的服务协议 |
| `CostAllocationMetric` | `MetricDefinition` | PRC-2025-005 | 成本分摊指标 |
| `ReturnProcess` | `ProcurementProcess` | PRC-2025-006 | 退货流程 |
| `PettyCashProcurement` | `PurchaseOrder` | PRC-2025-007 | 小额垫付采购 |
| `ForeignCurrencyContract` | `PurchaseOrder` | PRC-2025-008 | 外币采购合同 |
| `FXVariance` | `MetricDefinition` | PRC-2025-008 | 汇率差异 |
| `SampleProcurement` | `PurchaseOrder` | PRC-2025-009 | 样品采购 |
| `RDProcurement` | `PurchaseOrder` | PRC-2025-009 | 研发采购 |
| `CommitmentSchedule` | `MetricDefinition` | PRC-2025-010 | 承诺时间分布 |
| `RebateAccrual` | `MetricDefinition` | PRC-2025-011 | 返利应计 |
| `InternalTransferAgreement` | `fibo:Contract` | PRC-2025-012 | 内部转移协议 |
| `Deposit` | `generically dependent continuant` | PRC-2025-013 | 押金（资产） |
| `ComplianceViolation` | `process` | PRC-2025-014 | 合规违规 |
| `SingleSourceProcurement` | `EmergencyPurchaseContract` | PRC-2025-014 | 单一来源采购 |
| `DualMetric` | `MetricDefinition` | PRC-2025-015 | 双口径指标 |

## 关键 BFO 区分在案例中的应用

### continuant vs occurrent（最核心）
- **PRC-2025-004**: 承诺 500 万 = `Commitment` (continuant，持续存在); 执行 320 万 = 对 `PurchasePhase` 的度量 (occurrent)
- **PRC-2025-015**: 预算口径 = `commitment_metric` (continuant); 核算口径 = `execution_metric` (occurrent)

### disposition vs role
- **PRC-2025-002**: `ApprovalThreshold` 是 `disposition`（条件性倾向: >50K 需总监）; `ApproverRole` 是 `role`（由语境赋予的身份）

### generically dependent continuant vs specifically dependent continuant
- **PRC-2025-003/PRC-2025-013**: `Prepayment`/`Deposit` 是 `generically dependent continuant`（可转让的权利模式）
- **PRC-2025-002**: `ApproverRole` 是 `specifically dependent continuant`（依附于特定人）

## 下一步

- [x] 完成 15 个案例的 FIBO 语义化
- [x] 运行 cognify 对照实验（无本体 vs FIBO 增强）
- [x] 评估抽取准确率、关系完整性、跨案例一致性
- [x] 撰写 `RESULTS.md`
