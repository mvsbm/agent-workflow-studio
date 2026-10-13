# Agent Workflow Studio

A [ComfyUI](https://github.com/comfyanonymous/ComfyUI) extension that puts a **Pi-backed agent inside the node editor**. The agent sees the live canvas, searches every installed node schema, plans a workflow, builds and edits it with real nodes and typed links, runs it, and reads the results. Everything it does lands on the native canvas, so you can inspect, undo, or take over at any step.

## What the agent can do

The **Pi** sidebar embeds Pi's persistent RPC agent. It works through these tools:

| Tool | What it does |
| --- | --- |
| `comfy_read_workflow` | Reads the live canvas: nodes, widgets, links, selection. |
| `comfy_node_catalog` | Searches all installed node schemas (names, descriptions, aliases, enum choices). |
| `comfy_edit_workflow` | Adds, edits, connects, moves and retitles nodes, up to 32 operations per batch. Edits are applied atomically with native Undo/Redo, and a failed batch rolls back. |
| `comfy_run_workflow` | Queues a native Run. Asks for confirmation unless you enabled auto-approve for that workflow. |
| `comfy_execution_results` | Reads outputs and errors of the run it submitted. |
| `comfy_workspace` + read/edit/write | A private per-workflow file workspace for notes and data files. |

Four bundled skills guide it: **node creation, workflow debugging, connector/contract management, and node organization**.

### Planning before building

The agent plans with you before it builds. It asks multiple-choice questions (each with an "Other" free-text answer) until the requirements are clear, then proposes a **workflow plan** in a standard format: the goal, what you provide and get, each step in plain words, and how we know each step worked (its **success gate**), plus assumptions and decisions. Node names and connections are kept in a technical section at the end. You approve the plan in the chat; it is saved as a version, and the agent cannot add nodes to a workflow until a plan is approved (small fixes such as changing a value don't need one). After a run the agent checks each gate against the real outputs: gates with an automatic check pass or fail, the rest are listed for you to confirm.

### Transparency

- Tool calls expand inline in the chat; replies render through ComfyUI's sanitized Markdown.
- The workflow is the plain ComfyUI graph: no hidden state, no separate workflow format.
- The chat status separates *no workflow submitted*, *submitted but unverified*, *execution failed* and *native execution succeeded*. A chat reply alone never counts as proof that a node ran.
- Chats are saved read-only per workflow (**History**). **Stop** aborts the agent; use ComfyUI's Interrupt for a job that is already queued.
- Click the provider/model header in the chat to choose provider, model, thinking level and credential source per workflow.

### Versions

Every successful agent edit batch and every save of a workflow is recorded as a version in that workflow's own local git repository (under the ComfyUI user directory, in `agent-projects/`). The agent writes a one-line summary of each batch as the version message. The **Versions** sidebar lists them newest first, labelled *Agent* or *You*, shows what each changed (nodes added, removed or changed; links added or removed), and can restore any version. A restore is itself a new version and one native undo step; history is never rewritten. Files in the agent's workspace are not versioned yet.

Each run of a **Pi CLI · Structured LLM** node is also recorded as a version, together with the exact workflow that ran: status, model, prompt, output schema, the validated output, duration and token usage. A failed run records only its error category, never provider error text. Credentials and the model's private reasoning are never recorded. Open the run's version in **Versions** to see its record. Records stay in the local repository; nothing is sent anywhere.

### Boundaries

- Shell and host search tools, `then_run`, and dependency installation are disabled.
- File tools are confined to the workflow's workspace (128 KiB per file, 8 MiB, 128 files, 32 subdirectories). Traversal, symlinks and special files are rejected.
- The bridge is loopback-only, same-origin and token-authenticated. It is not a sandbox for third-party MCP servers or installed native nodes. Keep ComfyUI bound to `127.0.0.1`.
- Native **Auto Queue** still applies to canvas edits; turn it off if edits must not execute anything.
- Workspace files are data, never loaded as Pi configuration or executed.

## Nodes

- **Fuzzy · Plan with nodes**: describe a text job in plain words. The model plans a chain of allowed installed nodes (default: the built-in string nodes); the plan is checked against their real schemas (node and input names, link types, allowed values, step order) and runs as ordinary nodes. Its `plan` output shows the chosen chain, and every plan is recorded in **Versions**. The model never writes code. Text in, text out for now.

  A fuzzy node is also how an unfinished step lives on the canvas. Its color shows its state: **Planned** (blue-grey, not run yet), **Fuzzy** (purple, the model plans it on every run; the latest plan is shown on the node), **Hardened** (green). Right-click a fuzzy node and choose **Harden this step** to replace it with its latest plan as real nodes, grouped in a native subgraph titled with the goal and wired in its place. A hardened step never calls the model again. Hardening is one undo step and one version.
- **Pi CLI · Model Config** (`PI_MODEL_CONFIG_V1`): provider, model, thinking level, credential source.
- **Pi CLI · Structured LLM**: a single-turn Pi call that must fill a JSON-Schema-bound result tool. Action tools are disabled, output is validated locally, and the result goes out on a typed `DICT` socket.

Use the **Pi provider credentials & model catalog** canvas menu to manage provider API keys in the OS keychain. Pi-managed auth stays Pi-managed. No credential is stored in node inputs, workflows, command arguments or logs.

## Requirements

- macOS or Linux, Git, `curl`; the installer bootstraps `uv` and Python 3.12.
- The Pi CLI (`pi`, npm package `@earendil-works/pi-coding-agent`) on `PATH`, or set `AGENT_WORKFLOW_STUDIO_PI_CLI`.
- Optional: **SoL-Pi** and **Ponytail** in your Pi profile. When installed, the chat loads them (Ponytail in `ultra` mode, which also questions requests before building) and shows them in its header; without them it shows "No Pi add-ons".

## Install and run

```bash
git clone https://github.com/mvsbm/agent-workflow-studio.git
cd agent-workflow-studio
./install.sh      # pins ComfyUI (versions.lock), installs under ~/.local/share/agent-workflow-studio
./run-local.sh    # http://127.0.0.1:8188
```

The installer copies two demo workflows into your library: **Studio - Fuzzy Slug** (a fuzzy node turns text into a slug and shows the plan it chose) and **Studio - LLM Sentiment** (a Structured LLM node classifies a review). Both are set to `opencode-go / deepseek-v4-flash` with a Studio Keychain key; change the **Pi CLI · Model Config** node to use another model. Override the runtime location with `AGENT_WORKFLOW_STUDIO_HOME`. Restart ComfyUI after changing backend code.

## Development

```bash
scripts/test.sh   # Python (unittest, via the installed ComfyUI venv) + JS (node --test)
```

```bash
scripts/e2e.sh    # real Pi startup check, then a browser test: the agent builds and runs a workflow
```

`scripts/e2e.sh` starts an isolated ComfyUI (temp directory, free port, CPU, only this package) and replaces the LLM with a scripted fake Pi; the chat UI, canvas bridge and native queue are real. It runs with and without the optional add-ons. No test makes a provider call. To measure the real agent, run the evaluation, which **makes real provider calls** with a provider key saved in Studio Keychain:

```bash
~/.local/share/agent-workflow-studio/ComfyUI/.venv/bin/python tests/eval/run_eval.py --provider opencode-go --model deepseek-v4-flash
```

It gives the agent five requests (`tests/eval/tasks.json`) in fresh isolated instances, approves its permission prompts like a user would, and scores only evidence: run outputs, saved images, preserved nodes and run records.
