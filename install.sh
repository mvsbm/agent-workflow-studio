#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=versions.lock
source "$PACKAGE_ROOT/versions.lock"
APP_HOME="${AGENT_WORKFLOW_STUDIO_HOME:-$HOME/.local/share/agent-workflow-studio}"
COMFYUI_DIR="$APP_HOME/ComfyUI"
NAYOSE_DIR="$APP_HOME/Nayose"
NODE_DIR="$COMFYUI_DIR/custom_nodes/agent_workflow_studio"

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

clone_pinned() {
    local url="$1" destination="$2" commit="$3"
    if [[ -e "$destination" ]]; then
        git -C "$destination" rev-parse --git-dir >/dev/null 2>&1 || fail "$destination exists but is not a Git checkout."
        local current
        current="$(git -C "$destination" rev-parse HEAD)"
        if [[ "$current" != "$commit" ]]; then
            [[ -z "$(git -C "$destination" status --porcelain)" ]] || fail "$destination is modified; refusing to reset it."
            git -C "$destination" fetch --depth 1 origin "$commit"
            git -C "$destination" checkout --detach "$commit"
        fi
    else
        mkdir -p "$(dirname "$destination")"
        git clone --filter=blob:none "$url" "$destination"
        git -C "$destination" fetch --depth 1 origin "$commit"
        git -C "$destination" checkout --detach "$commit"
    fi
}

install_torch() {
    local python="$1"
    if [[ "$PLATFORM" == Darwin ]]; then
        # PyPI's macOS wheels include the Apple Silicon MPS backend.
        uv pip install --python "$python" --reinstall \
            "torch==$TORCH_VERSION" "torchvision==$TORCHVISION_VERSION"
    elif [[ -e /dev/nvidia0 ]]; then
        uv pip install --python "$python" --reinstall \
            --index-url https://download.pytorch.org/whl/cu130 \
            "torch==$TORCH_VERSION" "torchvision==$TORCHVISION_VERSION"
    else
        uv pip install --python "$python" --reinstall \
            --index-url https://download.pytorch.org/whl/cpu \
            "torch==$TORCH_VERSION" "torchvision==$TORCHVISION_VERSION"
    fi
}

mkdir -p "$APP_HOME" "$APP_HOME/state" "$APP_HOME/logs"
clone_pinned "$COMFYUI_REPOSITORY" "$COMFYUI_DIR" "$COMFYUI_COMMIT"
clone_pinned "$NAYOSE_REPOSITORY" "$NAYOSE_DIR" "$NAYOSE_COMMIT"

# Apply only the Studio adapter patch, and only to the pinned Nayose revision.
if git -C "$NAYOSE_DIR" apply --check "$PACKAGE_ROOT/patches/nayose-agent-workflow.patch"; then
    git -C "$NAYOSE_DIR" apply "$PACKAGE_ROOT/patches/nayose-agent-workflow.patch"
elif ! git -C "$NAYOSE_DIR" apply --reverse --check "$PACKAGE_ROOT/patches/nayose-agent-workflow.patch"; then
    fail "the Nayose adapter patch does not match the pinned checkout."
fi

uv python install "$PYTHON_VERSION"
if [[ ! -x "$COMFYUI_DIR/.venv/bin/python" ]]; then
    uv venv --python "$PYTHON_VERSION" "$COMFYUI_DIR/.venv"
fi
uv pip install --python "$COMFYUI_DIR/.venv/bin/python" -r "$COMFYUI_DIR/requirements.txt"
install_torch "$COMFYUI_DIR/.venv/bin/python"
uv pip install --python "$COMFYUI_DIR/.venv/bin/python" \
    -r "$PACKAGE_ROOT/custom_nodes/agent_workflow_studio/requirements.txt"

if [[ ! -e "$NODE_DIR" ]]; then
    ln -s "$PACKAGE_ROOT/custom_nodes/agent_workflow_studio" "$NODE_DIR"
elif [[ "$(cd "$NODE_DIR" && pwd -P)" != "$(cd "$PACKAGE_ROOT/custom_nodes/agent_workflow_studio" && pwd -P)" ]]; then
    fail "$NODE_DIR already exists and is not this package's node directory."
fi

if [[ ! -x "$NAYOSE_DIR/.venv/bin/python" ]]; then
    uv venv --python "$PYTHON_VERSION" "$NAYOSE_DIR/.venv"
fi
install_torch "$NAYOSE_DIR/.venv/bin/python"
uv pip install --python "$NAYOSE_DIR/.venv/bin/python" \
    -e "$NAYOSE_DIR[matcher,performance,serve]"

# The internal local API bearer token is kept in the OS vault, never in a file.
PYTHONPATH="$PACKAGE_ROOT" "$COMFYUI_DIR/.venv/bin/python" - <<'PY'
import secrets
from custom_nodes.agent_workflow_studio.credential_store import (
    credential_configured,
    save_credential,
)

account = "nayose-api"
if not credential_configured(account):
    save_credential(secrets.token_urlsafe(32), account)
PY

mkdir -p "$APP_HOME/state/comfy-user/default/workflows"
cp "$PACKAGE_ROOT/workflows/Nayose Agent Smoke.json" \
    "$APP_HOME/state/comfy-user/default/workflows/Nayose Agent Smoke.json"
printf '\nInstalled Agent Workflow Studio at: %s\n' "$APP_HOME"
printf 'Start it with: %s/run-local.sh\n' "$PACKAGE_ROOT"
printf 'OpenCode Go key is entered later from the ComfyUI canvas menu; it is not part of installation.\n'
