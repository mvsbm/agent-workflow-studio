# Agent Workflow Studio custom nodes

The extension registers ComfyUI V3 nodes through `ComfyExtension` and native socket types. The OpenCode Go node accepts an editable/connectable `STRING` prompt plus a JSON Schema, and returns a standard ComfyUI `DICT`. The Nayose adapter consumes that `DICT`, calls Nayose's read-only generation preview, and emits a typed `NAYOSE_GENERATION_CONFIG` socket only after the domain check passes.

The bundled flow is:

`StringConcatenate → OpenCode Go Structured Agent (DICT) → Nayose Validate Agent Controls (NAYOSE_GENERATION_CONFIG) → Preview / Generate (Nayose artifact sockets) → Train`

Side-effecting nodes are marked non-cacheable; LLM inputs and user-adjustable Nayose controls are ordinary ComfyUI inputs/widgets. Generated artifacts remain in Nayose and cross ComfyUI sockets only as validated IDs/fingerprints.

## Credentials and network

In ComfyUI, open **Agent Workflow Studio → OpenCode Go Credentials** from the canvas context menu. The key is kept server-side in the host OS credential vault; the browser sees only configured/not-configured status. It is never a widget value, workflow field, localStorage entry, or API response. A provider connection test is available in the same dialog. The Nayose API token uses a separate keyring account and is created by the installer.

The production endpoint is `https://opencode.ai/zen/go/v1/chat/completions`; the requested model is fixed to `mimo-v2.6-flash`. `AGENT_WORKFLOW_STUDIO_OPENCODE_URL` can override the endpoint only for loopback HTTP development/tests; other HTTP endpoints are rejected. The integration makes one schema-constrained chat-completion call and has no implicit tools or workflow-planning authority. Schema validation verifies output shape, not factual truth.

Credential routes accept loopback or same-origin calls. Keep ComfyUI bound to loopback unless you have a deliberate secure deployment. A missing OS vault fails closed rather than falling back to plaintext storage.

## Tests

Run the tests from the ComfyUI checkout so its `comfy_api` package is importable, using its virtual environment:

```bash
cd /path/to/ComfyUI
.venv/bin/python -m pytest custom_nodes/agent_workflow_studio -q
ruff check custom_nodes/agent_workflow_studio
ruff format --check custom_nodes/agent_workflow_studio
```

Never commit API credentials. If a key was exposed, revoke it and use only a newly rotated key.
