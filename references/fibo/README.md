# FIBO 参考目录

本目录存放 EDM Council / OMG FIBO 的按需抽取子集，用于 AOF 财务域本体的**设计期对齐**。

## 文件

| 文件 | 大小 | 来源 | 核心内容 |
|---|---|---|---|
| `FND/Agreements/Agreements.rdf` | 15KB | FIBO master | Agreement、Commitment、Obligor/Obligee |
| `FND/Agreements/Contracts.rdf` | 72KB | FIBO master | Contract、ContractParty、BreachOfContract、ConditionPrecedent 等 |
| `INTEGRATION_GUIDE.md` | — | AOF 起草 | FIBO 用于 AOF 财务域的集成指南（v0.1 草稿） |

## 用途

- 不克隆完整 FIBO 仓库（~5000+ commits，多域工业细节）
- 按需抽取 FND（Foundations）核心模块作为财务域本体的语义基准
- 与 `references/bfo-2020/` 的 BFO 映射指南衔接：FIBO 基于 BFO 构建，填补金融域中层空白
- 详细映射规则与 SHACL 质控约束见 `INTEGRATION_GUIDE.md`

## 来源

- 上游仓库: https://github.com/edmcouncil/fibo
- 下载日期: 2026-09-02
- 许可证: MIT
