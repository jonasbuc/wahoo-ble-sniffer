# ── Streamlit Dashboard – Dockerfile ─────────────────────────────────────────
#
# Build context: repository root  (run from "Blu Sniffer/")
#   docker build -f deployment/dashboard/Dockerfile -t carvr/dashboard:latest .
#
# Service exposed:
#   8501  Streamlit HTTP server
#
# LA_API_BASE must point to the analytics-api service reachable from
# the Streamlit *server process* (not the user's browser).
# In Kubernetes: LA_API_BASE=http://analytics-api:8080
#
# Note: Streamlit makes all API calls server-side via Python `requests`.
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

# Streamlit writes its own config/cache; needs a writable home dir
RUN mkdir -p /home/appuser/.streamlit \
    && chown -R appuser:appgroup /home/appuser

USER appuser

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/appuser \
    # Analytics API URL — must be reachable from the Streamlit server process.
    # In K8s: set to http://analytics-api:8080 via ConfigMap.
    LA_API_BASE=http://analytics-api:8080 \
    LA_DASH_REFRESH_SEC=5

EXPOSE 8501

# Streamlit has no JSON health endpoint; use a simple TCP-level check.
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('localhost',8501),timeout=3); s.close()" \
    || exit 1

ENTRYPOINT ["python", "-m", "streamlit", "run", \
    "live_analytics/dashboard/streamlit_app.py", \
    "--server.port", "8501", \
    "--server.address", "0.0.0.0", \
    "--server.headless", "true", \
    "--browser.gatherUsageStats", "false"]
