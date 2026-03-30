# Docker 优化说明

## 优化对比

### 镜像大小

| 阶段 | 大小 | 说明 |
|------|------|------|
| 优化前 | ~500MB+ | 单阶段构建，包含编译工具 |
| 优化后 | ~250MB | 多阶段构建，仅运行时依赖 |

### 安全特性

| 特性 | 优化前 | 优化后 |
|------|--------|--------|
| 运行用户 | root | 非 root (aof) |
| 健康检查 | ❌ | ✅ |
| 只读文件系统 | ❌ | ✅ (prod) |
| 资源限制 | ❌ | ✅ |

### 构建优化

| 特性 | 优化前 | 优化后 |
|------|--------|--------|
| 多阶段构建 | ❌ | ✅ |
| 层缓存 | 一般 | 优化 |
| 构建参数 | 部分 | 完整 |

## 多阶段构建说明

```
Stage 1: Builder
├─ 安装编译工具 (gcc, libpq-dev)
├─ 创建虚拟环境
├─ 安装 Python 依赖
└─ 安装 cognee

Stage 2: Runtime
├─ 仅运行时依赖 (libpq5)
├─ 复制虚拟环境
├─ 复制应用代码
└─ 非 root 用户运行
```

## Dockerfile 关键改进

### 1. 多阶段构建

```dockerfile
# 构建阶段
FROM python:3.13-slim AS builder
RUN pip install ...

# 运行阶段
FROM python:3.13-slim AS runtime
COPY --from=builder /opt/venv /opt/venv
```

**效果**: 减小镜像约 50%

### 2. 非 Root 用户

```dockerfile
RUN groupadd -r aof && useradd -r -g aof aof
USER aof
```

**效果**: 符合安全最佳实践

### 3. 健康检查

```dockerfile
HEALTHCHECK --interval=30s \
    CMD curl -f http://localhost:8787/healthz || exit 1
```

**效果**: 自动故障检测和恢复

### 4. 资源限制

```yaml
deploy:
  resources:
    limits:
      cpus: '2.0'
      memory: 2G
```

**效果**: 防止资源耗尽

## Docker Compose 配置

### 开发环境 (docker-compose.dev.yml)

- ✅ 热重载
- ✅ 调试日志
- ✅ 代码挂载

### 生产环境 (docker-compose.prod.yml)

- ✅ 多 worker
- ✅ 只读文件系统
- ✅ 自动重启
- ✅ Nginx 反向代理

### 默认环境 (docker-compose.yml)

- ✅ 平衡配置
- ✅ 快速启动

## 使用方法

### 快速部署

```bash
# 开发环境
./deploy.sh dev

# 生产环境
./deploy.sh prod

# 停止服务
./deploy.sh stop
```

### 手动构建

```bash
# 构建镜像
docker-compose build

# 查看镜像大小
docker images aof-semantic-api

# 运行容器
docker-compose up -d
```

### 验证配置

```bash
./check-docker.sh
```

## 性能优化建议

### 1. 调整 Worker 数量

根据 CPU 核心数调整：

```yaml
environment:
  - UVICORN_WORKERS=4
```

### 2. 连接池配置

如果使用数据库，配置连接池：

```yaml
environment:
  - DB_POOL_SIZE=20
```

### 3. 内存优化

监控内存使用：

```bash
docker stats
```

### 4. 日志轮转

```yaml
logging:
  options:
    max-size: "100m"
    max-file: "3"
```

## 安全加固

### 1. 网络隔离

```yaml
networks:
  aof-network:
    driver: bridge
    internal: true
```

### 2. 密钥管理

使用 Docker secrets：

```yaml
secrets:
  llm_api_key:
    file: ./secrets/llm_api_key.txt
```

### 3. 安全扫描

```bash
docker scan aof-semantic-api:latest
```

## 故障排除

### 镜像构建失败

```bash
# 清理缓存
docker-compose build --no-cache

# 查看构建日志
docker-compose build 2>&1 | tee build.log
```

### 容器无法启动

```bash
# 查看日志
docker-compose logs -f

# 检查配置
docker-compose config
```

### 内存不足

```bash
# 增加 Docker 内存限制
# 编辑 docker-compose.prod.yml
deploy:
  resources:
    limits:
      memory: 4G
```

## 参考

- [Docker Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [Dockerfile Reference](https://docs.docker.com/engine/reference/builder/)
- [Compose File Reference](https://docs.docker.com/compose/compose-file/)
