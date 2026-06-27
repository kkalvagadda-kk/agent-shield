#!/usr/bin/env bash
set -euo pipefail

AGENT_NAME=""
PROBES="dan,encoding,knownbadsignatures"
OUTPUT="garak-report-$(date +%Y%m%d-%H%M%S).json"
PORT_FORWARD_PID=""

usage() {
  echo "Usage: bash scripts/run-garak.sh --agent <agent-name> [--probes <probe1,probe2>] [--output <path>]"
  exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      AGENT_NAME="$2"
      shift 2
      ;;
    --probes)
      PROBES="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      ;;
  esac
done

if [[ -z "${AGENT_NAME}" ]]; then
  echo "ERROR: --agent is required"
  usage
fi

# Check garak is installed
echo "[*] Checking garak installation..."
if ! python -m garak --version &>/dev/null; then
  echo ""
  echo "ERROR: garak is not installed or not reachable via 'python -m garak'."
  echo "Install it with: pip install garak"
  echo "See: https://docs.garak.ai/garak/installation"
  exit 1
fi
echo "    OK: garak found"

# Trap to clean up port-forward on exit
cleanup() {
  if [[ -n "${PORT_FORWARD_PID}" ]]; then
    echo ""
    echo "[*] Killing port-forward (PID ${PORT_FORWARD_PID})..."
    kill "${PORT_FORWARD_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# Get agent pod
echo ""
echo "[*] Looking up agent pod for '${AGENT_NAME}'..."
AGENT_POD=$(kubectl get pod -n agents-platform \
  -l "app.kubernetes.io/name=${AGENT_NAME}" \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [[ -z "${AGENT_POD}" ]]; then
  echo "ERROR: No pod found in namespace agents-platform with label app.kubernetes.io/name=${AGENT_NAME}"
  exit 1
fi
echo "    Found pod: ${AGENT_POD}"

# Port-forward to localhost:18080
echo ""
echo "[*] Starting port-forward to ${AGENT_POD}:8000 -> localhost:18080..."
kubectl port-forward -n agents-platform "${AGENT_POD}" 18080:8000 &>/dev/null &
PORT_FORWARD_PID=$!

# Wait up to 10s for port-forward to be ready
READY=false
for i in $(seq 1 10); do
  if python3 -c "
import socket
s = socket.socket()
s.settimeout(1)
try:
    s.connect(('localhost', 18080))
    s.close()
    exit(0)
except:
    exit(1)
" 2>/dev/null; then
    READY=true
    break
  fi
  sleep 1
done

if [[ "${READY}" != "true" ]]; then
  echo "ERROR: Port-forward to localhost:18080 did not become ready within 10 seconds"
  exit 1
fi
echo "    OK: Port-forward ready on localhost:18080"

# Run garak
echo ""
echo "[*] Running garak probes: ${PROBES}"
echo "    Agent: agentshield-${AGENT_NAME}"
echo "    Output prefix: ${OUTPUT}"
echo ""

GARAK_EXIT=0
GARAK_REST_ENDPOINT="http://localhost:18080/chat" \
GARAK_REST_JSON_RESPONSE_FIELD="response" \
python -m garak \
  --model_type rest \
  --model_name "agentshield-${AGENT_NAME}" \
  --probes "${PROBES}" \
  --report_prefix "${OUTPUT}" || GARAK_EXIT=$?

# Check for critical findings in output file
CRITICAL_FOUND=false
OUTPUT_FILE="${OUTPUT}.report.jsonl"
if [[ -f "${OUTPUT_FILE}" ]]; then
  if grep -q '"severity":\s*"critical"' "${OUTPUT_FILE}" 2>/dev/null || \
     grep -qi '"status":\s*"fail"' "${OUTPUT_FILE}" 2>/dev/null; then
    CRITICAL_FOUND=true
  fi
fi

echo ""
if [[ "${GARAK_EXIT}" -ne 0 ]] || [[ "${CRITICAL_FOUND}" == "true" ]]; then
  echo "[FAIL] garak run completed with issues (exit=${GARAK_EXIT}, critical_findings=${CRITICAL_FOUND})"
  echo "       Report: ${OUTPUT}"
  exit 1
else
  echo "[PASS] garak run completed — no critical findings"
  echo "       Report: ${OUTPUT}"
fi
