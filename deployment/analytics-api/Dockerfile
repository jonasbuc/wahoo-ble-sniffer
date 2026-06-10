# ── Analytics API – Dockerfile ───────────────────────────────────────────────
#
# Build context: repository root  (run from "Blu Sniffer/")
#   docker build -f deployment/analytics-api/Dockerfile -t carvr/analytics-api:latest .
#
# Services exposed:
#   8080  FastAPI HTTP + REST API
#   8766  WebSocket ingest (Unity telemetry)
#
# Runtime data is expected on a mounted volume at /data.
# Override paths via environment variables (see deployment/README.md).
# ---------------------------------------------------------------------------

# ── Stage 1: dependency installation ────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /build

# Install build tools needed for some native extensions (e.g. numpy wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests first so this layer is cached when only source changes.
COPY requirements.txt pyproject.toml README.md ./

# Install all runtime dependencies into a virtual environment.
RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for security
RUN groupadd --system --gid 1000 appgroup \
    && useradd --system --uid 1000 --gid appgroup --no-create-home appuser

WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=deps /venv /venv

# Copy application source code
COPY --chown=appuser:appgroup bridge/ ./bridge/
COPY --chown=appuser:appgroup live_analytics/ ./live_analytics/
COPY --chown=appuser:appgroup pyproject.toml README.md ./

# Install the package itself (no external deps; already installed in venv)
RUN /venv/bin/pip install --no-cache-dir --no-deps .

# Create data directory (will be replaced by mounted volume at runtime)
RUN mkdir -p /data && chown appuser:appgroup /data

# Switch to non-root user
USER appuser

# Make the venv the default Python
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Default data paths — override via env vars in K8s ConfigMap/Secret
    LA_DATA_DIR=/data \
    LA_DB_PATH=/data/analytics.db \
    LA_SESSIONS_DIR=/data/sessions \
    LA_PARTICIPANTS_DIR=/data/participants \
    LA_PULSE_LOG_DIR=/data/pulse \
    LA_HTTP_HOST=0.0.0.0 \
    LA_HTTP_PORT=8080 \
    LA_WS_INGEST_HOST=0.0.0.0 \
    LA_WS_INGEST_PORT=8766

# Expose both ports used by this service
EXPOSE 8080
EXPOSE 8766

# Health check against the /healthz endpoint
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" \
    || exit 1

ENTRYPOINT ["python", "-m", "live_analytics.app.main"]
