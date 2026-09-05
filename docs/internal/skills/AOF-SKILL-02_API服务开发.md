---
name: aof-skill-02
description: AOF FastAPI服务开发规范，涵盖端点设计、受治理身份/租户边界、错误处理和质量评估。适用于构建可维护、可审计的语义中间层API服务。
---

# AOF-SKILL-02: API 服务开发

> **定位**: AOF服务层开发标准，确保 API 的一致性、可靠性、可观测性及受治理语义边界。传统 topic API 可以保留兼容；知识发布、编译、查询和动作必须使用签名主体和 release-pinned 控制面。

---

## 一、核心思想

基于 **柳叶刀方法** 和 **质量控制** 原则：
- **模块化分解**: 每个端点职责单一，可独立测试
- **混合驱动**: 规则检查（快）+ LLM评估（准）结合
- **防御式编程**: 输入验证、错误处理、降级策略
- **可观测性**: 健康检查、日志、指标全覆盖
- **治理优先**: 来源不是发布知识，body 中 actor 不是授权，查询必须可关联 release 与 receipt

---

## 二、使用场景

| 场景 | 示例 |
|------|------|
| 新增语义端点 | 添加 `/v1/semantic/search` 端点 |
| 集成 LLM 能力 | 生成候选/辅助评估，不能绕过发布、策略与回执 |
| 受治理语义端点 | proposal、compiler、query、ontology、continuous ingestion |
| 数据摄取接口 | 文档、元数据、反馈的摄取端点 |
| 导出功能 | OWL、Mapping、Regression 导出 |

---

## 三、端点设计规范

### URL 规范

```
/v{version}/{resource}/{action}
```

示例:
- `/v1/semantic/retrieve` - 语义检索
- `/v1/semantic/query` - release-pinned、策略受控的查询
- `/v1/semantic/proposals/*` - 资源提案、验证、审批、编译和发布
- `/v1/ingest/docs` - 文档摄取
- `/v1/build/topic` - 构建主题

`/v1/semantic/compile` 已退役并返回 `410 Gone`；不得用新的“生成 SQL”端点重建该旁路。

### HTTP 方法

| 方法 | 用途 | 示例 |
|------|------|------|
| GET | 获取资源 | `/v1/artifacts/{run_id}` |
| POST | 创建/执行 | `/v1/build/topic` |
| PUT | 更新（完整）| （较少使用）|
| DELETE | 删除 | （较少使用）|

### 请求/响应规范

**请求**:
```python
class RetrieveReq(BaseModel):
    topic: str                    # 主题标识
    query: str                    # 查询内容
    top_k: int = 10              # 可选，默认参数
    use_semantic: bool = True    # 可选，功能开关
```

**响应**:
```python
{
    "topic": "users",           # 主题
    "query": "active",          # 原始查询
    "hits": [...],              # 结果列表
    "total_matches": 5          # 元数据
}
```

**错误响应**:
```python
{
    "detail": "Error message"   # FastAPI 默认格式
}
# 或自定义格式
{
    "error": {
        "type": "ValidationError",
        "message": "...",
        "suggestions": [...]
    }
}
```

---

## 四、执行步骤

### Step 1: 定义请求/响应模型

```python
from pydantic import BaseModel, Field

class MyRequest(BaseModel):
    """请求模型文档"""
    topic: str = Field(description="主题标识")
    param: str = Field(description="参数说明")
    
    class Config:
        json_schema_extra = {
            "example": {
                "topic": "users",
                "param": "value"
            }
        }

class MyResponse(BaseModel):
    """响应模型"""
    topic: str
    result: str
    score: float
```

---

### Step 2: 实现端点函数

```python
from fastapi import HTTPException

@app.post('/v1/resource/action')
def my_endpoint(req: MyRequest) -> MyResponse:
    """
    端点功能描述
    
    - **参数**: topic - 主题标识
    - **返回**: 处理结果
    """
    # 1. 参数验证（自动由 Pydantic 完成）
    
    # 2. 加载资源
    mf = _load_latest_manifest(req.topic)
    if not mf:
        raise HTTPException(status_code=404, detail='Topic not found')
    
    # 3. 执行业务逻辑
    result = _do_something(req)
    
    # 4. 返回响应
    return MyResponse(
        topic=req.topic,
        result=result,
        score=0.95
    )
```

---

### Step 3: 添加错误处理

```python
def _load_latest_manifest(topic: str) -> dict | None:
    """加载 manifest，处理异常情况"""
    try:
        mf_path = _latest_manifest_for_topic(topic)
        if not mf_path or not mf_path.exists():
            return None
        return json.loads(mf_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail='Corrupted manifest file')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to load manifest: {str(e)}')
```

---

### Step 4: 集成 LLM（如需要）

```python
def _call_llm(messages: list[dict], temperature: float = 0.1) -> str:
    """LLM 调用封装"""
    if not LLM_API_KEY:
        raise HTTPException(status_code=500, detail='LLM not configured')
    
    try:
        import openai
        client = openai.OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_ENDPOINT
        )
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=temperature
        )
        return response.choices[0].message.content or ''
    except Exception as e:
        # 降级策略：返回空或使用模板
        raise HTTPException(status_code=503, detail=f'LLM service unavailable: {str(e)}')
```

---

### Step 5: 添加测试

```python
# tests/test_api_my_feature.py
from fastapi.testclient import TestClient

def test_my_endpoint_success(client: TestClient) -> None:
    """测试正常情况"""
    response = client.post('/v1/resource/action', json={
        'topic': 'test',
        'param': 'value'
    })
    assert response.status_code == 200
    data = response.json()
    assert 'result' in data

def test_my_endpoint_not_found(client: TestClient) -> None:
    """测试资源不存在"""
    response = client.post('/v1/resource/action', json={
        'topic': 'nonexistent',
        'param': 'value'
    })
    assert response.status_code == 404
```

---

## 五、常见错误模式

### 模式1: Pydantic 验证错误

**症状**: 422 Unprocessable Entity

**原因**:
- 请求体缺少必填字段
- 字段类型不匹配
- 字符串格式错误（如枚举值）

**修复**:
```python
# 添加更友好的错误提示
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation Error",
            "details": exc.errors()
        }
    )
```

---

### 模式2: 同步阻塞

**症状**: 高并发时响应缓慢

**原因**:
- 同步 I/O 操作（文件读取、网络请求）阻塞事件循环

**修复**:
```python
from fastapi import BackgroundTasks

@app.post('/v1/build/topic')
def build_topic(req: BuildReq, background_tasks: BackgroundTasks) -> dict:
    """异步执行耗时操作"""
    background_tasks.add_task(_run_build_async, req.topic)
    return {'status': 'started', 'topic': req.topic}
```

---

### 模式3: LLM 调用超时

**症状**: 504 Gateway Timeout

**修复**:
```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=4)

async def _call_llm_async(messages: list[dict]) -> str:
    """异步调用 LLM"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: _call_llm(messages)
    )
```

---

## 六、最佳实践

### 1. 输入验证

- **使用 Pydantic 模型**: 自动验证类型、必填字段
- **自定义验证器**: 复杂业务规则

```python
from pydantic import validator

class MyRequest(BaseModel):
    topic: str
    max_items: int = 10
    
    @validator('max_items')
    def validate_max_items(cls, v):
        if v < 1 or v > 100:
            raise ValueError('max_items must be between 1 and 100')
        return v
```

### 2. 错误处理层级

```
L1: Pydantic 验证（自动）
L2: 业务规则验证（端点内）
L3: 资源加载验证（辅助函数）
L4: 外部服务调用（try-except）
```

### 3. 性能优化

- **缓存频繁访问的数据**: 使用 `@lru_cache`
- **数据库连接池**: 如果使用数据库
- **流式响应**: 大文件下载使用 `StreamingResponse`

### 4. 可观测性

```python
import logging

logger = logging.getLogger(__name__)

@app.post('/v1/resource/action')
def my_endpoint(req: MyRequest) -> MyResponse:
    logger.info(f"Processing request for topic: {req.topic}")
    
    start_time = time.time()
    result = _process(req)
    duration = time.time() - start_time
    
    logger.info(f"Request completed in {duration:.2f}s")
    return result
```

---

## 七、示例

### 完整端点实现

```python
@app.post('/v1/semantic/evaluate', response_model=EvaluateResponse)
def semantic_evaluate(req: EvaluateReq) -> EvaluateResponse:
    """
    评估 SQL 查询质量
    
    结合规则检查和 LLM 评估，提供综合质量评分和改进建议。
    """
    # 规则检查（快）
    rule_score, rule_risks = _rule_based_check(req.candidate)
    
    # LLM 评估（慢，可选）
    llm_score = None
    if LLM_API_KEY:
        try:
            llm_score = _llm_evaluate(req.candidate)
        except Exception as e:
            logger.warning(f"LLM evaluation failed: {e}")
    
    # 综合评分
    final_score = (rule_score + (llm_score or rule_score)) / 2
    
    return EvaluateResponse(
        topic=req.topic,
        score=round(final_score, 2),
        risks=rule_risks,
        llm_score=llm_score
    )
```

---

## 八、关联文档

- **元技能**: [可读性](../methodology/00-META-07_可读性.md), [Agent提示词工程](../methodology/00-META-09_Agent提示词工程.md)
- **SKILL**: [本体抽提流程](./AOF-SKILL-01_本体抽提流程.md)
- **代码**: [app.py](../../services/semantic_middle_layer_api/app.py)

---

## 九、变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2024-03-28 | 初始版本，总结 FastAPI 服务开发实践 |
