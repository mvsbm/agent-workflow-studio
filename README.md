# Agent Workflow Studio

A [ComfyUI](https://github.com/comfyanonymous/ComfyUI) extension that puts a **Pi-backed agent inside the node editor**. The agent sees the live canvas, searches every installed node schema, plans a workflow, builds and edits it with real nodes and typed links, runs it, and reads the results. Everything it does lands on the native canvas, so you can inspect, undo, or take over at any step.

## What the agent can do

The **Pi** sidebar embeds Pi's persistent RPC agent. It works through these tools:

| Tool | What it does |
| --- | --- |
| `comfy_read_workflow` | Reads the live canvas: nodes, widgets, links, selection. |
| `comfy_node_catalog` | Searches all installed node schemas (names, descriptions, aliases, enum choices). |
| `comfy_edit_workflow` | Adds, edits, connects, moves and retitles nodes inside a step's stage (and connects stages), up to 32 operations per batch. Edits are applied atomically with native Undo/Redo, and a failed batch rolls back. |
| `comfy_run_workflow` | Queues a native Run. Asks for confirmation unless you enabled auto-approve for that workflow. |
| `comfy_execution_results` | Reads outputs and errors of the run it submitted. |
| `comfy_workspace` + read/edit/write | A private per-workflow file workspace for notes and data files. |

Four bundled skills guide it: **node creation, workflow debugging, connector/contract management, and node organization**.

### Contracts: planning before building

Every workflow the agent builds has one **contract**, agreed with you before any node is added and stored in the workflow itself: the intent in the workflow's extra data, each step on its stage node (the high-level design), and each node inside a stage tagged with the part of the design it realizes (the low-level design). Every change is a version in the workflow's history. The agent first asks multiple-choice questions (each with an "Other" answer) until the requirements are clear, then proposes the contract in plain language:

- **Purpose**, and **what you asked for**: each request in your own words next to the agent's restatement ("When …, the workflow …"), with a priority (must / should / could).
- **Examples** of input and expected output, and the **promised** inputs and outputs.
- **Steps**, each covering your requests, with typed inputs and outputs (`takes` / `gives`: registered types from [STANDARDS.md](STANDARDS.md): TABLE, STRING, a dataset class such as RECORD_PAIRS, …) and how we know it worked (its **success gate**, checked on the data by a **Check** node where it can be).
- **Decisions** (each with why, and whether you or the agent decided), what is **not now**, and **open questions**, which must be resolved before you can approve.

You approve it in the chat; the agent cannot add nodes to a workflow, or write files to its workspace (except scratch files under `tmp/`), without an approved contract, and changing it means proposing an amendment you approve again. The contract, its design (one stage per step) and each stage's nodes are stored in the workflow itself and checked against `contract.schema.json`; the **Design** tab traces them both ways (each request → the stages serving it → the nodes realizing each part of a stage, and back). Each step's state (planned, built, fuzzy, verified, failing) is never written down: it is derived from the canvas and the latest gate check, so progress ("2 of 3 steps verified") always matches reality.

**Each step is a stage.** When you approve the contract, the canvas gets its skeleton: one stage (a subgraph card) per step, numbered by what it depends on (independent steps, such as one cleaning lane per source system, share a number and a column), wired by its typed ports, with a **Stub** passing data through until the step is built and a **Check** node for its gate. The agent builds inside the stages; it cannot place nodes outside them. A step's state comes from its stage: planned (only Stub and Checks), built, fuzzy, verified (its Checks pass on the latest run of exactly this graph) or failing.

The **Contract** sidebar tab shows the approved contract with live progress: each step's state as a colored chip with its next action, nodes outside every stage, the **to-do list** (untyped ports, stages without a Check, no workflow signature yet), and whether the progress is based on the latest run of exactly this graph.

After every change that adds, removes or rewires nodes, the canvas is also checked against the contract and the result appears in the chat and the Contract tab: **✓ aligned**, or **⚠ drift** with the reasons — nodes outside every stage, a stage whose step the contract no longer has, a port whose type differs from the contract, a fixed step that uses a Fuzzy node, or graph problems. Steps not built yet are progress, not drift.

### Agent tools

When no installed node can do a step that needs real computation (generating a data set, matching records, statistics), the agent can write a small Python **tool** for it. You see the tool's full code in the chat and approve it; the approved copy is stored in the workflow's history, and only that exact code can run. An **Agent Tool** node runs it with the node's text and data inputs and returns a result and a text summary; files it writes are saved in a run folder under ComfyUI's output, and every run is recorded in Versions. Tools use the Python standard library only and run in a separate process with a time limit; on macOS they also run inside a sandbox with no network, no other programs, no access to your files, and writing only to their own run folder. On Linux only the time limit applies, which the approval card says. Changing a tool's code needs a new approval.

### Graph checks

After every agent edit that adds, removes or rewires nodes, the workflow is checked automatically, without running it or calling a model, and the result appears in the chat: nodes that will not run (they reach no output), required inputs left unconnected, type mismatches, cycles and deprecated nodes. The agent must fix what it finds before running. It can also run the check on demand, see the checkpoints every path to an output passes (natural places for success gates), and look up the shortest chain of installed nodes that converts one data type to another.

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

The nayose workflows ship with the package and appear in ComfyUI's own **Templates** browser (under this node pack): **Studio - Record matching benchmark** (the synthetic exports and the hidden-key score) and **Studio - Record matching reference** (the whole matching pipeline built from the studio's own nodes only, no workflow code, published as an API that takes your exports). They always match the installed version. Override the runtime location with `AGENT_WORKFLOW_STUDIO_HOME`. Restart ComfyUI after changing backend code.

ComfyUI is set up as a data and text studio, with its own switches (`studio_defaults.py`, filled in only where you have not chosen): a new tab starts empty instead of ComfyUI's image example; the Templates browser lists only this pack's templates; ComfyUI's image, 3D and audio editors are off (Settings → Extension turns any back on); and `run-local.sh` loads only this node pack (`--whitelist-custom-nodes`).

## Development

```bash
scripts/test.sh   # Python (unittest, via the installed ComfyUI venv) + JS (node --test)
```

Tests live in the repository, outside the shipped node folder: `tests/unit` (Python, importing the package as ComfyUI loads it), `tests/web` (front-end modules), `tests/e2e` (browser) and `tests/eval` (the real agent). GitHub Actions (`.github/workflows/ci.yml`) runs the lint, unit, web and browser suites on every push and pull request; the real-agent evaluation stays a manual command.

**Every workflow has its own CI/CD.** Its tests are stored in it and versioned with it, one level per layer: the contract's examples (acceptance), each stage's Checks (integration), the examples of each tool the workflow runs (unit), the design rules on every stage (ports wired, every part of a step realized, no node that traces to nothing) and no regression since the last release. **Run checks** (Contract tab, or the agent's `comfy_run_examples`) runs them all on exactly this graph and keeps the run as a version (`Checks · ✓ design · ✓ tool tests · ✓ stage Checks · ✓ examples · ✓ no regression`). Publishing needs a green run; each release says why its version moved (major: a breaking change for callers, listed; minor: an addition or a design change; patch: inside stages), callers can pin `name@x.y.z`, and **Roll back** points the API at an earlier release.

**Workflow code is a tier of node, not a separate kind of thing.** A step is built from the studio's nodes whenever they can do it. When none can, the agent writes a **tool**: Python with the same interface as a node (ports typed by the type standard: core types and the dataset classes of any domain) and its own tests (examples), plus why no node would do. You approve the exact code after its examples pass in the sandbox; it is stored with a hash in the workflow's own history, runs confined (separate process, time limit, macOS sandbox), has its declared ports checked on every run, and its examples run as the **tool tests** level of Run checks. It is a node of its stage's LLD, traced like any other; the stage card says "workflow code", the Design tab shows its interface, tests and source, and Versions shows its code diff. A tool that proves itself **graduates** into a studio node: the Design tab's *Graduation brief* hands a developer its interface, tests and code, and the node goes through review and CI, since it then runs in-process for every workflow.

**APIs are built to a standard the tools enforce** (`api_review.py`, the api-design skill): names that say what they hold, a description for every input and output, records taken as uploaded data, every table output's fields named, examples covering the whole interface. A caller uploads data with ComfyUI's own `POST /upload/image` into an input folder and passes the folder name; long calls are ComfyUI's native jobs (`/api/jobs/{id}`, cancel included); errors are RFC 9457 Problem Details; the generated OpenAPI describes all of it. APIs serve this machine only; for remote callers put a reverse proxy with authentication in front of ComfyUI.

```bash
scripts/e2e.sh    # real Pi startup check, then a browser test: the agent builds and runs a workflow
```

`scripts/e2e.sh` starts an isolated ComfyUI (temp directory, free port, CPU, only this package) and replaces the LLM with a scripted fake Pi; the chat UI, canvas bridge and native queue are real. It runs with and without the optional add-ons. No test makes a provider call. To measure the real agent, run the evaluation, which **makes real provider calls** with a provider key saved in Studio Keychain:

```bash
~/.local/share/agent-workflow-studio/ComfyUI/.venv/bin/python tests/eval/run_eval.py --provider opencode-go --model deepseek-v4-flash
```

It gives the agent the requests in `tests/eval/tasks.json` (or another file with `--tasks`, e.g. the nayose scenarios in `tests/eval/scenarios/`; `--publish` copies each finished workflow with its versions, tools and data into the studio on 8188, never overwriting) in fresh isolated instances, answers questions with the first option and approves prompts like a user would, and scores only evidence: run outputs, saved images, preserved nodes and run records. It also reports whether the agent followed the process: contract approved, approved before any building or writing, every node tagged, final canvas aligned, workflow ran, gates checked, checks green, and how many tries each proposal took (`retries`, `first_try`): a rejected proposal lists every problem at once, so this should stay near zero. The evaluation runs locally only (it needs your provider key).
