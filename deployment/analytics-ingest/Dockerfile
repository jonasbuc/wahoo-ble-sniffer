# ── analytics-ingest – Dockerfile ────────────────────────────────────────────
#
# Build context: repository root  (run from "Blu Sniffer/")
#   docker build -f deployment/analytics-ingest/Dockerfile \
#                -t carvr/analytics-ingest:latest .
#
# Services exposed:
#   8766  Unity → analytics WebSocket ingest (receives telemetry batches)
#   8767  HTTP /healthz endpoint (Kubernetes liveness / readiness probe)
#
# This pod writes scored telemetry + live_state to the shared SQLite database
# on the PVC at /data.  analytics-api and analytics-ws pods read from the
# same PVC.  All three pods must mount the same PersistentVolumeClaim.
# ---------------------------------------------------------------------------

# ── Stage 1: dependency installation ────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md ./

RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN groupadd --system --gid 1000 appgroup \
    && useradd --system --uid 1000 --gid appgroup --no-create-home appuser

WORKDIR /app

COPY --from=deps /venv /venv

COPY --chown=appuser:appgroup bridge/ ./bridge/
COPY --chown=appuser:appgroup live_analytics/ ./live_analytics/
COPY --chown=appuser:appgroup pyproject.toml README.md ./

RUN /venv/bin/pip install --no-cache-dir --no-deps .

RUN mkdir -p /data && chown appuser:appgroup /data

USER appuser

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LA_DATA_DIR=/data \
    LA_DB_PATH=/data/analytics.db \
    LA_SESSIONS_DIR=/data/sessions \
    LA_PARTICIPANTS_DIR=/data/participants \
    LA_PULSE_LOG_DIR=/data/pulse \
    LA_WS_INGEST_HOST=0.0.0.0 \
    LA_WS_INGEST_PORT=8766 \
    # Port for the HTTP healthz endpoint (separate from the WebSocket port)
    INGEST_HEALTHZ_PORT=8767

# WebSocket ingest port
EXPOSE 8766
# Healthz HTTP port
EXPOSE 8767

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8767/healthz')" \
    || exit 1

# Run ws_ingest as a standalone pod (starts WS server + healthz HTTP server)
ENTRYPOINT ["python", "-m", "live_analytics.app.ws_ingest"]
