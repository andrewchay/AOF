---
name: aof-skill-03
description: AOF Docker容器化部署标准，涵盖多阶段构建、安全配置、环境分离、监控等。适用于构建生产就绪的AOF服务镜像和部署流程。
---

# AOF-SKILL-03: Docker 部署

> **定位**: AOF 容器化部署标准，确保部署的一致性、安全性和可观测性。

---

## 一、核心思想

基于 **质量控制** 和 **迭代工作流** 原则：
- **多阶段构建**: 分离构建时和运行时依赖，减小镜像体积
- **安全加固**: 非 root 用户、只读文件系统、最小权限
- **环境分离**: dev/prod 配置分离，避免配置泄露
- **可观测性**: 健康检查、资源限制、日志轮转

---

## 二、使用场景

| 场景 | 示例 |
|------|------|
| 本地开发 | 使用 docker-compose.dev.yml 启动热重载环境 |
| 测试环境 | 使用默认配置快速验证 |
| 生产部署 | 使用 docker-compose.prod.yml + Nginx 反向代理 |
| CI/CD 集成 | 自动化构建和推送镜像 |

---

## 三、镜像构建规范

### Dockerfile 结构

```dockerfile
# ==================== Stage 1: Builder ====================
FROM python:3.13-slim AS builder

# 安装编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 创建虚拟环境
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 安装 Python 依赖
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ==================== Stage 2: Runtime ====================
FROM python:3.13-slim AS runtime

# 安全：创建非 root 用户
RUN groupadd -r aof && useradd -r -g aof aof

# 仅安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# 复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 复制应用代码
COPY --chown=aof:aof . /app/AOF
WORKDIR /app/AOF

# 切换到非 root 用户
USER aof

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s \
    CMD curl -f http://localhost:8787/healthz || exit 1

EXPOSE 8787
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8787"]
```

---

## 四、执行步骤

### Step 1: 配置环境变量

```bash
cd services/semantic_middle_layer_api

# 复制模板
cp .env.example .env

# 编辑配置
vim .env
```

**必需配置**:
```bash
LLM_API_KEY=your-api-key-here
LLM_PROVIDER=custom
LLM_MODEL=deepseek/deepseek-chat
```

---

### Step 2: 验证配置

```bash
# 运行配置检查脚本
./check-docker.sh
```

**预期输出**:
```
✓ Dockerfile exists
✓ docker-compose.yml exists
✓ .env file exists
✓ Multi-stage build detected
✓ Non-root user configured
✓ Health check configured
```

---

### Step 3: 选择部署模式

**开发模式**（热重载、调试日志）:
```bash
./deploy.sh dev

# 或使用 docker-compose 直接
docker-compose -f docker-compose.dev.yml up -d
```

**生产模式**（多 worker、自动重启）:
```bash
./deploy.sh prod

# 或使用 docker-compose 直接
docker-compose -f docker-compose.prod.yml up -d
```

**默认模式**（平衡配置）:
```bash
./deploy.sh build  # 仅构建
docker-compose up -d  # 启动
```

---

### Step 4: 验证部署

```bash
# 检查容器状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 测试健康端点
curl http://localhost:8787/healthz

# 测试 API
curl http://localhost:8787/docs
```

---

## 五、常见错误模式

### 模式1: 镜像构建失败

**症状**: `docker-compose build` 报错

**原因**:
- 网络问题导致依赖下载失败
- Dockerfile 语法错误
- 基础镜像更新导致不兼容

**修复**:
```bash
# 清理缓存重建
docker-compose build --no-cache

# 查看详细日志
docker-compose build 2>&1 | tee build.log

# 手动构建调试
docker build -t test-build -f Dockerfile .
```

---

### 模式2: 容器启动后立即退出

**症状**: `docker-compose ps` 显示 Exit

**原因**:
- 缺少必需的环境变量
- 端口冲突
- 权限问题

**修复**:
```bash
# 查看日志
docker-compose logs

# 检查环境变量
docker-compose exec aof-semantic-api env | grep LLM

# 检查端口占用
lsof -i :8787
```

---

### 模式3: 健康检查失败

**症状**: 容器状态显示 unhealthy

**原因**:
- 服务启动慢，健康检查过早
- 健康检查端点本身有问题
- 网络配置错误

**修复**:
```bash
# 调整健康检查参数
# 在 docker-compose.yml 中
healthcheck:
  interval: 60s          # 增加间隔
  timeout: 30s           # 增加超时
  retries: 5             # 增加重试次数
  start_period: 60s      # 增加启动宽限期
```

---

### 模式4: 内存不足

**症状**: 容器被 OOM Killer 终止

**修复**:
```bash
# 查看资源使用
docker stats

# 调整资源限制
# 在 docker-compose.prod.yml 中
deploy:
  resources:
    limits:
      memory: 4G        # 增加内存限制
    reservations:
      memory: 1G        # 增加预留内存
```

---

## 六、最佳实践

### 1. 镜像优化

```dockerfile
# 使用 .dockerignore 减少构建上下文
# 避免 COPY . /app（复制所有文件）
# 改为只复制必要文件
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ ./src/
```

### 2. 安全加固

```dockerfile
# 使用非 root 用户
RUN useradd -m -u 1000 appuser
USER appuser

# 只读文件系统（生产环境）
docker run --read-only -v /tmp:/tmp myimage

# 限制能力
docker run --cap-drop=ALL --cap-add=NET_BIND_SERVICE myimage
```

### 3. 日志管理

```yaml
# docker-compose.yml
services:
  app:
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "3"
        labels: "service_name"
        env: "OS_VERSION"
```

### 4. 备份策略

```bash
# 数据卷备份
docker run --rm -v aof-data:/data -v $(pwd):/backup alpine \
    tar czf /backup/aof-data-backup.tar.gz -C /data .

# 恢复
docker run --rm -v aof-data:/data -v $(pwd):/backup alpine \
    tar xzf /backup/aof-data-backup.tar.gz -C /data
```

---

## 七、示例

### 完整生产部署流程

```bash
# 1. 准备环境
export VERSION=1.0.0
export LLM_API_KEY="sk-..."

# 2. 构建镜像
docker-compose -f docker-compose.prod.yml build

# 3. 打标签
docker tag aof-semantic-api:latest aof-semantic-api:${VERSION}

# 4. 推送镜像（可选）
docker push aof-semantic-api:${VERSION}

# 5. 部署
docker-compose -f docker-compose.prod.yml up -d

# 6. 验证
curl -f http://localhost:8787/healthz || echo "Deploy failed"

# 7. 查看状态
docker-compose -f docker-compose.prod.yml ps
docker-compose -f docker-compose.prod.yml logs -f
```

---

## 八、关联文档

- **元技能**: [质量控制](../methodology/00-META-10_质量控制.md), [可读性](../methodology/00-META-07_可读性.md)
- **SKILL**: [API服务开发](./AOF-SKILL-02_API服务开发.md)
- **部署脚本**: [deploy.sh](../../services/semantic_middle_layer_api/deploy.sh)

---

## 九、变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2024-03-28 | 初始版本，总结 Docker 部署实践 |
