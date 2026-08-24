# AOF 生产化可信运行底座 Runbook

本文覆盖生产准入、密钥轮换、证据备份恢复、完整性检查和 SLO 告警处置。适用对象是语义 Release、
CompilationRun、QueryRun 及其编译产物；业务数据库备份由数据平台另行负责。

## 1. 上线准入

生产实例必须设置 `AOF_RUNTIME_MODE=production`，并显式配置三个相互独立、至少 32 bytes 的信任域：

- `AOF_SEMANTIC_IDENTITY_SECRET` / `AOF_SEMANTIC_IDENTITY_KEY_ID`
- `AOF_RELEASE_SIGNING_SECRET` / `AOF_RELEASE_SIGNING_KEY_ID`
- `AOF_QUERY_EVIDENCE_SIGNING_SECRET` / `AOF_QUERY_EVIDENCE_SIGNING_KEY_ID`

生产应以 `ExternalSigningProvider` 对接 KMS/HSM；本地 secret 仅是参考部署。还必须设置：

- `AOF_OTEL_EXPORTER_OTLP_ENDPOINT`
- `AOF_SLO_TARGETS_FILE=config/observability/slo_targets.yaml`
- `AOF_QUERY_TIMEOUT_MS`、`AOF_QUERY_MAX_ROWS`
- `AOF_QUERY_MAX_VM_STEPS`、`AOF_QUERY_PROGRESS_INTERVAL`

上线门执行顺序：

1. 调用 `GET /v1/ops/readiness`，要求 HTTP 200、`ready=true` 且无 finding。
2. 对 Release、CompilationRun、QueryRun 仓库分别运行 `verify_all()`，要求 `valid=true`。
3. 调用 `GET /v1/ops/trusted-runtime`，要求 `status=ok`。
4. 检查 `/metrics` 可被 Prometheus 抓取，OTLP collector 能收到带 `aof.release_id`、
   `aof.compilation_run_id`、`aof.query_run_id` 和 `aof.plan_digest` 的 span。
5. 只有独立 replay 成功并经 reviewer 审批的 CompilationRun 才能提升到 production channel。

readiness 失败时业务端点返回 503。不得临时切换 development 绕过生产门。

## 2. 备份与恢复

使用 Repository API 创建一致性在线备份，不要直接复制 WAL 模式下正在写入的 SQLite 文件：

```python
from pathlib import Path
from bridge.semantic_core import SqliteQueryRunRepository
from bridge.semantic_core.compilers import SqliteCompilationRunRepository

state = Path("data/semantic_compiler")
backup = Path("backups/semantic-2026-08-24")
SqliteCompilationRunRepository(state / "acme").backup_to(backup / "acme")
SqliteQueryRunRepository(state / "query-runs.sqlite3").backup_to(
    backup / "query-runs.sqlite3"
)
```

每个租户的 CompilationRun root 要单独备份；Release Repository 同样使用自身 `backup_to()`。备份完成后，
从备份路径创建新 Repository 并运行 `verify_all()`。只有校验成功的备份才能计入恢复点。

恢复步骤：

1. 阻断写流量，保留 liveness、readiness、metrics 和 SLO 诊断入口。
2. 保存受损状态作取证，不在原目录覆盖修复。
3. 将最近一次已验证备份恢复到新的 state root。
4. 对 Release、CompilationRun、QueryRun 全量运行 `verify_all()`。
5. 使用历史 QueryRun 执行 `get_run()` 验签；抽样执行 strict replay。
6. 将 `AOF_COMPILER_STATE_DIR` 切换到新 root，重启实例并再次通过上线准入门。
7. 恢复流量，记录恢复所用 backup digest、时间点和审批决策。

`PRAGMA integrity_check=ok` 只证明 SQLite 页结构可读，不等于证据完整；必须同时验证记录摘要、channel
pointer history、实际 artifact bytes 和签名。

## 3. 密钥轮换

KMS/HSM 轮换必须先增加新 key，再切换 current key，历史 verification key 不得提前删除：

1. 在 provider 中注册新 key ID，保持旧 key 可验证。
2. 对测试 Release 和 QueryRun 使用新 key 签名并立即验签。
3. 将 provider current key 切换到新版本。
4. 生成一条新 Release attestation 和 QueryRun，确认 attestation 的 `key_id` 已更新。
5. 抽样验证旧 Release 与旧 QueryRun 仍可通过历史 key 验签。
6. 观察至少一个审计保留周期后，按组织密码策略退役旧签名权限；验证权限应覆盖证据保留期。

外部 signer 不可用时发布和 QueryRun 持久化必须失败，不得回退到默认 key 或本地临时 secret。

## 4. 告警处置

`AOFTrustedRuntimeSLOBreach` 触发后：

1. 查看 `/v1/ops/trusted-runtime` 的 `alerts`、operation 维度成功/失败数和 p95。
2. 使用 `last_correlation` 或当前 OTLP trace 定位 Release、CompilationRun、QueryRun 和 plan。
3. 错误率异常时先区分 policy 正常拒绝、signer/storage 故障和 Executor 资源预算触发。
4. 延迟异常时检查数据源锁、VM step budget、行数和 timeout；不要简单放宽所有租户预算。
5. 证据完整性失败立即阻断发布和查询流量，按备份恢复流程处理。

Prometheus 指标只用 operation/status 作为标签。Release ID、Run ID 和 digest 只放在 trace/诊断信息中，
不得新增为高基数 metric label。

## 5. 定期演练与完成标准

至少每季度执行：并发幂等写、进程重启、artifact 篡改、QueryRun 索引篡改、KMS key 轮换、在线备份和异地
恢复。演练完成必须满足：

- 所有篡改在提供结果前被发现；
- 恢复仓库 `verify_all()` 全部通过；
- 恢复后的新旧签名证据均可验证；
- 相同 Release/channel/query 的 trusted plan 可重建；
- 告警、trace、决策记录和恢复审批能形成单一审计轨迹。

自动化回归入口为：

```bash
.venv/bin/python -m pytest tests/test_trusted_runtime_production_e2e.py -q
```
