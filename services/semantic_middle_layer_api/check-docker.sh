#!/bin/bash
# Docker Configuration Check Script
# Usage: ./check-docker.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================"
echo "AOF Docker Configuration Check"
echo "================================"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_pass() {
    echo -e "${GREEN}✓${NC} $1"
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
}

check_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check Docker installation
echo "1. Checking Docker installation..."
if command -v docker &> /dev/null; then
    check_pass "Docker installed: $(docker -v)"
else
    check_fail "Docker not installed"
    echo "   Install: https://docs.docker.com/get-docker/"
fi

# Check Docker Compose
echo ""
echo "2. Checking Docker Compose..."
if command -v docker-compose &> /dev/null; then
    check_pass "Docker Compose installed: $(docker-compose -v)"
else
    check_warn "Docker Compose not found (may be integrated in Docker)"
fi

# Check file existence
echo ""
echo "3. Checking configuration files..."
files=(
    "Dockerfile"
    "docker-compose.yml"
    "docker-compose.dev.yml"
    "docker-compose.prod.yml"
    "requirements.txt"
)

for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        check_pass "$file exists"
    else
        check_fail "$file missing"
    fi
done

# Check .env file
echo ""
echo "4. Checking environment configuration..."
if [ -f ".env" ]; then
    check_pass ".env file exists"
    
    # Check for required variables
    if grep -q "LLM_API_KEY" .env; then
        if grep "LLM_API_KEY" .env | grep -q "your-api-key"; then
            check_warn "LLM_API_KEY not configured (still has placeholder)"
        else
            check_pass "LLM_API_KEY configured"
        fi
    else
        check_fail "LLM_API_KEY not found in .env"
    fi
else
    check_warn ".env file not found (copy from .env.example)"
fi

# Validate Dockerfile syntax
echo ""
echo "5. Validating Dockerfile syntax..."
if [ -f "Dockerfile" ]; then
    # Basic syntax checks
    if grep -q "^FROM" Dockerfile; then
        check_pass "Dockerfile has FROM instruction"
    else
        check_fail "Dockerfile missing FROM instruction"
    fi
    
    if grep -q "CMD\|ENTRYPOINT" Dockerfile; then
        check_pass "Dockerfile has CMD/ENTRYPOINT"
    else
        check_warn "Dockerfile missing CMD/ENTRYPOINT"
    fi
    
    # Check for multi-stage build
    if grep -q "AS builder" Dockerfile; then
        check_pass "Multi-stage build detected"
    else
        check_warn "Not using multi-stage build"
    fi
    
    # Check for non-root user
    if grep -q "USER" Dockerfile; then
        check_pass "Non-root user configured"
    else
        check_warn "No USER instruction (runs as root)"
    fi
    
    # Check for health check
    if grep -q "HEALTHCHECK" Dockerfile; then
        check_pass "Health check configured"
    else
        check_warn "No HEALTHCHECK instruction"
    fi
else
    check_fail "Dockerfile not found"
fi

# Validate docker-compose
echo ""
echo "6. Validating docker-compose files..."
for compose_file in docker-compose.yml docker-compose.dev.yml docker-compose.prod.yml; do
    if [ -f "$compose_file" ]; then
        if grep -q "version:" "$compose_file"; then
            check_pass "$compose_file has version"
        fi
        
        if grep -q "services:" "$compose_file"; then
            check_pass "$compose_file has services section"
        fi
    fi
done

# Check if ports are available
echo ""
echo "7. Checking port availability..."
if command -v lsof &> /dev/null; then
    if lsof -Pi :8787 -sTCP:LISTEN -t >/dev/null 2>&1; then
        check_warn "Port 8787 is already in use"
    else
        check_pass "Port 8787 is available"
    fi
else
    check_warn "lsof not installed, skipping port check"
fi

# Image size estimation
echo ""
echo "8. Docker image info..."
if command -v docker &> /dev/null; then
    if docker image inspect python:3.13-slim &> /dev/null 2>&1; then
        SIZE=$(docker image inspect python:3.13-slim --format='{{.Size}}' 2>/dev/null | awk '{print int($1/1024/1024)}')
        echo "   Base image (python:3.13-slim): ${SIZE}MB"
    else
        echo "   Base image not pulled yet"
    fi
    
    if docker image inspect aof-semantic-api:latest &> /dev/null 2>&1; then
        SIZE=$(docker image inspect aof-semantic-api:latest --format='{{.Size}}' 2>/dev/null | awk '{print int($1/1024/1024)}')
        check_pass "AOF image built: ${SIZE}MB"
    else
        check_warn "AOF image not built yet"
    fi
else
    check_warn "Docker not available"
fi

# Summary
echo ""
echo "================================"
echo "Check Complete!"
echo "================================"
echo ""
echo "Next steps:"
echo "1. Ensure .env is configured with your LLM_API_KEY"
echo "2. Run: ./deploy.sh dev     # For development"
echo "3. Run: ./deploy.sh prod    # For production"
echo ""
