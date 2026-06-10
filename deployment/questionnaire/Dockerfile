# ── Questionnaire API – Dockerfile ───────────────────────────────────────────
#
# Build context: repository root  (run from "Blu Sniffer/")
#   docker build -f deployment/questionnaire/Dockerfile -t carvr/questionnaire:latest .
#
# Service exposed:
#   8090  FastAPI HTTP + REST API + static SPA frontend
#
# Runtime data is expected on a mounted volume at /data/questionnaire.
# Override paths via environment variables (see deployment/README.md).
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

# Create data directories (replaced by mounted volumes at runtime)
RUN mkdir -p /data/questionnaire /data/participants \
    && chown -R appuser:appgroup /data

USER appuser

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    QS_HOST=0.0.0.0 \
    QS_PORT=8090 \
    QS_DATA_DIR=/data/questionnaire \
    QS_DB_PATH=/data/questionnaire/questionnaire.db \
    LA_PARTICIPANTS_DIR=/data/participants \
    # QS_ANALYTICS_API_URL must be set to the K8s service name at deploy time.
    # Default uses localhost for local-dev fallback only.
    QS_ANALYTICS_API_URL=http://analytics-api:8080

EXPOSE 8090

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/api/healthz')" \
    || exit 1

ENTRYPOINT ["python", "-m", "live_analytics.questionnaire.app"]
