#!/bin/bash
# FIBO 映射试点 — Cognify 对照实验脚本
# 对照组: 无本体 | 实验组: FIBO 增强本体

set -e

PROJECT_ROOT="/Users/chaihao/LLM/AOF"
EXPERIMENT_DIR="$PROJECT_ROOT/examples/procurement_fibo_pilot/experiment"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
TEST_DATA="$EXPERIMENT_DIR/test_data.txt"

echo "========================================"
echo "AOF FIBO 映射试点 — Cognify 对照实验"
echo "========================================"
echo ""

# 检查环境
echo "[1/6] 检查环境..."
if [ ! -f "$VENV_PYTHON" ]; then
    echo "错误: 未找到 Python 解释器: $VENV_PYTHON"
    exit 1
fi

if [ ! -f "$TEST_DATA" ]; then
    echo "错误: 未找到测试数据: $TEST_DATA"
    exit 1
fi

echo "✓ 环境检查通过"
echo ""

# 检查 API 密钥
if [ -z "$LLM_API_KEY" ]; then
    echo "警告: LLM_API_KEY 未设置，请在运行前设置:"
    echo "  export LLM_API_KEY='your-key'"
    echo ""
    read -p "是否继续? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "[2/6] 清理历史数据集（如存在）..."
# 可选：清理旧数据集以确保干净状态
# $VENV_PYTHON "$PROJECT_ROOT/scripts/cleanup_dataset.py" procurement_fibo_control 2>/dev/null || true
# $VENV_PYTHON "$PROJECT_ROOT/scripts/cleanup_dataset.py" procurement_fibo_experiment 2>/dev/null || true
echo "✓ 清理完成（或无需清理）"
echo ""

echo "[3/6] 对照组实验: 无本体"
echo "----------------------------------------"
echo "Dataset: procurement_fibo_control"
echo "Ontology: 无"
echo "----------------------------------------"

LLM_PROVIDER="custom" \
LLM_MODEL="deepseek/deepseek-chat" \
LLM_ENDPOINT="https://api.deepseek.com/v1" \
EMBEDDING_PROVIDER="custom" \
EMBEDDING_MODEL="deepseek/deepseek-embedding" \
EMBEDDING_ENDPOINT="https://api.deepseek.com/v1" \
COGNEE_SKIP_CONNECTION_TEST="true" \
$VENV_PYTHON "$PROJECT_ROOT/aof_add.py" \
    --spec "$EXPERIMENT_DIR/spec_control.json" \
    --data-path "$TEST_DATA"

echo ""
echo "✓ 对照组 add 完成"
echo ""

echo "[4/6] 实验组实验: FIBO 增强本体"
echo "----------------------------------------"
echo "Dataset: procurement_fibo_experiment"
echo "Ontology: procurement_o2c_fibo.owl"
echo "----------------------------------------"

LLM_PROVIDER="custom" \
LLM_MODEL="deepseek/deepseek-chat" \
LLM_ENDPOINT="https://api.deepseek.com/v1" \
EMBEDDING_PROVIDER="custom" \
EMBEDDING_MODEL="deepseek/deepseek-embedding" \
EMBEDDING_ENDPOINT="https://api.deepseek.com/v1" \
COGNEE_SKIP_CONNECTION_TEST="true" \
$VENV_PYTHON "$PROJECT_ROOT/aof_add.py" \
    --spec "$EXPERIMENT_DIR/spec_fibo.json" \
    --data-path "$TEST_DATA"

echo ""
echo "✓ 实验组 add 完成"
echo ""

echo "[5/6] 运行 cognify（如 spec 配置自动运行则跳过）"
echo "注意: 如果 aof_add.py 未自动触发 cognify，请手动运行:"
echo "  $VENV_PYTHON $PROJECT_ROOT/aof_run.py --spec $EXPERIMENT_DIR/spec_control.json --cognify"
echo "  $VENV_PYTHON $PROJECT_ROOT/aof_run.py --spec $EXPERIMENT_DIR/spec_fibo.json --cognify"
echo ""

echo "[6/6] 实验完成"
echo "========================================"
echo ""
echo "下一步:"
echo "1. 检查两个数据集的图谱输出"
echo "2. 对比抽取的实体和关系质量"
echo "3. 运行评估脚本（待创建）"
echo ""
echo "数据集位置:"
echo "  对照组: procurement_fibo_control"
echo "  实验组: procurement_fibo_experiment"
