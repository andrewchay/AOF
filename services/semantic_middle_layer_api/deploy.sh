#!/bin/bash
# AOF Docker Deployment Script
# Usage: ./deploy.sh [dev|prod|build|stop|logs]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default environment
ENV=${1:-dev}

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_env() {
    if [ ! -f .env ]; then
        if [ -f .env.example ]; then
            log_warn ".env file not found, copying from .env.example"
            cp .env.example .env
            log_warn "Please edit .env and fill in your API keys"
        else
            log_error ".env file not found and no .env.example available"
            exit 1
        fi
    fi
    
    # Check if LLM_API_KEY is set
    if ! grep -q "LLM_API_KEY=your-api-key" .env && ! grep -q "LLM_API_KEY=$" .env; then
        log_info "LLM_API_KEY is configured"
    else
        log_warn "LLM_API_KEY not configured, some features may not work"
    fi
}

deploy_dev() {
    log_info "Deploying in DEVELOPMENT mode..."
    check_env
    docker-compose -f docker-compose.dev.yml up -d
    log_info "Development server running at http://localhost:8787"
    log_info "API docs at http://localhost:8787/docs"
}

deploy_prod() {
    log_info "Deploying in PRODUCTION mode..."
    check_env
    
    # Pull latest images
    docker-compose -f docker-compose.prod.yml pull
    
    # Build and start
    docker-compose -f docker-compose.prod.yml up -d --build
    
    log_info "Production server starting..."
    sleep 5
    
    # Health check
    if curl -f http://localhost:8787/healthz > /dev/null 2>&1; then
        log_info "✅ Server is healthy"
    else
        log_error "❌ Health check failed, check logs with: ./deploy.sh logs"
    fi
}

deploy_default() {
    log_info "Deploying with default configuration..."
    check_env
    docker-compose up -d --build
    log_info "Server running at http://localhost:8787"
}

build_only() {
    log_info "Building Docker image..."
    docker-compose build
    log_info "✅ Build completed"
}

stop_services() {
    log_info "Stopping all services..."
    docker-compose down
    docker-compose -f docker-compose.dev.yml down
    docker-compose -f docker-compose.prod.yml down
    log_info "✅ All services stopped"
}

show_logs() {
    log_info "Showing logs..."
    docker-compose logs -f
}

clean_up() {
    log_warn "Cleaning up Docker resources..."
    docker-compose down -v
    docker system prune -f
    log_info "✅ Cleanup completed"
}

# Main command handler
case "$ENV" in
    dev)
        deploy_dev
        ;;
    prod)
        deploy_prod
        ;;
    build)
        build_only
        ;;
    stop)
        stop_services
        ;;
    logs)
        show_logs
        ;;
    clean)
        clean_up
        ;;
    *)
        echo "Usage: $0 [dev|prod|build|stop|logs|clean]"
        echo ""
        echo "Commands:"
        echo "  dev    - Deploy in development mode (with hot reload)"
        echo "  prod   - Deploy in production mode"
        echo "  build  - Build Docker image only"
        echo "  stop   - Stop all services"
        echo "  logs   - Show service logs"
        echo "  clean  - Clean up Docker resources"
        echo ""
        echo "Examples:"
        echo "  $0 dev     # Start development server"
        echo "  $0 prod    # Deploy to production"
        echo "  $0 logs    # View logs"
        exit 1
        ;;
esac
