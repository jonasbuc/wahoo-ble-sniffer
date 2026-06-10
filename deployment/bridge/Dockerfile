# ── Wahoo BLE Bridge – Dockerfile ────────────────────────────────────────────
#
# Build context: repository root  (run from "Blu Sniffer/")
#   docker build -f deployment/bridge/Dockerfile -t carvr/bridge:latest .
#
# Service exposed:
#   8765  WebSocket server (binary HR frames)
#
# IMPORTANT: In Kubernetes this service runs in MOCK MODE by default.
# Mock mode generates simulated HR data — no Bluetooth hardware is required.
# To enable mock mode explicitly, set: BRIDGE_MOCK=1 (handled in CMD below).
#
# Real BLE mode requires host Bluetooth and is NOT supported in containers.
# ---------------------------------------------------------------------------

# ── Stage 1: dependency installation ────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /build

# bleak may need dbus headers on Linux for installation even in mock mode.
# Installing python3-dbus avoids import errors at load time.
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

USER appuser

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Default: run in mock mode.  Set BRIDGE_MOCK=0 (with host Bluetooth) for live mode.
    BRIDGE_MOCK=1 \
    BRIDGE_HOST=0.0.0.0 \
    BRIDGE_PORT=8765

EXPOSE 8765

# TCP health check — bridge has no HTTP endpoint.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('localhost',8765),timeout=3); s.close()" \
    || exit 1

# Entrypoint script resolves BRIDGE_MOCK env var to --mock / --live flag.
# We use sh -c so the env var is evaluated at runtime, not at image build time.
CMD ["sh", "-c", \
     "exec python bridge/bike_bridge.py \
      --host \"${BRIDGE_HOST}\" \
      --port \"${BRIDGE_PORT}\" \
      $([ \"${BRIDGE_MOCK}\" = '0' ] && echo '--live' || echo '--mock')"]
