# AOF Kubernetes 部署指南

本文档介绍如何在 Kubernetes 集群上部署 AOF 服务。

## 前置条件

- Kubernetes 1.24+
- kubectl 已配置
- 至少 4 核 8GB 内存的节点
- （可选）NebulaGraph 集群（如果启用分布式存储）

## 快速开始

### 1. 一键部署

```bash
kubectl apply -f k8s/
```

### 2. 查看状态

```bash
# 查看命名空间
kubectl get ns

# 查看 Pod
kubectl get pods -n aof

# 查看服务
kubectl get svc -n aof

# 查看 Ingress
kubectl get ingress -n aof
```

### 3. 验证部署

```bash
# 端口转发（本地测试）
kubectl port-forward svc/aof-api 8787:80 -n aof

# 访问 API
curl http://localhost:8787/health
```

## 详细配置

### 命名空间

```bash
kubectl apply -f k8s/namespace.yaml
```

### 配置管理

#### ConfigMap（非敏感配置）

编辑 `configmap.yaml`：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: aof-config
  namespace: aof
data:
  API_PORT: "8787"
  STORAGE_BACKEND: "nebula"  # 或 "cognee"
  # ...
```

应用：
```bash
kubectl apply -f k8s/configmap.yaml
```

#### Secret（敏感配置）

编辑 `secret.yaml`：

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: aof-secrets
  namespace: aof
stringData:
  NEBULA_PASSWORD: "your-password"
  LLM_API_KEY: "your-api-key"
  JWT_SECRET_KEY: "your-secret"
```

应用：
```bash
kubectl apply -f k8s/secret.yaml
```

### 存储后端

#### 选项 1：Cognee（单机，轻量）

```bash
# 修改 ConfigMap
kubectl patch configmap aof-config -n aof --type merge \
  -p '{"data":{"STORAGE_BACKEND":"cognee"}}'

# 重启 Deployment
kubectl rollout restart deployment aof-api -n aof
```

#### 选项 2：NebulaGraph（分布式，推荐生产）

```bash
# 部署 NebulaGraph
kubectl apply -f k8s/nebula.yaml

# 等待就绪（约 2 分钟）
kubectl wait --for=condition=ready pod -l app=nebula-graphd -n aof --timeout=300s

# 修改 ConfigMap
kubectl patch configmap aof-config -n aof --type merge \
  -p '{"data":{"STORAGE_BACKEND":"nebula"}}'

# 重启 Deployment
kubectl rollout restart deployment aof-api -n aof
```

### 缓存

```bash
# 部署 Redis
kubectl apply -f k8s/redis.yaml
```

### 应用部署

```bash
# 部署 API 服务
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 配置 Ingress（需要 Ingress Controller）
kubectl apply -f k8s/ingress.yaml

# 配置 HPA（自动扩缩容）
kubectl apply -f k8s/hpa.yaml
```

## 运维操作

### 查看日志

```bash
# 查看所有 Pod 日志
kubectl logs -l app=aof-api -n aof --tail=100

# 查看特定 Pod 日志
kubectl logs aof-api-xxx -n aof -f

# 查看 NebulaGraph 日志
kubectl logs -l app=nebula-graphd -n aof
```

### 扩缩容

#### 手动扩缩容

```bash
# 扩容到 5 个副本
kubectl scale deployment aof-api --replicas=5 -n aof

# 缩容到 2 个副本
kubectl scale deployment aof-api --replicas=2 -n aof
```

#### 自动扩缩容（HPA）

```bash
# 查看 HPA 状态
kubectl get hpa -n aof

# 编辑 HPA 配置
kubectl edit hpa aof-api -n aof
```

默认配置：
- 最小副本：3
- 最大副本：10
- CPU 阈值：70%
- 内存阈值：80%

### 更新部署

```bash
# 滚动更新
kubectl rollout restart deployment aof-api -n aof

# 查看更新状态
kubectl rollout status deployment aof-api -n aof

# 回滚（如需要）
kubectl rollout undo deployment aof-api -n aof
```

### 监控

```bash
# 查看 Pod 资源使用
kubectl top pod -n aof

# 查看节点资源使用
kubectl top node
```

需要安装 metrics-server：
```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

## 生产环境建议

### 1. 高可用配置

```yaml
# deployment.yaml
spec:
  replicas: 3  # 至少 3 个副本
  strategy:
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0  # 确保零停机
```

### 2. 资源限制

```yaml
resources:
  requests:
    memory: "1Gi"
    cpu: "500m"
  limits:
    memory: "4Gi"
    cpu: "2000m"
```

### 3. 持久化存储

```yaml
# NebulaGraph 存储类配置
volumeClaimTemplates:
- metadata:
    name: data
  spec:
    storageClassName: "fast-ssd"  # 使用 SSD 存储类
    accessModes: ["ReadWriteOnce"]
    resources:
      requests:
        storage: 500Gi
```

### 4. 网络策略

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: aof-network-policy
  namespace: aof
spec:
  podSelector:
    matchLabels:
      app: aof-api
  policyTypes:
  - Ingress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: ingress-nginx
    ports:
    - protocol: TCP
      port: 8787
```

### 5. 备份策略

```bash
# 备份 NebulaGraph
kubectl exec -it nebula-metad-0 -n aof -- /usr/local/nebula/bin/db_dump

# 备份到 S3
kubectl create job backup --image=amazon/aws-cli -- \
  aws s3 sync /data s3://aof-backup/$(date +%Y%m%d)
```

## 故障排查

### Pod 无法启动

```bash
# 查看事件
kubectl get events -n aof --sort-by='.lastTimestamp'

# 查看 Pod 详情
kubectl describe pod aof-api-xxx -n aof

# 检查资源限制
kubectl describe node
```

### 服务不可访问

```bash
# 检查 Service
kubectl get svc aof-api -n aof
kubectl describe svc aof-api -n aof

# 检查 Endpoints
kubectl get endpoints aof-api -n aof

# 检查 Ingress
kubectl describe ingress aof-api -n aof
```

### 性能问题

```bash
# 查看 Pod 资源使用
kubectl top pod -n aof

# 查看 HPA 状态
kubectl describe hpa aof-api -n aof

# 查看日志中的慢查询
kubectl logs -l app=aof-api -n aof | grep "slow"
```

## 清理

```bash
# 删除所有资源
kubectl delete -f k8s/

# 删除命名空间（包括所有资源）
kubectl delete namespace aof
```

## 参考

- [Kubernetes 官方文档](https://kubernetes.io/docs/)
- [NebulaGraph K8s 部署](https://docs.nebula-graph.com.cn/3.8.0/4.deployment-and-installation/3.deploy-nebula-graph-with-kubernetes/)
- [FastAPI 部署](https://fastapi.tiangolo.com/deployment/)

## 支持

如有问题，请提交 [Issue](https://github.com/your-org/aof/issues)。
