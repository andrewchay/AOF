# AOF Semantic Middle Layer API
# Multi-stage build for optimized image size and security

# ==================== Stage 1: Builder ====================
FROM python:3.13-slim AS builder

# Build arguments
ARG INSTALL_COGNEE=1
ARG COGNEE_PIP_SPEC=cognee
ARG PIP_INDEX_URL=https://pypi.org/simple

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY services/semantic_middle_layer_api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# Install cognee if enabled
RUN if [ "$INSTALL_COGNEE" = "1" ]; then \
    pip install --no-cache-dir "$COGNEE_PIP_SPEC"; \
    fi

# ==================== Stage 2: Runtime ====================
FROM python:3.13-slim AS runtime

# Security: Create non-root user
RUN groupadd -r aof && useradd -r -g aof -s /bin/bash aof

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

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

# Switch to non-root user
USER aof

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8787/healthz || exit 1

# Expose port
EXPOSE 8787

# Run the application
CMD ["python", "-m", "uvicorn", "services.semantic_middle_layer_api.app:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1"]
