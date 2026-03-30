# AOF Semantic Middle Layer API

FastAPI 服务，提供本体构建、语义检索、SQL 生成和查询评估能力。

## 快速开始

### 使用部署脚本

```bash
# 1. 复制环境变量配置
cp .env.example .env
# 编辑 .env 填入你的 LLM_API_KEY

# 2. 启动开发环境
./deploy.sh dev

# 3. 查看服务状态
./deploy.sh logs
```

### 使用 Docker Compose

```bash
# 开发模式（热重载）
docker-compose -f docker-compose.dev.yml up -d

# 生产模式
docker-compose -f docker-compose.prod.yml up -d

# 默认模式
docker-compose up -d
```

## 环境变量

复制 `.env.example` 到 `.env` 并配置：

```bash
LLM_API_KEY=your-api-key-here
LLM_PROVIDER=custom
LLM_MODEL=deepseek/deepseek-chat
LLM_ENDPOINT=https://api.deepseek.com/v1
```

## API 端点

启动后访问：http://localhost:8787/docs

### 核心端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/healthz` | GET | 健康检查 |
| `/v1/ingest/docs` | POST | 摄取文档 |
| `/v1/build/topic` | POST | 构建主题本体 |
| `/v1/semantic/retrieve` | POST | 语义检索 |
| `/v1/semantic/compile` | POST | SQL 生成 |
| `/v1/semantic/evaluate` | POST | 查询评估 |

## Docker 镜像

### 构建镜像

```bash
# 使用部署脚本
./deploy.sh build

# 或直接使用 docker-compose
docker-compose build

# 带构建参数
docker-compose build --build-arg COGNEE_PIP_SPEC="cognee==0.1.45"
```

### 镜像特性

- **多阶段构建**: 减小镜像体积
- **非 root 用户**: 安全运行
- **健康检查**: 自动故障检测
- **资源限制**: 防止资源耗尽

## 部署选项

### 开发环境

```bash
./deploy.sh dev
```

特性：
- 代码热重载
- 调试日志
- 本地代码挂载

### 生产环境

```bash
./deploy.sh prod
```

特性：
- 多 worker 进程
- 只读文件系统
- 资源限制
- 自动重启

## 监控

### 健康检查

```bash
curl http://localhost:8787/healthz
```

### 查看日志

```bash
# 实时日志
./deploy.sh logs

# 或使用 docker-compose
docker-compose logs -f
```

### Docker 状态

```bash
docker-compose ps
docker stats
```

## 目录结构

```
.
├── Dockerfile                  # 多阶段构建配置
├── docker-compose.yml          # 默认配置
├── docker-compose.dev.yml      # 开发配置
├── docker-compose.prod.yml     # 生产配置
├── deploy.sh                   # 部署脚本
├── .env.example                # 环境变量示例
├── nginx.conf                  # Nginx 反向代理配置
├── app.py                      # FastAPI 应用
├── requirements.txt            # Python 依赖
└── API.md                      # API 详细文档
```

## 故障排除

### 服务无法启动

```bash
# 检查日志
docker-compose logs

# 检查环境变量
cat .env

# 重建镜像
docker-compose down
docker-compose up -d --build
```

### 健康检查失败

```bash
# 手动检查健康端点
curl -v http://localhost:8787/healthz

# 检查容器状态
docker ps
```

### 内存不足

```bash
# 查看资源使用
docker stats

# 增加 Docker 内存限制
# 编辑 docker-compose.prod.yml 中的 deploy.resources
```

## 安全建议

1. **使用 HTTPS**: 配置 SSL 证书
2. **限制访问**: 使用防火墙限制端口访问
3. **定期更新**: 更新基础镜像和依赖
4. **密钥管理**: 使用 Docker secrets 或环境变量
5. **日志审计**: 启用访问日志记录

## 性能优化

### 调整 Worker 数量

在 `docker-compose.prod.yml` 中：

```yaml
environment:
  - UVICORN_WORKERS=4
```

### 启用连接池

如果使用数据库，配置连接池大小。

### 使用 CDN

静态资源使用 CDN 加速。

## License

[Your License]
