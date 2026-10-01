# Agent Workflow Studio architecture

## Decisions

ComfyUI is the workflow platform: graph editor, workflow serializer, queue, run button, history, and UI. Nayose remains authoritative for generation, training, artifact storage, fingerprints, and audit. The integration consists of thin ComfyUI V3 nodes and a small Nayose API adapter; it does not move datasets or arbitrary filesystem paths over graph edges.

OpenCode Go is a single dataflow node, not a planner. Upstream Comfy nodes provide its prompt and JSON Schema; its output is a native ComfyUI `DICT`. The Nayose adapter sends that dictionary through Nayose's generation-preview validation before emitting the typed `NAYOSE_GENERATION_CONFIG` socket. Credentials stay server-side in the OS keyring.

## Current nodes and workflow

The V3 extension registers:

- Generation Controls → `NAYOSE_GENERATION_CONFIG`
- Validate Agent Controls: `DICT` → Nayose preview validation → `NAYOSE_GENERATION_CONFIG`
- Preview Generation → `NAYOSE_GENERATION_PREVIEW`
- Generate Dataset → `NAYOSE_GENERATION`
- Train Model → `NAYOSE_MODEL_RUN`
- OpenCode Go Structured Agent: connected/editable `STRING` prompt + schema → native `DICT`

The bundled ComfyUI-native workflow is `workflows/Nayose Agent Smoke.json`:

`StringConcatenate → OpenCode Go Structured Agent → Validate Agent Controls → Preview / Generate → Train`

The training device defaults to `auto` and resolves to the ComfyUI host's available MPS, CUDA, or CPU backend. The smoke workflow uses `model_family=off`; it verifies workflow/dataflow and fingerprint binding, not neural training. Promotion is not implemented and must never happen automatically. A future promotion node must require explicit human approval.

## Artifact and credential boundaries

Comfy edges contain only small typed config/artifact objects. A generation reference contains its immutable generation ID and fingerprint; a model reference contains the run ID and fingerprint. Nayose validates the generation ID beneath its generations root and requires its manifest before training. Training rejects a result whose fingerprint differs from the connected generation.

OpenCode Go's API key is saved in the host OS credential vault by the ComfyUI credential dialog. It is not a widget, workflow field, browser-storage value, or API response. The local Nayose API bearer token is stored separately in the same OS vault. Credential routes accept loopback/same-origin requests; keep ComfyUI bound to loopback.

## Installation and platform

`install.sh` pins ComfyUI and Nayose source commits through `versions.lock`, applies only `patches/nayose-agent-workflow.patch`, installs isolated environments, and puts runtime data outside this repository. `run-local.sh` starts Nayose and ComfyUI locally. The installer has macOS and Linux branches; Apple Silicon uses PyPI PyTorch wheels with MPS, Linux selects CUDA wheels when an NVIDIA device is present, and CPU is the fallback. The macOS branch has not yet been tested on a physical Mac.

The first generation may download/cache Nayose's configured reference sources under the runtime Nayose checkout. Dataset/model artifacts are never included in this repository.

## Verification and known gaps

The node suite covers schema/socket behavior and client validation. The smoke workflow was loaded in ComfyUI and executed with the Run button through a loopback OpenAI-compatible mock; generation and training succeeded with matching fingerprints. A live OpenCode Go request still requires the operator's newly rotated key and must be entered through the credential dialog. GPU-backed neural training was not verified; the completed smoke run had `model_family=off`.

A complete generation-bound evaluation API/node and explicit human approval/promotion workflow remain future work. The current workflow reports Nayose preview and training/evaluation artifacts but does not promote a model.

## Distribution contents

- `custom_nodes/agent_workflow_studio/`: V3 nodes, keyring-backed credentials, API clients, extension routes, and tests.
- `patches/nayose-agent-workflow.patch`: safe generation-ID pinning and device propagation for the pinned Nayose checkout.
- `workflows/`: native ComfyUI workflow templates.
- `install.sh` / `run-local.sh`: local setup and launch.
- `versions.lock`: reproducible source/runtime version pins.

ComfyUI's core and frontend are downloaded at pinned revisions rather than vendored or forked in this repository.
