# Design: fuzzy-to-deterministic agentic studio

Status: proposal. Describes where the code is going, not what it does today (see [ARCHITECTURE.md](ARCHITECTURE.md) for that).

## Thesis

Every job starts fuzzy. An LLM is good at fuzzy work, and it does that work with tools. As the user learns what the job really needs, fuzzy steps are replaced by deterministic ones. The product is the **versioned, typed, replayable history of everything the LLM did**, so LLM work can be tracked, reproduced, audited and reverted instead of trusted.

## Two layers

| Layer | Role | Status |
| --- | --- | --- |
| **Meta layer**: the Pi chat sidebar | Plans, builds, edits, runs and debugs the whole workflow with the user. Decides where fuzzy nodes go and when to harden them. | Exists (`comfy_*` tools). |
| **In-graph layer**: the **Fuzzy node** | Does one fuzzy job inside the graph, possibly with many internal steps. | Missing. Today's `Structured LLM` node has no tools and no graph awareness. |

## Concepts

### 1. Project repository (one per workflow)

Each workflow gets a local Git repository, its **project**. Every change is a commit, so Git is the version history, audit log and undo for everything below.

```
<comfy user dir>/projects/<workflow-id>/      # a git repo
  workflow.json            # snapshot of the canvas
  nodes/<fuzzy-node>/      # fuzzy node folders (concept 2)
  records/                 # run records (concept 4)
  artifacts/               # large outputs, git-ignored, referenced by hash
```

- **What gets committed:** each agent edit batch, each user save (debounced), each accepted code change, each run record, each hardening step. The agent's commit message states its intent; the author is `pi-agent` or the user, and run commits carry the record ID.
- **Revert is a new commit** that restores an earlier snapshot. History is never rewritten.
- **Visualization:** a History panel shows the commit graph. Selecting a commit shows what changed on the canvas (nodes and links added, removed, changed) and lets the user preview it, restore it as a new commit, or branch from it.
- **Reuse:** the existing per-workflow chat workspace becomes this repository. Fuzzy node folders can be copied between projects; the copy records its source project and commit.
- The project repo lives under the ComfyUI user directory and is separate from this source repository. It has no remote by default.

### 2. Fuzzy node = a folder

A fuzzy node is a small self-contained folder, so one node can do a multi-step job, and nodes can nest.

```
nodes/<name>/
  node.json        # typed inputs/outputs (the contract), tool allowlist, version
  subgraph.json    # inner workflow as a native ComfyUI subgraph (see "Built on ComfyUI")
  tools/*.py       # optional Python tools the inner steps can call
  cases/           # pinned input/output examples (concept 5)
```

At run time the node loads its folder, asks the model for a **plan**, validates the plan locally against the node's contract and the installed node schemas, and expands it into inner nodes with ComfyUI's V3 node expansion. Inner nodes appear in execution history like any others. Because the inner graph can itself contain fuzzy nodes, workflows nest. Once a plan is accepted, the inner graph is stored as a native subgraph, which is what makes it viewable and editable in ComfyUI's own UI.

### 3. Typed connectors

A connector is a ComfyUI type plus an optional JSON Schema or invariants. Each fuzzy node declares its contract in `node.json`, and model output is rejected at the boundary if it does not conform. This is the up-front cost, and it is what makes a fuzzy step replaceable later: a deterministic replacement only has to satisfy the same contract.

### 4. Run records

Each fuzzy run writes a content-addressed record `AGENT_RUN_RECORD_V1` into `records/`: goal, input hashes, tool allowlist, model and settings (never credentials), the validated plan, the expanded node list, output hashes, timings and usage, and parent record IDs. Provider-private reasoning text is never stored. Raw prompts and large outputs go to `artifacts/` (git-ignored); the committed record keeps hashes.

### 5. Hardening

Replacing fuzzy with deterministic is always a user-approved edit, and each step is a commit:

- **Materialize:** replace the fuzzy node with its accepted plan as editable native nodes.
- **Pin:** save input/output pairs from run records as `cases/`.
- **Partial:** keep a fuzzy node only for the residual judgment and make the rest deterministic.

A frozen graph must reproduce its pinned cases with the LLM disabled. Opt-in per node, an identical input hash may reuse a stored result instead of calling the model.

## Built on ComfyUI

ComfyUI is the platform, and the design uses its own mechanisms wherever they exist. Items marked ✔ were confirmed present in the pinned ComfyUI core and frontend (`versions.lock`); whether they compose as described is checked by a spike at the start of the phase that needs them.

| Need | ComfyUI mechanism |
| --- | --- |
| Fuzzy node and tool definitions | ✔ V3 `io.ComfyNode` schemas, typed sockets, `io.Custom` types (already used for `PI_MODEL_CONFIG_V1`). |
| Variable typed inputs on a fuzzy node | ✔ V3 `Autogrow`, `DynamicCombo`, `DynamicSlot`, `MatchType`. |
| Running the plan as real nodes | ✔ `enable_expand` and `GraphBuilder` (`comfy_execution.graph_utils`). |
| Not caching LLM calls; opt-in reuse | ✔ `fingerprint_inputs`, `not_idempotent`. |
| Nested workflows | ✔ Native subgraphs and subgraph blueprints. ComfyUI loads blueprints from `<custom node dir>/subgraphs/<name>.json` (`app/subgraph_manager.py`), so fuzzy node folders can ship as blueprints users insert from the normal UI. |
| Canvas, undo, queue, history, outputs | ✔ The live `app.graph`, native undo, native Run, `/history`. Already used by the chat bridge. |
| UI surfaces | ✔ Frontend extension hooks: `registerSidebarTab` (chat, History panel), `nodeCreated`, `beforeRegisterNodeDef`, `selectionToolbox`, `bottomPanelTabs`, commands and menus. |
| Install and distribution | The Comfy Registry / `comfy-cli` metadata in `pyproject.toml`, so the package installs through ComfyUI-Manager. Not done yet. |

Where we add to ComfyUI rather than reuse: the per-workflow Git repository, run records, hardening and the proposal-branch code review. ComfyUI has no equivalent. We do not add a parallel workflow format, node registry or execution engine.

Known deviation to fix in P0: the chat UI (`pi_chat.js`) is hand-built DOM and CSS. It should be rebuilt on the frontend's own components and conventions.

## Safety: agent-written code

Letting the agent write `tools/*.py` means executing model-authored Python. That is the largest risk in this design. Rules:

1. **Inert until approved.** Agent-written code is committed to a proposal branch and does nothing until the user reviews the diff and accepts it. Git is the review mechanism.
2. **Never in the ComfyUI process.** Tools run in a separate subprocess with a timeout and a restricted working directory.
3. **Limits we should state plainly:** a subprocess is not a security sandbox. Real isolation (the earlier WASM/WASI worker, or an OS sandbox) is a later phase, and until then user review of the diff is the control.
4. **Side-effect-free first.** Tools and nodes may not write outside the project, use the network or call paid APIs until an approval model for effectful tools exists.

## What already exists

- Chat, canvas bridge, node-schema search, undo-safe edits, run and result reading, and a per-workflow workspace (the meta layer).
- Pi CLI adapter with schema-validated structured output and safe progress telemetry (`llm_harness.py`).
- Deleted prototypes (in git history before `b72418c`, or the local backup tarball) that proved plan validation, `GraphBuilder` expansion, canvas materialization, and a WASM worker for generated code. Lift approaches selectively; do not restore them wholesale.

## Phases

| Phase | Deliverable | Done when |
| --- | --- | --- |
| **P0 Foundation** | Readable `pi_chat.js`; SoL-Pi and Ponytail optional; one browser end-to-end test where the agent builds and runs a small workflow. | The test passes without the two add-ons installed. |
| **P1 Project repo** ✔ (slim: linear list, no lane graph or squashing; workspace files not yet versioned) | Per-workflow git repo with auto-commit of agent edits and saves; History panel with commit graph, diff and restore-as-new-commit. | Any agent or user change can be seen as a commit and reverted from the UI, and the canvas matches the restored commit. |
| **P2 Run records** ✔ (Structured LLM node; full prompt/output kept in the local record, no separate artifacts store) | `Structured LLM` writes records into the project repo; chat tools to list and read them. | A run commits a record with a stable hash and no credentials or reasoning text. |
| **P3 Fuzzy node folders** ✔ (slim: a single `Fuzzy · Plan with nodes` node, text in/out, allowlist widget; no folders yet) | `node.json` / `graph.json` loader, plan validation, expansion; side-effect-free nodes only, no generated Python yet. | A goal produces a visible, type-valid inner graph; invalid plans fail locally before anything runs. |
| **P4 Hardening** ✔ (slim: step states on canvas, plan shown on the node, Harden → native subgraph; pinned cases and offline replay not yet) | Materialize, pin cases, offline replay check. | A frozen subgraph reproduces its pinned cases with the LLM disabled. |
| **P5 Agent-written tools** | Proposal branches, diff review, subprocess execution. | Unapproved code never runs; approved code runs out of process. |
| **P6 Studio MCP server** | Publish the same canvas tools (read, catalog, edit, run, results, history) as an MCP server, bridged to the open browser session, so other MCP clients such as Claude Code can drive the studio with the same undo, rollback, confirmation and commit safeguards. Pi keeps using the tools natively. | An MCP client builds and runs a workflow, and the edit appears as one undo step and one commit. |
| **P7 Orchestration** | Chat tools to create fuzzy nodes, inspect records and history, and propose hardening as a reviewable diff. | The user can go from request to a partially hardened workflow entirely in conversation. |

## Evaluation results

2026-10-13, `opencode-go / deepseek-v4-flash`, thinking `low`, `tests/eval/run_eval.py`: **5/5**.

| Task | Built | Evidence | Time |
| --- | --- | --- | --- |
| uppercase | PrimitiveString → CaseConverter → PreviewAny | output `HELLO STUDIO` | 21 s |
| join | StringConcatenate → PreviewAny | output `alpha-beta` | 31 s |
| edit_existing | kept both nodes, inserted CaseConverter (1 rewiring approval) | output `GOOD MORNING` | 26 s |
| image | EmptyImage → SaveImage | 1 PNG saved | 21 s |
| fuzzy_llm | Structured LLM node with a `sentiment` schema → PreviewAny | `{"sentiment": "positive"}`, run record committed | 31 s |
| fuzzy_node (added with P3) | Fuzzy node planned StringTrim → CaseConverter → RegexReplace | output `hello-big-world`, plan recorded | 40 s |

The first edit_existing attempt stalled because the harness did not answer the agent's (correct) request to rewire an occupied input; the harness now approves such requests and counts them. Five tasks is a smoke test, not a benchmark: it shows the tools and guide work end to end with a small fast model, not how the agent handles ambiguous or large requests.

## MCP decision

Community ComfyUI MCP servers can edit the live canvas, and Comfy's official local MCP server is in private testing and aimed at running workflows. We do not route Pi through a third-party MCP server: it would duplicate our bridge without its safeguards (atomic undoable batches, rollback, destructive-edit and Run confirmation, auto-commit) and would add someone else's custom node with broad canvas access. Pi gets the tools directly from our extension, which is the shortest path. Exposing our own tools over MCP (P6) is the way to let other agents use the studio.

## Non-goals

- Installing dependencies, or running agent code inside the ComfyUI process.
- Autonomous background agents; every run is user-initiated and visible.
- Multi-agent protocols. Revisit only after P4.

## Open questions

1. **Isolation for agent-written code.** Is user-reviewed diff plus subprocess acceptable for P5, or must P5 wait for real sandboxing?
2. **Pushing project repos.** Records and plans may contain sensitive data. Default is local-only; sharing a project would need a review/redaction step first.
3. **Where autosave commits stop.** One commit per canvas save may be noisy. Proposal: debounce user saves and squash them under one user-authored commit per editing session.

## Spike results (2026-10-13, pinned ComfyUI + frontend 1.54.12)

| Spike | Result | Consequence |
| --- | --- | --- |
| Headless UI | ✔ `playwright-core@1.62.0` drives the cached Chromium (rev 1234) with no download. An isolated instance uses `--base-directory <tmp>` (own `custom_nodes/`, only our pack), a free port and `--cpu`. A fresh user dir opens a Templates dialog. | E2E fixture seeds settings to skip it. Commands that open dialogs must not be awaited in tests. |
| Undo granularity | ✔ One agent edit batch is exactly one native undo step; Redo restores it. A failed batch rolls back and leaves no undo entry. | Auto-commit only after a successful batch. |
| Build and run | ✔ Bridge `edit` (3 nodes, 2 links) then `run` then `results` succeeds end to end; `app.graph.serialize()` is deterministic and equals the workflow's stored state. | Snapshots for commits come from `serialize()`. |
| Nested expansion | ✔ A node expanding into a node that expands again produces the right output. Expanded nodes do **not** appear in `/history` unless they have UI output. | Run records, not ComfyUI history, are the audit trail for fuzzy nodes. |
| Subgraphs | ✔ Native `Comfy.Graph.ConvertToSubgraph` on bridge-created nodes works and runs (flattened ids like `4:1:PrimitiveString`). `Comfy.PublishSubgraph` saves `user/default/subgraphs/<name>.json` (workflow JSON with one node plus `definitions.subgraphs`). A pack's `subgraphs/*.json` is listed by `/global_subgraphs`, but the frontend has no public API to insert a blueprint. Our `read` tool shows a subgraph node only by its UUID type. | Materialize = bridge adds nodes, then the native convert command. `read` must describe subgraph nodes (name, inner nodes). |
| Save signal | ✔ Pinia `workflowStore.$onAction` fires `saveWorkflow` exactly once per save (first save and later saves), followed by `POST /userdata/workflows/<name>.json`. | User-change commits hook `saveWorkflow`. |
| Git isolation | ✔ Separate `--git-dir`, isolated config/HOME, `core.hooksPath=/dev/null`: model-written `.git/hooks`, `.git/config` and `.gitconfig` never executed; git never tracks `.git` paths. ~24 ms per commit, ~9 ms log, ~29 ms restore-as-commit over 500 commits. | Project repo design confirmed. |
| Add-ons absent | ✔ Confirmed: with an empty Pi profile, `pi_chat_extension.ts` fails to load ("Install/configure sol-pi…"); with the user's profile it loads and Ponytail reports `full`. | Make them optional; this real-Pi startup check (no provider call) becomes a regression test. |
