#!/bin/bash
# AOF Test Runner
# Usage: ./run_tests.sh [unit|integration|e2e|all]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Default to all tests
TEST_TYPE="${1:-all}"

echo "================================"
echo "AOF Test Runner"
echo "================================"
echo ""

# Check if pytest is available
if ! command -v .venv/bin/python &> /dev/null; then
    echo "Error: Virtual environment not found"
    echo "Please create and activate virtual environment first:"
    echo "  uv venv --python 3.13 .venv"
    exit 1
fi

# Install test dependencies if needed
if ! .venv/bin/python -c "import pytest" 2>/dev/null; then
    echo "Installing test dependencies..."
    uv pip install pytest pytest-asyncio httpx
fi

echo "Running tests: $TEST_TYPE"
echo ""

case $TEST_TYPE in
    unit)
        echo "Running unit tests..."
        .venv/bin/python -m pytest tests/test_spec_mapper.py tests/test_quality_gate.py tests/test_preflight.py tests/test_error_surface.py tests/test_add_dataset_bridge.py -v -m unit
        ;;
    integration)
        echo "Running integration tests..."
        .venv/bin/python -m pytest tests/test_api_integration.py tests/test_data_adapter.py tests/test_ontology_factory_integration.py -v -m integration
        ;;
    e2e)
        echo "Running end-to-end tests..."
        .venv/bin/python -m pytest tests/test_end_to_end.py -v -m e2e
        ;;
    all)
        echo "Running all tests..."
        .venv/bin/python -m pytest -v
        ;;
    quick)
        echo "Running quick tests (excluding slow)..."
        .venv/bin/python -m pytest -v -m "not slow"
        ;;
    *)
        echo "Usage: $0 [unit|integration|e2e|all|quick]"
        echo ""
        echo "Options:"
        echo "  unit         - Run unit tests only"
        echo "  integration  - Run integration tests only"
        echo "  e2e          - Run end-to-end tests only"
        echo "  all          - Run all tests (default)"
        echo "  quick        - Run all tests except slow ones"
        exit 1
        ;;
esac

echo ""
echo "================================"
echo "Tests completed!"
echo "================================"
