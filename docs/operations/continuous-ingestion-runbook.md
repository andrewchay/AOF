# 企业知识持续接入 Runbook

## 1. 生产配置

至少设置：

```bash
AOF_RUNTIME_MODE=production
AOF_SEMANTIC_IDENTITY_KEY_ID=identity-key-v1
AOF_SEMANTIC_IDENTITY_SECRET=<from-secret-manager>
AOF_CONTINUOUS_INGESTION_DATABASE=/var/lib/aof/ingestion/state.sqlite3
AOF_INGESTION_FILE_ROOTS=/srv/knowledge:/srv/reference-data
AOF_COMPILER_STATE_DIR=/var/lib/aof/compiler
AOF_DECISION_PROVENANCE_FILE=/var/lib/aof/audit/decisions.jsonl
```

同时按可信运行底座要求配置 Release 与 Query evidence signer。API/事件连接器的凭据只放在 Secret Manager、
sidecar 或 provider 实现中；不得放进 Source registration body。

## 2. 上线检查

1. 使用 owner/editor 签名身份注册 Source revision。
2. 使用 ingestor 身份执行一次接入，并记录 Run、cursor、snapshot digest、ChangeSet digest 和 decision ID。
3. 再执行一次未变化轮询，确认返回同一个 Run ID，仓库没有新增 Run。
4. 使用 editor+validator 的流水线身份 stage；确认状态为 review、conflict_review 或 schema_drift_review。
5. 用互不相同的 reviewer、compiler/replay compiler、publisher 身份完成测试 channel 晋级。
6. 确认 channel 指向 replay Run，且决策 audit trail 可追到 IngestionRun。

## 3. 故障处置

`failed` Run 的 cursor 没有推进。先查看 error type/message 和 decision audit，再按类别处理：

- 路径越界：调整 `AOF_INGESTION_FILE_ROOTS` 或移动文件，不放宽到系统根目录。
- API timeout/结果未知：先向源系统按 request/cursor 查证，不直接自动重试。
- identity 缺失/重复：修复数据或 mapping identity，保留原失败 Run。
- cursor collision：停止该连接器；相同 cursor 返回了不同内容，必须修复 provider 契约。
- schema drift：执行影响分析，更新 mapping/约束/回归测试后，以新 Release ID 重新 stage。
- conflict review：由治理工作台处理 finding；不得直接调用 publish 或修改 channel 文件。

修复完成后使用新 attempt ID 运行。旧 attempt 用于取证，不删除、不覆盖。

## 4. 备份与恢复

持续接入库使用 WAL。在线备份应调用 SQLite backup API，或在阻断写流量并 checkpoint 后复制数据库及 WAL；
不要只复制主文件。恢复步骤：

1. 停止 ingestor 调度，不停止只读审计接口。
2. 保存受损数据库、WAL、决策账本和 connector 日志作取证。
3. 恢复到新的路径并运行 `SqliteContinuousIngestionRepository.verify_all()`。
4. 对每个 Source 核对 cursor 与最后一个成功 Run；失败 Run 不应改变 cursor。
5. 抽样重新 fetch：相同快照必须复用旧 Run；不得直接编辑 cursor。
6. 对关联的 Release、CompilationRun 和 channel 运行各自完整性检查，再恢复调度。

决策 JSONL 与 ingestion SQLite 必须位于同一个恢复点。若 Run 已提交而决策写入失败，重新调用相同轮询会复用
Run 并补写确定性的 ingestion decision；完成对账前不得 stage。

## 5. 自动化验收

```bash
.venv/bin/python -m pytest -q \
  tests/test_continuous_ingestion.py \
  tests/test_enterprise_source_connectors.py \
  tests/test_continuous_ingestion_api.py \
  tests/test_continuous_compilation.py \
  tests/test_enterprise_continuous_ingestion_e2e.py
npm --prefix web run build
```

验收必须同时覆盖文件、数据库、API、事件 fixture，增量 add/update/delete、无变化幂等、schema drift、连接器
失败恢复、独立 replay 与 channel 晋级。绿色单测不能替代真实 provider 的认证、限流、CDC offset 和恢复演练。

