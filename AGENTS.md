# AOF 项目地图

## 命令

```bash
# 测试（全量）
/Users/chaihao/LLM/AOF/.venv/bin/python -m pytest tests/ -q -o addopts=''
# Lint
/Users/chaihao/LLM/AOF/.venv/bin/python -m ruff check bridge services tools tests
# 零跳过测试（CI enterprise job 使用）
python tools/ci/run_pytest_no_skips.py <test files...>
```

## 基础设施栈（验证/集成环境）

```bash
docker compose -f deploy/docker-compose.infra.yml up -d
sh deploy/keycloak-init.sh   # 幂等，需在容器启动后执行
sh deploy/openbao-init.sh    # 幂等
```

- PG: `postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control`
- OpenBao: http://127.0.0.1:8200 (token: aof-dev-root-token)
- Keycloak: http://127.0.0.1:8081 (admin/kc-dev-only, realm aof)
- RabbitMQ: amqp://aof:aof-dev-only@127.0.0.1:5673/%2F
- 注意：keycloak/openbao 服务无 healthcheck，`compose up --wait` 不代表可服务；init 脚本自带就绪等待。

## 架构要点

- **Repository SPI**: 同一协议 + SQLite/PostgreSQL 双后端 + 参数化合同测试（tests/*_pg_contract.py）
- **PG repo 模式**: `_xxx_on(connection)` + 公共方法包 `managed_sqlite_connection` 事务
- **UnitOfWork**: `SqliteUnitOfWork.atomic()` 单连接单事务（decision+state 同提交同回滚）
- **写透门面**: `SqliteIamStore` Mapping/Sequence 视图 + `dataclasses.replace` 保持枚举类型
- **操作注册表**: config/capabilities/operations.json 驱动 auth gate + policy matrix
- **能力清单**: tools/ci/generate_capability_inventory.py 生成 manifest v3（含 integration_profiles）；修改其 generated_from 源文件后需重新生成
- **默认拒绝**: 未注册路由不能通过校验；strict 模式下未认证请求 401
- **集成能力档案**: bridge/capability_status.py + config/capabilities/integration-profiles.json，ProductionReadiness 通过 AOF_CAPABILITY_PROFILE 评估

## 代码位置

- bridge/access/: session_acl, oidc, policy, rate_limit, revocations, operation_registry, data_governance, deletion, iam_migration, openbao_signer
- bridge/persistence/: sqlite_support, decision_ledger_repository, sqlite_iam_store, sqlite_session_store, postgres_*_repository/store, unit_of_work, checkpoint
- bridge/semantic_core/action_runs.py: ActionRunService + W02.05 receipt recovery
- services/semantic_middle_layer_api/: API 服务（Dockerfile + 多环境 compose）
- deploy/: docker-compose.infra.yml, openbao-init.sh, keycloak-init.sh
- docs/remediation/: 整改登记表（closure-register.json）

## 协作约定

- main 有分支保护，直接 push 会被拦截（GH006）——必须走 PR
- pytest.importorskip 用于缺依赖时优雅跳过；enterprise 集成 job 要求零跳过
- PG repo 用行内 `# type: ignore[...]` 处理 psycopg dict_row 类型（见 postgres_iam_store.py）
