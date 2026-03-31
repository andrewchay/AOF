# AOF 90天落地执行手册 v1

最后更新：2026-04-01

## 0. 目标

在 90 天内，把 AOF 从“可用平台”落到“可持续产出业务价值”的生产化能力。

北极星指标：**Agent 业务任务一次正确率（First-pass correctness）**

## 1. 落地范围（先打一个主战场）

首个必胜场景：**互联网/游戏 Text-to-SQL + 指标口径治理**

为什么先做这个：
1. 业务痛点最明确（口径冲突、SQL误用）
2. 数据资产现成（历史 SQL、口径文档、报表）
3. 价值可量化（准确率、工时、争议工单）

## 2. 固定交付件（每个主题域必须有）

1. `ontology.owl`
2. `mapping bundle`（term/metric/dimension/sql_pattern）
3. `regression cases`
4. `manifest + 版本说明`

目录标准：
- `data/middle_layer/<topic>/mapping/`
- `data/middle_layer/<topic>/regression/`
- `data/middle_layer/<topic>/artifacts/`

## 3. 角色与职责

1. 业务 Owner  
- 定义术语、口径优先级、上线验收标准

2. 数据 Owner  
- 提供 metadata、表关系、口径 SQL 基线

3. AOF 工程 Owner  
- 执行构建、回归门禁、发布与回滚

4. Agent 应用 Owner  
- 接入调用协议（检索-约束-生成-校验）

## 4. 90天里程碑

### Day 1-14（打底）

1. 选定 1 个主题域（如投放 ROI）
2. 导入 50-200 份文档+SQL
3. 产出第一版 ontology/mapping/regression/manifest
4. 接入 1 个 Text-to-SQL Agent

验收：
- 回归样例 >= 30
- 一次正确率基线建立

### Day 15-45（跑通）

1. 每周反馈回流 1 次（更新规则）
2. 建立版本发布节奏（每周一版）
3. 引入 CI 门禁（回归不通过不可发布）

验收：
- 回归通过率 >= 90%
- 口径争议工单下降 >= 20%

### Day 46-90（复制）

1. 扩展到第 2/3 个主题域
2. 输出行业 starter pack v1
3. 固化运营机制（月度复盘）

验收：
- 新主题接入时间 <= 2 周
- 一次正确率较基线提升 >= 15%

## 5. 每周执行节奏

1. 周一：确定本周语义变更清单
2. 周三：完成构建与回归
3. 周四：灰度验证（Agent 实战）
4. 周五：发布、复盘、沉淀样例

## 6. 首月任务清单（可直接执行）

1. 建立试点目录和输入样本
2. 跑通一键构建命令：

```bash
LLM_API_KEY="<YOUR_KEY>" \
/Users/chaihao/LLM/AOF/tools/middle_layer/run_semantic_middle_layer.sh \
  pilot_topic \
  /ABS/PATH/docs \
  /ABS/PATH/metadata.json \
  /ABS/PATH/feedback.jsonl \
  3
```

3. 产出并评审 4 类交付件
4. 把 regression 接到发布门禁
5. 建立 KPI 看板（正确率/回归通过率/工时节省）

## 7. 风险与兜底

1. 文档质量差  
- 先做高价值文档白名单

2. 业务规则冲突  
- 由业务 Owner 定义单一真源

3. Agent 输出不稳定  
- 强制走 SQL 约束+回归校验链路

## 8. 退出条件（90天后）

满足以下 4 项即可进入规模化阶段：
1. 已稳定运行 >= 2 个主题域
2. 有持续版本节奏与回滚能力
3. KPI 连续 2 个迭代周期改善
4. 形成可复用的行业模板包
