# Agent Workflow Studio

ComfyUI is the workflow editor, serializer, queue, and run-button runtime. Nayose remains authoritative for dataset generation, training, fingerprints, and artifacts. This repository installs both applications locally and adds a small ComfyUI V3 custom-node package.

## Install (macOS / Linux)

The installer supports Apple Silicon macOS (PyTorch MPS) and Linux (NVIDIA CUDA or CPU). Requirements: Git, `curl`, and internet access for the pinned source/dependency downloads. It bootstraps `uv` and Python 3.12. Apple Silicon is recommended for Nayose's production F2LLM profile, which is locked to Apple MPS; CPU-only runs work for the smoke workflow but do not provide neural training.

```bash
git clone https://github.com/mvsbm/agent-workflow-studio.git
cd agent-workflow-studio
./install.sh
./run-local.sh
```

The scripts use `versions.lock` for the ComfyUI and Nayose commits and install under `~/.local/share/agent-workflow-studio` by default. Override the runtime location with `AGENT_WORKFLOW_STUDIO_HOME=/path/to/runtime`. Runtime data, logs, generated datasets, model bundles, and the ComfyUI user directory stay outside this repository. On macOS, allow the OS Keychain prompt when the credential dialog is first used.

Open <http://127.0.0.1:8188>. Load **Nayose Agent Smoke** from ComfyUI's workflow list. The template shows the intended typed flow:

`StringConcatenate → OpenCode Go Structured Agent (DICT) → Nayose Validate Agent Controls (NAYOSE_GENERATION_CONFIG) → Preview / Generate → Train`

Use ComfyUI's **Run** button to execute. The LLM result must pass both its JSON Schema and Nayose's generation-preview gate before it can reach generation. The bundled workflow uses smoke data, `device=auto` (MPS on Apple Silicon), and `model_family=off` for a low-cost integration check; it is not a GPU neural-training benchmark.

## OpenCode Go credential

The installer creates a random local Nayose API bearer token in the host OS credential vault. It does not provision an OpenCode Go key. Configure a newly issued provider key from the ComfyUI canvas' **OpenCode Go Credentials** action when ready to use the hosted model. Keys stay on the server in the OS keyring and are never stored in node widgets, browser storage, or workflow JSON. Do not commit keys or paste them into workflows.

The Studio fixes the requested model to `mimo-v2.6-flash`. No live provider call is performed during installation. Provider account access/model availability must be verified by the operator with their rotated key.

## What is included

- ComfyUI V3 nodes with native Comfy sockets: editable Nayose controls, Nayose domain validation, preview, generation, training, and OpenCode Go structured JSON.
- A Nayose API client that passes artifact IDs and fingerprints—not dataset contents or arbitrary paths—over graph edges.
- A server-side OS-keyring credential dialog and loopback-only credential routes.
- A minimal Nayose patch that safely pins training to a validated generation ID and preserves the configured device across resource planning.
- A ComfyUI-native smoke workflow and a single local installer/launcher.

Promotion is intentionally not automatic. Any future promotion node must require explicit human approval. GPU availability at ComfyUI startup does not imply that Nayose's locked production neural-training profile supports CUDA.

## Development checks

Run node tests from the ComfyUI checkout so `comfy_api` is importable:

```bash
cd /path/to/ComfyUI
.venv/bin/python -m pytest custom_nodes/agent_workflow_studio -q
ruff check custom_nodes/agent_workflow_studio
ruff format --check custom_nodes/agent_workflow_studio
```

The Nayose source patch is `patches/nayose-agent-workflow.patch` and is applied only to the pinned checkout. The installer refuses to reset a modified runtime checkout.
