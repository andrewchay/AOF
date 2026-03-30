# AOF 测试套件

本目录包含 AOF 项目的完整测试套件，包括单元测试、集成测试和端到端测试。

## 测试结构

```
tests/
├── conftest.py                          # pytest 配置和共享 fixtures
├── test_spec_mapper.py                  # 单元测试：配置映射
├── test_quality_gate.py                 # 单元测试：质量门
├── test_preflight.py                    # 单元测试：预检检查
├── test_error_surface.py                # 单元测试：错误处理
├── test_add_dataset_bridge.py           # 单元测试：数据集添加
├── test_api_integration.py              # 集成测试：FastAPI 服务
├── test_data_adapter.py                 # 集成测试：数据适配器
├── test_ontology_factory_integration.py # 集成测试：本体工厂
└── test_end_to_end.py                   # 端到端测试：完整流程
```

## 运行测试

### 安装测试依赖

```bash
# 使用 uv（推荐）
uv pip install -r requirements-dev.txt

# 或使用 pip
pip install -r requirements-dev.txt
```

### 运行所有测试

```bash
# 使用 pytest
python -m pytest

# 详细输出
python -m pytest -v

# 生成覆盖率报告
python -m pytest --cov=. --cov-report=html
```

### 运行特定测试

```bash
# 仅运行单元测试
python -m pytest tests/test_spec_mapper.py tests/test_quality_gate.py

# 仅运行集成测试
python -m pytest tests/test_api_integration.py

# 仅运行端到端测试
python -m pytest tests/test_end_to_end.py

# 按标记运行
python -m pytest -m unit        # 单元测试
python -m pytest -m integration # 集成测试
python -m pytest -m e2e         # 端到端测试
```

### 快速测试（跳过慢速测试）

```bash
python -m pytest -m "not slow"
```

## 测试分类

### 单元测试
- **特点**：快速、无外部依赖、隔离测试
- **文件**：`test_spec_mapper.py`, `test_quality_gate.py`, `test_preflight.py` 等
- **运行时间**：< 1 秒

### 集成测试
- **特点**：测试组件间交互，可能需要临时文件
- **文件**：`test_api_integration.py`, `test_data_adapter.py`, `test_ontology_factory_integration.py`
- **运行时间**：几秒到几十秒

### 端到端测试
- **特点**：测试完整流程，可能需要外部服务（cognee）
- **文件**：`test_end_to_end.py`
- **运行时间**：取决于外部服务响应

## 编写新测试

### 基本结构

```python
import pytest
from pathlib import Path

def test_something(temp_dir: Path) -> None:
    """测试描述。"""
    # Arrange
    input_file = temp_dir / "input.txt"
    input_file.write_text("test content")
    
    # Act
    result = process_file(input_file)
    
    # Assert
    assert result == "expected"
```

### 使用 Fixtures

```python
@pytest.fixture
def sample_data() -> str:
    return "sample data"

def test_with_fixture(sample_data: str) -> None:
    assert "sample" in sample_data
```

### 标记测试类型

```python
import pytest

@pytest.mark.unit
def test_simple() -> None:
    pass

@pytest.mark.integration
def test_with_deps() -> None:
    pass

@pytest.mark.slow
def test_long_running() -> None:
    pass
```

## 测试数据

测试数据通过 `conftest.py` 中的 fixtures 提供：

- `temp_dir`: 临时目录
- `sample_spec`: 示例 AOF 配置
- `sample_sql_data`: 示例 SQL 数据
- `sample_csv_data`: 示例 CSV 数据
- `mock_cognee_env`: Mock 环境变量
- `clean_api_state`: 干净的 API 状态

## 持续集成

建议的 CI 配置：

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.13'
      
      - name: Install dependencies
        run: |
          pip install -r requirements-dev.txt
          pip install -e .
      
      - name: Run unit tests
        run: pytest -m unit -v
      
      - name: Run integration tests
        run: pytest -m integration -v
      
      - name: Run e2e tests
        run: pytest -m e2e -v
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
```

## 注意事项

1. **外部依赖**：需要 cognee 的测试会自动跳过如果 cognee 未安装
2. **LLM 调用**：涉及 LLM 的测试需要设置环境变量 `LLM_API_KEY`
3. **临时文件**：所有测试使用 `temp_dir` fixture 创建临时文件，自动清理
4. **并行运行**：可以使用 `pytest-xdist` 加速测试：`pytest -n auto`
