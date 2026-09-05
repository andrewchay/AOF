# 企业确定性运行层 Runbook

## 1. 生产配置

```bash
AOF_RUNTIME_MODE=production
AOF_SEMANTIC_IDENTITY_KEY_ID=identity-key-v1
AOF_SEMANTIC_IDENTITY_SECRET=<secret-manager-reference>
AOF_ENTERPRISE_RUNTIME_STATE_DIR=/var/lib/aof/enterprise-runtime
AOF_REASONING_RUNTIME_DATABASE=/var/lib/aof/enterprise-runtime/reasoning.sqlite3
AOF_WORKFLOW_RUN_DATABASE=/var/lib/aof/enterprise-runtime/workflows.sqlite3
AOF_SIMULATION_RUN_DATABASE=/var/lib/aof/enterprise-runtime/simulations.sqlite3
AOF_BITEMPORAL_OBJECT_DATABASE=/var/lib/aof/enterprise-runtime/objects.sqlite3
AOF_ACTION_RUN_DATABASE=/var/lib/aof/actions/action-runs.sqlite3
AOF_DECISION_PROVENANCE_FILE=/var/lib/aof/audit/decisions.jsonl
```

Action Connector 在进程启动时注册，凭据来自 Secret Manager/KMS 或 sidecar，不进入 Release、Plan、Run、Workflow
或日志。未注册 Connector 时推理和模拟仍可用，Action/Workflow execute 必须失败关闭。

## 2. 上线门

1. 确认生产 channel 指向 independently reproduced CompilationRun。
2. 对 reasoning、workflow、simulation、action、bitemporal、event repository 执行 `verify_all()`。
3. 使用不同服务身份完成 reasoner、analyst、operator、reviewer、worker 的 canary。
4. 注入重复事件、晚到旧事件和撤回，确认物化结论与 event time 一致。
5. 注入 Connector timeout，确认 ActionRun 和 WorkflowRun 都进入 reconciliation 且调用计数保持 1。
6. 对 reversible 两节点 Workflow 注入第二节点明确失败，确认第一节点仅补偿一次。
7. 验证最终 Decision audit trail 能追到 event/change、ruleset、Simulation、WorkflowPlan、ActionPlan 和 receipt。

## 3. 状态处置

- `blocked` simulation：检查新增 blocking outcomes 和双时态 snapshot，不启动 Workflow。
- `awaiting_approval`：由 ActionType 要求的独立 reviewer 审批；请求人不得自批。
- `failed`：仅表示 Connector 明确确认未产生效果，按 Workflow 策略执行补偿。
- `compensated`：保留原 effect receipt 与 compensation receipt，不把它改写为 succeeded。
- `reconciliation_required`：冻结自动推进，向下游按 execution/idempotency key 查证；由人工或对账服务决定
  “已生效、未生效、已补偿”，不得调用 execute 重试。

同一 ruleset ID 的 program version 冲突、同一 change ID 的内容冲突、同一 workflow/simulation idempotency identity
的计划冲突都按治理冲突处理，不能通过删除数据库记录解决。

## 4. 备份恢复

所有新仓库提供 `backup_to()`，使用 SQLite online backup API。Reasoning、Workflow、Simulation、Action、Object、
Event 数据库与 Decision JSONL 必须属于同一恢复点。

恢复顺序：

1. 停止 event consumer、reasoner worker 和 workflow worker，保留只读审计入口。
2. 保存受损文件、WAL 和日志作取证，不覆盖原目录。
3. 恢复到新 root，对所有 repository 运行 `verify_all()`。
4. 对 ReasoningRun 和 SimulationRun 抽样 strict replay。
5. 核对遗留 `executing` ActionRun；首次恢复推进只能转入 reconciliation，不能再次调用 Connector。
6. 核对 Workflow node action digest、补偿 receipt 和 channel/Release binding。
7. 通过 canary 后切换 state root，再恢复事件消费。

## 5. 验收命令

```bash
.venv/bin/python -m pytest -q \
  tests/test_incremental_reasoning_runtime.py \
  tests/test_bitemporal_simulation.py \
  tests/test_workflow_runs.py \
  tests/test_enterprise_runtime_control.py \
  tests/test_enterprise_runtime_e2e.py
```

Fixture 验收证明核心状态机和审计语义，不证明企业 Connector、消息系统、KMS 或跨区部署已经完成；真实上线仍需
provider canary、容量基准、灾备演练和 SLO 观察。

Web 工作台尚未随此运行时交付，当前规范入口为签名 REST API；不要把其他分支的 web build 计入本分支验收。
