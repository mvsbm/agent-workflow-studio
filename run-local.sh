#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
APP_HOME="${AGENT_WORKFLOW_STUDIO_HOME:-$HOME/.local/share/agent-workflow-studio}"
COMFYUI_DIR="$APP_HOME/ComfyUI"
NAYOSE_DIR="$APP_HOME/Nayose"
STATE_DIR="$APP_HOME/state"
LOG_DIR="$APP_HOME/logs"
NAYOSE_PORT="${NAYOSE_DASH_PORT:-8000}"
COMFY_PORT="${COMFYUI_PORT:-8188}"

[[ -x "$COMFYUI_DIR/.venv/bin/python" && -x "$NAYOSE_DIR/.venv/bin/python" ]] || {
    printf 'Run install.sh first.\n' >&2
    exit 1
}

NAYOSE_TOKEN="$(PYTHONPATH="$PACKAGE_ROOT" "$COMFYUI_DIR/.venv/bin/python" - <<'PY'
from custom_nodes.agent_workflow_studio.credential_store import get_credential

value = get_credential("nayose-api")
if not value:
    raise SystemExit("Local Nayose API credential is missing; rerun install.sh")
print(value)
PY
)"

DEVICE="cpu"
DEVICE="$("$COMFYUI_DIR/.venv/bin/python" - <<'PY'
import torch
if torch.cuda.is_available():
    print("cuda")
elif torch.backends.mps.is_available():
    print("mps")
else:
    print("cpu")
PY
)"

mkdir -p "$STATE_DIR/data" "$STATE_DIR/models" "$STATE_DIR/comfy-output" "$STATE_DIR/comfy-input" "$STATE_DIR/comfy-temp" "$STATE_DIR/comfy-user" "$LOG_DIR"
export NAYOSE_DATA_ROOT="$STATE_DIR/data"
export NAYOSE_DATA_DIR="$STATE_DIR/data/current"
export NAYOSE_RAW_DIR="$NAYOSE_DIR/data/raw"
export NAYOSE_MODELS_DIR="$STATE_DIR/models"
export NAYOSE_DASH_PORT="$NAYOSE_PORT"
export NAYOSE_API_TOKEN="$NAYOSE_TOKEN"
export NAYOSE_DEVICE="$DEVICE"
export NAYOSE_EMBEDDING_BACKEND=pytorch
export NAYOSE_API_URL="http://127.0.0.1:$NAYOSE_PORT"
export PYTHONPATH="$NAYOSE_DIR/src"
unset AGENT_WORKFLOW_STUDIO_OPENCODE_URL

NAYOSE_PID=""
COMFY_PID=""
cleanup() {
    [[ -z "$COMFY_PID" ]] || kill "$COMFY_PID" 2>/dev/null || true
    [[ -z "$NAYOSE_PID" ]] || kill "$NAYOSE_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
    cd "$NAYOSE_DIR"
    exec .venv/bin/python -m api.main
) >>"$LOG_DIR/nayose.log" 2>&1 &
NAYOSE_PID=$!

(
    cd "$COMFYUI_DIR"
    exec .venv/bin/python main.py \
        --listen 127.0.0.1 \
        --port "$COMFY_PORT" \
        --user-directory "$STATE_DIR/comfy-user" \
        --output-directory "$STATE_DIR/comfy-output" \
        --input-directory "$STATE_DIR/comfy-input" \
        --temp-directory "$STATE_DIR/comfy-temp"
) >>"$LOG_DIR/comfyui.log" 2>&1 &
COMFY_PID=$!

for _ in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:$NAYOSE_PORT/api/health" >/dev/null \
        && curl -fsS "http://127.0.0.1:$COMFY_PORT/object_info" >/dev/null; then
        printf 'Agent Workflow Studio is ready: http://127.0.0.1:%s\n' "$COMFY_PORT"
        printf 'GPU/runtime device detected: %s\n' "$DEVICE"
        wait "$COMFY_PID"
        exit $?
    fi
    sleep 1
done
printf 'Startup timed out. Check logs in %s\n' "$LOG_DIR" >&2
exit 1
