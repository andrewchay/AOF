# Agentic System P3–P6 运行手册

## 必需配置

```bash
export AOF_SEMANTIC_IDENTITY_SECRET='<secret-from-kms>'
export AOF_SEMANTIC_IDENTITY_KEY_ID='identity-YYYY-MM'
export AOF_AGENTIC_RUN_DATABASE='/durable/aof/agentic-runs.sqlite3'
export AOF_COMPILER_STATE_DIR='/durable/aof/semantic-compiler'
```

SQL 能力还需配置受限数据库和 QueryPolicy。production 模式必须同时满足 `ProductionReadiness` 对 release、query-evidence、OTel 和 SLO 的要求。

SQL 配置项为 `AOF_QUERY_SQLITE_DATABASE` 和 JSON 对象 `AOF_QUERY_SQLITE_ATTACHMENTS`（例如 schema 名 `dwd` 到只读数据库路径）。没有 SQL 后端时，Agentic 拒绝将编译结果当成答案。设置 `AOF_AGENTIC_ENABLED=true` 会让全局生产 readiness 要求显式 Agentic 数据库；未设置时其他运行时服务不因未启用 Agentic 被阻断，Agentic 自身仍检查持久数据库路径。

本地验收无需 Docker 或真实企业数据：运行 `python -m pytest -q tests/test_agentic_default_runtime.py`。它使用临时 RDF release 和实际 SQLite 数据库，通过默认 REST 控制面完成查询/回放。

## 发布前检查

财务业务闭环运行 `python -m pytest -q tests/test_finance_review_e2e.py`，包含成功和不确定回执两条路径。业务请求增加 `query_contract_id`，其已发布 QueryContract 定义 `accepted_queries`、`purpose` 和有序 `agentic_steps`；每个步骤包含 capability、depends_on 与受控参数。示例合同在该测试的 `finance_resources()`，明确使用合成数据。

动作返回 `awaiting_approval` 后，由不同主体调用 `/v1/semantic/action-runs/{id}/approve`，由 action-executor 调用对应 `/execute`，再调用 `/v1/agentic/runs/{id}/refresh-actions` 更新总运行状态。未知回执保留 `reconciliation_required`，不会自动重试。带动作的 Agentic run 禁止直接 replay，防止重放变成另一次业务写入。

1. production channel 指向已独立重放、已审批的 CompilationRun。
2. Agentic request 的 `release_id` 和 `release_digest` 与 channel 一致。
3. 只开放本租户获批的 `allowed_capabilities`；`allow_llm_fallback` 默认 `false`。
4. 运行 P3–P6 聚焦测试和全仓测试。
5. 检查 `/v1/ops/trusted-runtime` 无 SLO breach。

## 故障处理

- `ontology intent is unresolved`：补充已发布语义/路由契约，或按策略允许 RAG；不要默认开启 LLM。
- `returned no evidence`：修复 capability executor 的证据回执，不能让 summary 绕过。
- `different trusted release snapshot`：停止运行并核对 channel promotion；不要用请求参数覆盖当前 release。
- `digest mismatch`：隔离数据库副本，从已验证备份恢复并重放；不要继续读取受损状态。
- `reproducible=false`：比较工具证据和后端快照，禁止 promotion 或自动动作。
- `running` 在进程退出后仍存在：保留原 run，检查逐步结果和底层 query receipts；不以同一 ID 自动重试。确认后用新 ID 重新发起只读任务。

`SqliteAgenticRunRepository.backup_to(new_path)` 使用 SQLite 在线备份，不覆盖已有文件。恢复时在隔离路径创建 repository，先验证 run digest、memory 和回放结果再切换配置。这个本地能力不等于远程灾备演练通过。

## 数据与隐私

会话记忆只保存 query digest、用户 query、release 标识和带 citation 的 summary。接入生产前仍需定义字段级脱敏、保留期、删除/法务冻结和备份加密策略。
