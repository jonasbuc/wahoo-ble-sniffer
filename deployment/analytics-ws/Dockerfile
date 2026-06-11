# ── analytics-ws – Dockerfile ─────────────────────────────────────────────────
#
# Build context: repository root  (run from "Blu Sniffer/")
#   docker build -f deployment/analytics-ws/Dockerfile \
#                -t carvr/analytics-ws:latest .
#
# Services exposed:
#   8768  FastAPI WebSocket + HTTP  (/ws/dashboard, /healthz)
#
# This pod reads live_state rows from the shared SQLite database on the PVC
# and streams them to connected dashboard clients.  It is read-only with
# respect to the PVC — only analytics-ingest writes live_state rows.
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

# /data is shared read-only from PVC (analytics-ingest writes, analytics-ws reads)
RUN mkdir -p /data && chown appuser:appgroup /data

USER appuser

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LA_DB_PATH=/data/analytics.db \
    WS_DASHBOARD_HOST=0.0.0.0 \
    WS_DASHBOARD_PORT=8768 \
    POLL_INTERVAL=0.5

EXPOSE 8768

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8768/healthz')" \
    || exit 1

# Standalone FastAPI app — /healthz + /ws/dashboard
ENTRYPOINT ["python", "-m", "live_analytics.app.ws_dashboard_server"]
