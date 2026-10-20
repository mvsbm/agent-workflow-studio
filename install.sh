#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=versions.lock
source "$PACKAGE_ROOT/versions.lock"
APP_HOME="${AGENT_WORKFLOW_STUDIO_HOME:-$HOME/.local/share/agent-workflow-studio}"
COMFYUI_DIR="$APP_HOME/ComfyUI"
NODE_SRC="$PACKAGE_ROOT/custom_nodes/agent_workflow_studio"
NODE_DIR="$COMFYUI_DIR/custom_nodes/agent_workflow_studio"
PYTHON="$COMFYUI_DIR/.venv/bin/python"

fail() { printf 'Agent Workflow Studio install failed: %s\n' "$*" >&2; exit 1; }

PLATFORM="$(uname -s)"
[[ "$PLATFORM" == Linux || "$PLATFORM" == Darwin ]] || fail "this installer supports Linux and macOS."
command -v git >/dev/null || fail "git is required."
command -v curl >/dev/null || fail "curl is required to bootstrap uv."
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null || fail "uv installation did not complete."

install_torch() {
    if [[ "$PLATFORM" == Darwin ]]; then
        # PyPI's macOS wheels include the Apple Silicon MPS backend.
        uv pip install --python "$PYTHON" --reinstall \
            "torch==$TORCH_VERSION" "torchvision==$TORCHVISION_VERSION"
    elif [[ -e /dev/nvidia0 ]]; then
        uv pip install --python "$PYTHON" --reinstall \
            --index-url https://download.pytorch.org/whl/cu130 \
            "torch==$TORCH_VERSION" "torchvision==$TORCHVISION_VERSION"
    else
        uv pip install --python "$PYTHON" --reinstall \
            --index-url https://download.pytorch.org/whl/cpu \
            "torch==$TORCH_VERSION" "torchvision==$TORCHVISION_VERSION"
    fi
}

mkdir -p "$APP_HOME/state" "$APP_HOME/logs"

# Pin ComfyUI to the reviewed commit; never reset a modified checkout.
if [[ -e "$COMFYUI_DIR" ]]; then
    git -C "$COMFYUI_DIR" rev-parse --git-dir >/dev/null 2>&1 || fail "$COMFYUI_DIR exists but is not a Git checkout."
    if [[ "$(git -C "$COMFYUI_DIR" rev-parse HEAD)" != "$COMFYUI_COMMIT" ]]; then
        [[ -z "$(git -C "$COMFYUI_DIR" status --porcelain)" ]] || fail "$COMFYUI_DIR is modified; refusing to reset it."
        git -C "$COMFYUI_DIR" fetch --depth 1 origin "$COMFYUI_COMMIT"
        git -C "$COMFYUI_DIR" checkout --detach "$COMFYUI_COMMIT"
    fi
else
    git clone --filter=blob:none "$COMFYUI_REPOSITORY" "$COMFYUI_DIR"
    git -C "$COMFYUI_DIR" fetch --depth 1 origin "$COMFYUI_COMMIT"
    git -C "$COMFYUI_DIR" checkout --detach "$COMFYUI_COMMIT"
fi

uv python install "$PYTHON_VERSION"
[[ -x "$PYTHON" ]] || uv venv --python "$PYTHON_VERSION" "$COMFYUI_DIR/.venv"
uv pip install --python "$PYTHON" -r "$COMFYUI_DIR/requirements.txt"
install_torch
# This official frontend release is verified against the pinned core; change no other dependency.
uv pip install --python "$PYTHON" --no-deps "comfyui-frontend-package==$COMFYUI_FRONTEND_VERSION"
FRONTEND_ROOT="$("$PYTHON" -c \
    'import pathlib, comfyui_frontend_package; print(pathlib.Path(comfyui_frontend_package.__file__).parent / "static")')"
"$PYTHON" "$PACKAGE_ROOT/scripts/provision_comfy_assets.py" \
    --user-root "$APP_HOME/state/comfy-user" --frontend-root "$FRONTEND_ROOT"
uv pip install --python "$PYTHON" -r "$NODE_SRC/requirements.txt"

if [[ ! -e "$NODE_DIR" ]]; then
    ln -s "$NODE_SRC" "$NODE_DIR"
elif [[ "$(cd "$NODE_DIR" && pwd -P)" != "$(cd "$NODE_SRC" && pwd -P)" ]]; then
    fail "$NODE_DIR already exists and is not this package's node directory."
fi

mkdir -p "$APP_HOME/state/comfy-user/default/workflows"
cp -n "$PACKAGE_ROOT"/workflows/*.json "$APP_HOME/state/comfy-user/default/workflows/" || true

printf '\nInstalled Agent Workflow Studio at: %s\n' "$APP_HOME"
# The agent runs on the Pi CLI. Without one on PATH, install the pinned version privately.
if ! command -v pi >/dev/null && [[ -z "${AGENT_WORKFLOW_STUDIO_PI_CLI:-}" ]]; then
    if command -v npm >/dev/null; then
        npm install --prefix "$APP_HOME/pi" --no-audit --no-fund --silent "@earendil-works/pi-coding-agent@$PI_VERSION" \
            && printf 'Installed the Pi CLI %s in %s/pi\n' "$PI_VERSION" "$APP_HOME"
    else
        printf 'Note: the Pi CLI is needed for the agent. Install Node.js (npm), then run install.sh again.\n'
    fi
fi
printf 'Start it with: %s/run-local.sh\n' "$PACKAGE_ROOT"
