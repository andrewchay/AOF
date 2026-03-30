# 服务打包与 cognee 整合说明

目标：把 AOF + cognee 作为一个可部署服务交付，不依赖本机绝对路径。

## 一、整合策略（推荐）

1. AOF 作为主仓库与服务入口。
2. cognee 作为依赖安装（而不是运行时本地路径绑定）。
3. 所有路径通过环境变量控制：
   - `AOF_ROOT`
   - `COGNEE_ROOT`（可选）
4. 统一通过 FastAPI 对外暴露中间层能力。

## 二、构建参数

Docker 构建时可指定：

- `INSTALL_COGNEE=1`：安装 cognee 依赖。
- `COGNEE_PIP_SPEC=<spec>`：依赖来源。

示例：

```bash
cd /Users/chaihao/LLM/AOF
docker build \
  -f services/semantic_middle_layer_api/Dockerfile \
  --build-arg INSTALL_COGNEE=1 \
  --build-arg COGNEE_PIP_SPEC="cognee==0.1.45" \
  -t aof-semantic-api:latest .
```

若你有内部 fork：

```bash
--build-arg COGNEE_PIP_SPEC="git+https://<your_git>/cognee.git@<commit_hash>"
```

## 三、运行

```bash
docker run --rm -p 8787:8787 \
  -e LLM_API_KEY="<your_key>" \
  -e AOF_ROOT=/app/AOF \
  -v $(pwd)/data:/app/AOF/data \
  -v $(pwd)/logs:/app/AOF/logs \
  -v $(pwd)/ontologies:/app/AOF/ontologies \
  aof-semantic-api:latest
```

或使用：

```bash
docker compose -f services/semantic_middle_layer_api/docker-compose.yml up --build
```

## 四、关于 COGNEE_ROOT

- 推荐场景：cognee 作为 pip 依赖安装，`COGNEE_ROOT` 可不设置。
- 如果你需要读取 cognee 本地日志或源码调试，可额外设置 `COGNEE_ROOT`。

## 五、版本锁定建议

1. 锁定 AOF 版本（git tag）。
2. 锁定 cognee 版本（`COGNEE_PIP_SPEC` 用具体版本或 commit）。
3. 产物级回归：每次发布前跑回归样例库。

## 六、当前已完成的去硬编码改造

- 中间层入口脚本支持 `AOF_ROOT`/`AOF_PY`。
- ontology factory 入口脚本支持 `AOF_ROOT`/`AOF_PY`。
- `build_testdata_ontology_factory.py` 支持 `AOF_ROOT` 与 `COGNEE_ROOT` 环境参数。
- FastAPI 服务入口支持 `AOF_ROOT`。
