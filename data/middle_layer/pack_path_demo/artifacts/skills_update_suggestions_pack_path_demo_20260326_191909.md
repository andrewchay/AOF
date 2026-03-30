# Skills 更新建议：pack_path_demo

## 1. 术语归一技能（Term Normalization）

- 暂无 map_term 反馈，保持现有术语规则。

## 2. 口径约束技能（Metric Guardrails）

- 约束词：生成 SQL 时优先包含 `count(distinct`
- 约束词：生成 SQL 时优先包含 `date_sub`
- 约束词：生成 SQL 时优先包含 `join`
- 约束词：生成 SQL 时优先包含 `logdate`
- 约束词：生成 SQL 时优先包含 `regexp_like`
- 约束词：生成 SQL 时优先包含 `where`

## 3. 反馈闭环执行策略

- 新 feedback 进入后，先更新 OWL，再更新术语与约束技能。
- 每次发布前必须通过回归样例库。
- 对 high severity case 失败设置阻断。
