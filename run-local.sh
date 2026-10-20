#!/usr/bin/env bash
set -Eeuo pipefail

APP_HOME="${AGENT_WORKFLOW_STUDIO_HOME:-$HOME/.local/share/agent-workflow-studio}"
COMFYUI_DIR="$APP_HOME/ComfyUI"
STATE_DIR="$APP_HOME/state"
LOG_DIR="$APP_HOME/logs"
COMFY_PORT="${COMFYUI_PORT:-8188}"

[[ -x "$COMFYUI_DIR/.venv/bin/python" ]] || {
    printf 'Run install.sh first.\n' >&2
    exit 1
}

mkdir -p "$STATE_DIR"/{comfy-output,comfy-input,comfy-temp,comfy-user} "$LOG_DIR"

# The Pi CLI the installer placed privately, when there is none on PATH.
if ! command -v pi >/dev/null && [[ -z "${AGENT_WORKFLOW_STUDIO_PI_CLI:-}" && -x "$APP_HOME/pi/node_modules/.bin/pi" ]]; then
    export AGENT_WORKFLOW_STUDIO_PI_CLI="$APP_HOME/pi/node_modules/.bin/pi"
fi

cd "$COMFYUI_DIR"
# --disable-api-nodes: no paid cloud nodes, and the frontend makes no internet calls (customer data stays
# on this machine). The tests run the same way (tests/e2e/fixture.py).
.venv/bin/python main.py \
    --listen 127.0.0.1 \
    --port "$COMFY_PORT" \
    --user-directory "$STATE_DIR/comfy-user" \
    --output-directory "$STATE_DIR/comfy-output" \
    --input-directory "$STATE_DIR/comfy-input" \
    --temp-directory "$STATE_DIR/comfy-temp" \
    --disable-api-nodes \
    >>"$LOG_DIR/comfyui.log" 2>&1 &
COMFY_PID=$!
trap 'kill "$COMFY_PID" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:$COMFY_PORT/object_info" >/dev/null 2>&1; then
        printf 'Agent Workflow Studio is ready: http://127.0.0.1:%s\n' "$COMFY_PORT"
        wait "$COMFY_PID"
        exit $?
    fi
    kill -0 "$COMFY_PID" 2>/dev/null || break
    sleep 1
done
printf 'Startup failed or timed out. Check %s/comfyui.log\n' "$LOG_DIR" >&2
exit 1
