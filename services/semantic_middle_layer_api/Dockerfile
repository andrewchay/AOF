# AOF Semantic Middle Layer API
# Multi-stage build for optimized image size and security

# ==================== Stage 1: Builder ====================
FROM python:3.13-slim AS builder

# Build arguments
ARG INSTALL_COGNEE=1
ARG COGNEE_PIP_SPEC=cognee
ARG PIP_INDEX_URL=https://pypi.org/simple

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements/core.in /tmp/core-requirements.txt
COPY services/semantic_middle_layer_api/requirements.txt /tmp/api-requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --retries 8 --timeout 60 \
      --index-url "$PIP_INDEX_URL" \
      -r /tmp/core-requirements.txt \
      -r /tmp/api-requirements.txt

# Install cognee if enabled
RUN if [ "$INSTALL_COGNEE" = "1" ]; then \
    pip install --no-cache-dir --retries 8 --timeout 60 \
      --index-url "$PIP_INDEX_URL" "$COGNEE_PIP_SPEC"; \
    fi

# ==================== Stage 1b: Browser production build ====================
FROM node:22-alpine AS web-builder
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate && \
    pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm run build

# ==================== Stage 2: Runtime ====================
FROM python:3.13-slim AS runtime

# Security: Create non-root user
RUN groupadd -r aof && useradd -r -g aof -s /bin/bash aof

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    AOF_ROOT=/app/AOF \
    APP_HOME=/app/AOF

# Create app directory
WORKDIR $APP_HOME

# Copy application code
COPY --chown=aof:aof . $APP_HOME
COPY --from=web-builder --chown=aof:aof /web/dist $APP_HOME/web/dist
RUN mkdir -p "$APP_HOME/data" "$APP_HOME/logs" "$APP_HOME/ontologies" && \
    chown -R aof:aof "$APP_HOME/data" "$APP_HOME/logs" "$APP_HOME/ontologies"

# Switch to non-root user
USER aof

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8787/healthz', timeout=5).read()"]

# Expose port
EXPOSE 8787

# Run the application
CMD ["sh", "-c", "exec python -m uvicorn services.semantic_middle_layer_api.app:app --host 0.0.0.0 --port 8787 --workers ${UVICORN_WORKERS:-1}"]
