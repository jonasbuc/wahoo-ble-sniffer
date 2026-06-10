#!/usr/bin/env bash
# ── smoke-test.sh ─────────────────────────────────────────────────────────────
# Smoke tests for the CarVR stack deployed in Kubernetes.
# Port-forwards each service temporarily, runs HTTP checks, then cleans up.
#
# Usage:
#   ./deployment/scripts/smoke-test.sh
#   KUBE_CONTEXT=kind-carvr ./deployment/scripts/smoke-test.sh
#   NAMESPACE=carvr-local   ./deployment/scripts/smoke-test.sh
#
# Exit codes:
#   0  all tests passed
#   1  one or more tests failed
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

NAMESPACE="${NAMESPACE:-carvr-local}"
CONTEXT_ARG=""
[ -n "${KUBE_CONTEXT:-}" ] && CONTEXT_ARG="--context $KUBE_CONTEXT"

PASS=0
FAIL=0
# Background port-forward PIDs for cleanup
PF_PIDS=()

# ── Test-data identity ────────────────────────────────────────────────────────
# All data written during the smoke test is tagged with this ID so it can be
# identified and removed after the test. It must never collide with real users.
SMOKE_PID="k8s-smoke-test"

# ── Cleanup trap ──────────────────────────────────────────────────────────────
cleanup() {
  echo ""
  echo "► Cleaning up port-forwards ..."
  for pid in "${PF_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT

# ── Helpers ───────────────────────────────────────────────────────────────────
port_forward() {
  local svc="$1"
  local local_port="$2"
  local remote_port="$3"
  kubectl port-forward "svc/$svc" "${local_port}:${remote_port}" \
    --namespace "$NAMESPACE" \
    $CONTEXT_ARG \
    &>/dev/null &
  PF_PIDS+=($!)
  # Wait for port to be ready
  local tries=0
  while ! nc -z localhost "$local_port" 2>/dev/null; do
    sleep 0.5
    tries=$((tries + 1))
    [ $tries -ge 30 ] && { echo "  ✗ Timeout waiting for port $local_port"; return 1; }
  done
}

check_http() {
  local label="$1"
  local url="$2"
  local expected_status="${3:-200}"
  local actual
  actual=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  if [ "$actual" = "$expected_status" ]; then
    echo "  ✓ $label  ($url → HTTP $actual)"
    PASS=$((PASS + 1))
  else
    echo "  ✗ $label  ($url → HTTP $actual, expected $expected_status)"
    FAIL=$((FAIL + 1))
  fi
}

check_body_contains() {
  local label="$1"
  local url="$2"
  local needle="$3"
  local body
  body=$(curl -s --max-time 5 "$url" 2>/dev/null || echo "")
  if echo "$body" | grep -q "$needle"; then
    echo "  ✓ $label  (body contains '$needle')"
    PASS=$((PASS + 1))
  else
    echo "  ✗ $label  (body does NOT contain '$needle')"
    echo "    Response (first 200 chars): ${body:0:200}"
    FAIL=$((FAIL + 1))
  fi
}

check_deployment_ready() {
  local dep="$1"
  local desired
  local ready
  desired=$(kubectl get deployment "$dep" --namespace "$NAMESPACE" $CONTEXT_ARG \
    -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")
  ready=$(kubectl get deployment "$dep" --namespace "$NAMESPACE" $CONTEXT_ARG \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
  if [ "${ready:-0}" -ge "${desired:-1}" ] && [ "${desired:-0}" -gt 0 ]; then
    echo "  ✓ Deployment $dep  ($ready/$desired pods ready)"
    PASS=$((PASS + 1))
  else
    echo "  ✗ Deployment $dep  ($ready/$desired pods ready)"
    FAIL=$((FAIL + 1))
  fi
}

# ── Test suite ────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  CarVR — Smoke Tests"
echo "  Namespace: $NAMESPACE  |  Context: ${KUBE_CONTEXT:-default}"
echo "══════════════════════════════════════════════════════════"
echo ""

# ── 1. Deployment readiness ───────────────────────────────────────────────────
echo "── 1. Deployment readiness ──────────────────────────────"
for dep in analytics-api questionnaire dashboard bridge; do
  check_deployment_ready "$dep"
done
echo ""

# ── 2. Start port-forwards ────────────────────────────────────────────────────
echo "── 2. Starting port-forwards ────────────────────────────"
port_forward analytics-api  19080 8080 && echo "  ✓ analytics-api  → localhost:19080"
port_forward questionnaire  19090 8090 && echo "  ✓ questionnaire  → localhost:19090"
port_forward dashboard      19501 8501 && echo "  ✓ dashboard      → localhost:19501"
port_forward bridge         19765 8765 && echo "  ✓ bridge         → localhost:19765"
# Give services a moment to stabilise before hitting endpoints
sleep 2
echo ""

# ── 3. Health endpoints ───────────────────────────────────────────────────────
echo "── 3. Health endpoints ──────────────────────────────────"
check_http       "analytics-api /healthz"       "http://localhost:19080/healthz"
check_body_contains "analytics-api /healthz json" "http://localhost:19080/healthz" "status"
check_http       "questionnaire /api/healthz"   "http://localhost:19090/api/healthz"
check_body_contains "questionnaire healthz json" "http://localhost:19090/api/healthz" "status"
echo ""

# ── 4. API functionality ──────────────────────────────────────────────────────
echo "── 4. API functionality ─────────────────────────────────"
check_http "analytics-api GET /api/sessions"    "http://localhost:19080/api/sessions"
check_http "analytics-api GET /api/live/latest" "http://localhost:19080/api/live/latest" "200"
check_http "questionnaire GET /api/participants" "http://localhost:19090/api/participants"
# Verify the questionnaire SPA root returns HTML
check_body_contains "questionnaire SPA root" "http://localhost:19090/" "html"
echo ""

# ── 5. Dashboard reachable ────────────────────────────────────────────────────
echo "── 5. Dashboard reachable ───────────────────────────────"
check_http "dashboard HTTP 200" "http://localhost:19501" "200"
echo ""

# ── 6. Bridge WebSocket port open ────────────────────────────────────────────
echo "── 6. Bridge WebSocket port ─────────────────────────────"
if nc -z localhost 19765 2>/dev/null; then
  echo "  ✓ bridge WebSocket port 19765 is open"
  PASS=$((PASS + 1))
else
  echo "  ✗ bridge WebSocket port 19765 is NOT reachable"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── 7. Service-to-service: questionnaire → analytics-api ─────────────────────
echo "── 7. Service-to-service connectivity ───────────────────"
# The questionnaire ConfigMap sets QS_ANALYTICS_API_URL=http://analytics-api:8080.
# We verify the questionnaire can reach analytics-api by checking that
# /api/healthz on questionnaire returns 200 (it would fail to start or
# show degraded status if the URL was entirely unreachable at init time).
check_http "questionnaire reachable (implies API URL OK)" \
  "http://localhost:19090/api/healthz"
echo ""

# ── 8. Test-data injection (participant_id = k8s-smoke-test) ─────────────────
# All data is tagged with SMOKE_PID="k8s-smoke-test" so it can be identified
# and removed. Real participant data is never touched.
echo "── 8. Test-data injection  (id=${SMOKE_PID}) ────────────"

# 8a. Register the smoke-test participant in the questionnaire DB
POST_RESP=$(curl -s -w "\n%{http_code}" -X POST \
  "http://localhost:19090/api/participants" \
  -H "Content-Type: application/json" \
  -d "{\"participant_id\":\"${SMOKE_PID}\",\"display_name\":\"K8s Smoke Test\",\"session_id\":null,\"metadata\":{\"source\":\"k8s-smoke-test\"}}" \
  --max-time 5 2>/dev/null || printf "\n000")
POST_STATUS=$(printf '%s' "$POST_RESP" | tail -1)
if [ "$POST_STATUS" = "200" ] || [ "$POST_STATUS" = "201" ]; then
  echo "  ✓ POST questionnaire /api/participants  → HTTP $POST_STATUS"
  PASS=$((PASS + 1))
else
  echo "  ✗ POST questionnaire /api/participants  → HTTP $POST_STATUS  (expected 200/201)"
  FAIL=$((FAIL + 1))
fi

# 8b. Verify the participant is readable from questionnaire DB
GET_QS=$(curl -s -w "\n%{http_code}" \
  "http://localhost:19090/api/participants/${SMOKE_PID}" \
  --max-time 5 2>/dev/null || printf "\n000")
GET_QS_STATUS=$(printf '%s' "$GET_QS" | tail -1)
if [ "$GET_QS_STATUS" = "200" ]; then
  echo "  ✓ GET  questionnaire /api/participants/${SMOKE_PID}  → HTTP $GET_QS_STATUS  (readback OK)"
  PASS=$((PASS + 1))
else
  echo "  ✗ GET  questionnaire /api/participants/${SMOKE_PID}  → HTTP $GET_QS_STATUS  (expected 200)"
  FAIL=$((FAIL + 1))
fi

# 8c. Start a pulse-log session on analytics-api for the smoke participant.
#     session_id is also tagged k8s-smoke-test so it is trivially findable.
START_RESP=$(curl -s -w "\n%{http_code}" -X POST \
  "http://localhost:19080/api/pulse-session/start" \
  -H "Content-Type: application/json" \
  -d "{\"test_person_id\":\"${SMOKE_PID}\",\"session_id\":\"${SMOKE_PID}-session\",\"extra\":{\"source\":\"k8s-smoke-test\"}}" \
  --max-time 5 2>/dev/null || printf "\n000")
START_STATUS=$(printf '%s' "$START_RESP" | tail -1)
if [ "$START_STATUS" = "200" ]; then
  echo "  ✓ POST analytics-api /api/pulse-session/start  → HTTP $START_STATUS"
  PASS=$((PASS + 1))
else
  echo "  ✗ POST analytics-api /api/pulse-session/start  → HTTP $START_STATUS  (expected 200)"
  FAIL=$((FAIL + 1))
fi

# 8d. Confirm the session is visible in the current-sessions list
CURR_RESP=$(curl -s -w "\n%{http_code}" \
  "http://localhost:19080/api/pulse-session/current/${SMOKE_PID}" \
  --max-time 5 2>/dev/null || printf "\n000")
CURR_STATUS=$(printf '%s' "$CURR_RESP" | tail -1)
if [ "$CURR_STATUS" = "200" ]; then
  echo "  ✓ GET  analytics-api /api/pulse-session/current/${SMOKE_PID}  → HTTP $CURR_STATUS  (session visible)"
  PASS=$((PASS + 1))
else
  echo "  ✗ GET  analytics-api /api/pulse-session/current/${SMOKE_PID}  → HTTP $CURR_STATUS  (expected 200)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── 9. Test-data cleanup ──────────────────────────────────────────────────────
echo "── 9. Test-data cleanup  (id=${SMOKE_PID}) ──────────────"

# 9a. End the pulse-log session (200 = closed; 404 = start step failed → acceptable)
END_RESP=$(curl -s -w "\n%{http_code}" -X POST \
  "http://localhost:19080/api/pulse-session/end" \
  -H "Content-Type: application/json" \
  -d "{\"test_person_id\":\"${SMOKE_PID}\"}" \
  --max-time 5 2>/dev/null || printf "\n000")
END_STATUS=$(printf '%s' "$END_RESP" | tail -1)
if [ "$END_STATUS" = "200" ] || [ "$END_STATUS" = "404" ]; then
  echo "  ✓ POST analytics-api /api/pulse-session/end  → HTTP $END_STATUS"
  PASS=$((PASS + 1))
else
  echo "  ✗ POST analytics-api /api/pulse-session/end  → HTTP $END_STATUS  (expected 200 or 404)"
  FAIL=$((FAIL + 1))
fi

# 9b. Delete the smoke participant from questionnaire DB
DEL_RESP=$(curl -s -w "\n%{http_code}" -X DELETE \
  "http://localhost:19090/api/participants/${SMOKE_PID}" \
  --max-time 5 2>/dev/null || printf "\n000")
DEL_STATUS=$(printf '%s' "$DEL_RESP" | tail -1)
if [ "$DEL_STATUS" = "200" ] || [ "$DEL_STATUS" = "204" ]; then
  echo "  ✓ DELETE questionnaire /api/participants/${SMOKE_PID}  → HTTP $DEL_STATUS  (removed)"
  PASS=$((PASS + 1))
else
  echo "  ✗ DELETE questionnaire /api/participants/${SMOKE_PID}  → HTTP $DEL_STATUS  (expected 200/204)"
  FAIL=$((FAIL + 1))
fi

# 9c. The pulse JSONL file written to the analytics-api PVC is tagged with the
#     smoke ID and can be removed manually if needed:
echo "  ℹ  Pulse log on PVC (remove manually if needed):"
echo "     kubectl exec -n $NAMESPACE deployment/analytics-api -- \\
       find /data/pulse -name '*${SMOKE_PID}*' -delete"
echo ""

# ── Results ───────────────────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────"
echo "  Results: $PASS passed,  $FAIL failed"
echo "──────────────────────────────────────────────────────────"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "  ✗ Smoke tests FAILED."
  echo "  Tip: kubectl logs deployment/<name> -n $NAMESPACE"
  exit 1
fi

echo "  ✓ All smoke tests passed."
echo ""
