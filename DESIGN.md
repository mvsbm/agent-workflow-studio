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
| **P5 Agent-written tools** ✔ (stdlib-only tools, user-approved copies in project history, macOS sandbox; Linux: time limit only) | Proposal branches, diff review, subprocess execution. | Unapproved code never runs; approved code runs out of process. |
| **P6 Studio MCP server** | Publish the same canvas tools (read, catalog, edit, run, results, history) as an MCP server, bridged to the open browser session, so other MCP clients such as Claude Code can drive the studio with the same undo, rollback, confirmation and commit safeguards. Pi keeps using the tools natively. | An MCP client builds and runs a workflow, and the edit appears as one undo step and one commit. |
| **P7c Contract tab, step groups, alignment** ✔ | Contract sidebar tab with live progress and ✓ aligned / ⚠ drift; steps drawn as native groups; alignment after every structural edit, from one shared report. Examples as automatic checks: not yet. | Browser e2e: aligned after the build, drift for a stray node. |
| **P7b Contracts** ✔ | YAML contract v2 (`contract.schema.json`): requests (user's words + restatement + priority), examples, promises, steps with gates, decisions, not-now; nodes tagged with their step; step states derived through one transition table; `comfy_propose_contract`, `comfy_contract_status`; contracts skill. | Real model writes contracts with verbatim requests and output-node gates (eval). |
| **P7a Planning** ✔ | `comfy_ask_user` (multiple choice), `comfy_workflow_spec` (standardized plan, approved by the user, versioned as `spec.json`, required before adding nodes), `comfy_check_gates` (success gates checked against real outputs). | Real model asks, plans with gates and checks them (eval). |
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

### Nayose scenario (2026-10-14, deepseek-v4-flash)

A Japanese insurer nayose request (synthetic policy/claims/payment data, each corrupted differently, near state-of-the-art matching) run through `tests/eval/scenarios/nayose.json`. Before agent tools the agent could only build an LLM demo or leave Python files it could not run. With P5 it asked four questions, got the contract approved, wrote and got approved three tools (generator, Fellegi–Sunter + EM matcher with Japanese normalization, evaluator), built 19 nodes in three tagged steps and verified all gates. On realistic corruption (300 people, 1,129 records): pairwise precision 0.948, recall 0.980, F1 0.964, blocking recall 0.993; it found and fixed a bug in its own evaluator before reporting. Monitoring these runs found and fixed harness problems: false secret matches, keys lost between edit batches, single-query catalog, a 16-turn cap, results too large for the chat, gate checks that did not wait, a 120 s tool limit, and text limits the model kept exceeding.

### Nayose benchmark the agent does not grade (`tests/eval/nayose/`)

That score came from data and an evaluator the agent wrote itself. `generate.py` (stdlib, seeded) now produces the data instead: three legacy exports in the input folder (policies in Shift_JIS from a mainframe and two newer systems, claims as an Excel UTF-8 export, bank-format payments), damaged the way real data is — OCR on paper forms, agency staff who skip or copy fields (同上), call-centre staff typing names they only heard (wrong kanji, guessed readings), a mainframe with no small kana that stores rare kanji as 〓, era dates (S45.03.12, 3450312), maiden names and addresses updated on some policies and not others, municipalities renamed by mergers, payers and claimants who are family members, look-alike strangers with the same name. The answer key (who every mention is, and how it was damaged) stays outside the agent's reach; `score.py` reports pairwise and B-cubed F1, recall per damage type and per system pair, and what false merges were. A naive normalized-kana match scores pairwise F1 0.80 (recall 0.20 on maiden names, 0.23 on guessed readings, 0.35 on OCR damage). Agent tools read such data through the node's read-only `input_folder`; the agent previews it with `comfy_input_files`.

First real-agent run on it (deepseek-v4-flash): process all ✓, pairwise P 0.983 / R 0.694 / F1 0.814 (naive 0.80). It hid the whole pipeline in one 800-line tool, so the canvas showed three nodes; tools could not pass large data to each other. Now a downstream Agent Tool reads upstream run folders (`inputs["_run_folders"]`, read-only, this workflow's own runs only) and tools over 500 code lines are refused with "one tool per stage". 74% of its missed pairs had identical or near-identical kana: the loss was hard vetoes on damaged birth dates, not name similarity, so embeddings would not have helped much.

Local judge check: Gemma 4 E4B (Q4_0, llama.cpp, 2.1 s/pair on an M4) on 200 hard pairs (100 missed true matches, 100 different people with similar names): 68% correct, precision of "same" 0.71, recall 0.61; a same-kana rule gets 56%. Too imprecise to merge on its own; useful as a suggestion with a reason for the person reviewing.

**Review tab.** A tool writes `review.json` (`pairs` to decide, `groups` to check) in its run folder; the tab shows records side by side with differing fields marked, and the user's decisions (same / different / not sure; one person / has someone else; records that are not this person) are committed as the user's `labels/review.json` and passed to later tool runs as `inputs["review_labels"]`.

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

### Everything on the canvas: benchmark nodes, a visible matching cascade, organized layouts

- **Benchmark as nodes** (`nayose_nodes.py`, template `workflows/Studio - Nayose benchmark.json`): Nayose · 1 People → 2 Insurance activity → 3 Legacy export (×3, one per system, each with a damage setting) inside a *Synthetic data* subgraph, and **Score** (hidden answer key). Every stage shows its data as tables in the node. Score's results are never shown to the agent (bridge `agentOutputs`), and it has no outputs, so nothing can feed the key back into a tool: a visible run where the agent could read per-damage scores tuned itself to them (F1 0.87, not comparable).
- **Agent Tool nodes show what each step did**: operation (approved description), every table file in and out with rows and columns, and output samples (`data_preview.py`). Tools chain files through `inputs["_run_folders"]` (read-only, this workflow's runs only); tools over 500 code lines are refused, so the work stays split into visible stages.
- **The matching cascade as nodes**: compare tool (one level per field per pair) → **Learn weights** (Fellegi–Sunter EM over comparison patterns, review decisions as known answers, points capped at ±6 so no field becomes a veto) → **Decision rules** (YAML rules + scorecard + thresholds; flow with counts and each branch's condition; per-branch agreement with the user's reviews; spot-check examples) → **LLM suggestions** (local model server only; suggestions with reasons in Review, never decides) → **Cluster guard** (strongest merges first, refuses merges joining records decided different or exceeding a size cap; draws the whole decision tree) → result tool.
- **Review tab**: pairs and groups from any `review.json`, decisions committed as the user's `labels/review.json` and passed to tools and to Decision rules / Learn weights.
- **Layout is automatic**: after structural edits the canvas is laid out left to right by data flow, previews under their source, given nodes first in their column, step groups re-fitted (`arrangeGraph`, `organizeGraph`).
- **Agent quality loop**: before reporting a data job the agent spot-checks every decision branch, runs checks that need no answer key (coverage per source, group sizes, contradictions in groups, review band size), reads the user's review agreement, names the node to fix, changes only it and compares before/after.
- Fair visible runs (no answer-key feedback): run 2, decision in a Decision rules node with hand-picked points: F1 0.52 (families merged by shared address/phone/account, payments never linked). Run 3, with the quality loop: F1 0.829 (P 0.991, R 0.713), every source covered, household false merges 4,679 → 17, the agent fixed a specific rule itself; recall is limited by 26k pairs left in the review band.

### Two levels: a row of stage cards, details inside

The flat canvas showed how the workflow is built, not what happened to the data: overlapping nodes (layout used stale sizes), the same data three times per step (tool view, sample preview, count preview), settings drowning results, identical boxes, a 4,000 px canvas unreadable at any zoom, a tangle of preview links, and the decision logic buried mid-canvas. Now:

- **Overview**: `{"op": "organize"}` packs each contract step into a native subgraph; the top level is one row of **stage cards** in step order (given data first, Score last). A card shows the step's purpose, its key numbers (rows in → out, decisions per outcome, customers and blocked merges, learned match share) and its gate status, coloured by kind (data, stage, decision). Double-click opens the stage. A spike confirmed DOM cards on subgraph nodes survive runs and reloads.
- **Inside a stage**: the tool shows its operation, data in → out and a sample; previews only for what it does not show; gates may check a tool node directly (its result text is on the node). Decision rules shows the rules in effect even when they arrive over a link, every branch with its condition, the scorecard once.
- Contract evidence, gates and outputs look inside stages (`deepNodes`, run ids `outer:inner`); `unpack` opens a stage for editing. Layout measures real heights.
- Run 4 (cascade available; fair, no key feedback): F1 0.873 (P 0.930, R 0.822), 6/6 steps verified, the agent built standardize → candidates → Learn weights → Decision rules → Cluster guard → write result and tuned thresholds itself; family false merges rose again (1,199), the next thing to fix.

### Standard stage names, a matching split, and steps that explain themselves

- Matching jobs use the standard entity-resolution stages as contract step ids, which title the stage cards: profile_data → cleanse_and_standardize → find_candidates → deterministic_matching → probabilistic_matching → (review_uncertain) → consolidate_customers → publish_results.
- **Profile data (EDA)** node: per column fill, distinct values and value patterns (9/A/a/カ/ｶ/ひ/漢 with runs shortened), so mixed formats show at a glance.
- **Deterministic → probabilistic**: Decision rules without a scorecard leaves unsettled pairs `undecided`; a later Decision rules keeps earlier decisions ("decided by an earlier stage") and scores only the undecided ones.
- **Every tool explains itself**: `explain = {steps: [{what, count}], examples: [{before, after, why}]}` is shown first on the node ("What this step did", "Before → after"); tools without it are flagged. Run details, file shapes and one sample come after.
- Layout inside every stage, connectors on the left and right; nodes reserve the height their view uses (no spill-over); analysis treats a stage holding an output node as an output.
- Run 5 (fair): F1 0.868, P 0.855, R 0.881, 2,418 customers vs 2,210 true; the agent built the standard stages, explained each tool, ran the quality loop (added two rules, tried and reverted a third that flooded review) and organized into stages itself. Next weakness: households merged (shared address/phone/account).

### From black boxes to graphs: table nodes

Why stages had become black boxes: ComfyUI ships no data nodes, so every data step was an agent-written script in one Agent Tool node; the visibility work (previews, shapes, the tool's own explanation) decorated the scripts instead of replacing them. Only our own nodes (Decision rules, Learn weights, Cluster guard) were transparent.

Now data jobs are graphs of **table nodes** (`table_nodes.py`, a TABLE type between them): Read table, Stack roles, Concatenate tables, Filter rows, Join tables, Normalize text, Map values, Parse dates (Japanese eras, counted per form), Extract digits, Find candidate pairs (one pass per key), Compare fields (levels per field, Jaro-Winkler, date closeness), Attach group ids, Write table. Each node reports rows in → out and before → after for the values it changed, by itself. Agent Tools remain for gaps only and are coloured amber, as are stages that contain one. A stage card for a table-step stage shows its step count and rows in → out.

`workflows/Studio - Nayose reference (no code).json` solves the benchmark with these nodes only, in the standard stages (built by `scripts/build_templates.sh`): F1 0.708 (P 0.58, R 0.90) with untuned starting rules — over-merging that is visible and editable on the canvas.

### The graph mirrors the architecture

Source systems are parallel lanes (stages sharing a number share a column), each cleaning its own kind of dirt, merging into candidates. Matching is a cascade with real branches: Decision rules has **matched**, **different** and **unsettled** outputs; the next stage judges only the unsettled pairs (`pairs_subset`); **Your review decisions** makes the user a node (Review-tab decisions → matched / different / open); **Cluster guard** takes every stage's matched and different branches (`matched_1..3`, `different_1..3`) and its tree shows which stage decided what. Nodes label their branches with their own titles (hidden prompt). The reference workflow is rebuilt this way.

### Against classic deterministic matching (Trillium-style), and configurable rules

Same benchmark data, scored by `score.py` (the reference built by `scripts/build_templates.sh`; `VARIANT=deterministic|patterns` in `build_reference.cjs` prints the alternatives):

| approach | precision | recall | F1 |
|---|---|---|---|
| certain rules only (deterministic stage alone) | 0.997 | 0.246 | 0.394 |
| Trillium-style match-pattern grid (9 patterns, comparison levels, no learning) | 0.990 | 0.414 | 0.583 |
| reference cascade before (untuned starting rules) | 0.583 | 0.899 | 0.708 |
| reference cascade now | **0.961** | **0.893** | **0.926** |
| same, held-out seeds 11 / 23 | 0.956 / 0.970 | 0.882 / 0.886 | 0.917 / 0.926 |

Trillium (Precisely) matches with parsing, Japan postal tables and hand-written linking rules (comparison routines + patterns), without learning. What gets the cascade further: surname/given name split, reading frequency (rare names count more), family-member and namesake cannot-links, a maiden-name rule, extra blocking on given name + birth date, and Fellegi–Sunter points learned on the pairs the certain rules leave open. The biggest single gain (F1 0.854 → 0.926) was a bug: EM learned "value missing" as −6 points because missing values cluster in non-matches, so pairs without evidence became cannot-links that blocked transitive merges. Missing is now no evidence (0 points, not learned). What Trillium has that the reference lacks: postal-table address parsing (chome/ban) and company-name standardization — 55% of what recall still misses is company bank accounts (`ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ` vs 株式会社山田商事) that never become candidates.

Configuring and monitoring: rules stay a YAML list on the Decision rules node (add a line, delete a line, or ask the agent); `skip: true` keeps a rule but stops applying it (`off` is a YAML 1.1 boolean, so it cannot be the key). The node's Rules in effect table is the monitor: per rule its pairs now, on that stage's previous run (⚠ for >20% and >20 pairs), and how the user's Review decisions agree with it; the text output carries the same so the agent sees shifts. Each run folder carries `rule_counts.json` and the node id, so two stages finishing in the same second no longer share a folder.

### Reading the workflow at a glance

Detail at the wrong zoom hid the story: a 35-node canvas of tables is unreadable at 40%. Now (`web/glance.js`):

- **Wires say what flows along them.** Nodes report a label per output (`ui.aws_wires`: "5,480 rows", "9,418 matched", "learned scorecard"); the canvas draws it at the wire's midpoint. A wire out of a stage card shows what the stage's inner node sends out. Columns are 200 apart to leave room for the labels.
- **Zoomed out (< 60%), each node is its title and one headline** (`ui.aws_headline`, else the first line of its text; a stage card uses its first summary line) instead of tables. In Vue nodes mode nodes are HTML above the canvas, so the headline is HTML scaled by 1/zoom; on the classic canvas ComfyUI hides DOM widgets at low quality, so it is drawn on the canvas. `ui` values must be lists: a bare string arrives split into characters.
- **Finished work is packed into stage cards automatically** at the end of an agent reply once every contract step has nodes and the latest run succeeded (the agent can unpack to edit). Run 8 had left 35 loose nodes.
- The chat footer reports the latest run, not any earlier failure in the turn.
- **Why is this one customer?** Cluster guard writes `customers.json` (each customer's records with their values from the compared pairs, the merges that joined them — the chain, one per join, strongest first — and the merges refused into it, with the reason). The **Customers** sidebar tab lists customers largest first (where a wrong merge hides), searches names, readings and ids, and opens one to its records side by side (values unlike the rest marked), *Joined by* and *Kept apart*. Read-only; deciding stays in Review.

## Architecture: one graph, everything derived (in progress)

Target: the contract holds intent only; its steps become stage subgraphs with typed ports on approval (the skeleton); types are socket types; gates are Check nodes; the API is the App mode signature. Rules over the saved workflow (`architecture.py`) say what must hold when: **structural** always (edits that break it are rejected), **completeness** before a step is verified (its to-do), **release** before publishing.

**Capability tests** (`tests/e2e/capabilities/`, run first by `scripts/e2e.sh`, results in `tests/e2e/capabilities.json`; ComfyUI 0.38.0, frontend 1.54.12), all passing in classic and Vue nodes mode:
- `stage_skeleton`: an empty subgraph with typed ports is made, wired from its ports and nested from code, survives save/reload, refuses a wrong-type wire, and runs end to end with a passthrough stub inside.
- `generic_sockets`: `MatchType` with our types follows the input through a chain, refuses wrong types, accepts unions; the backend runs it.
- `output_in_nested_stage`: an output node two stages deep reports under `outer:inner:node` and reaches the canvas node — Check nodes can drive status.
- `app_signature`: App mode's `extra.linearData` survives save but holds bare node ids, ambiguous inside stages. **Decision:** the API signature names top-level interface nodes only.
- `known_pitfalls`: `ui` values are lists (a bare string arrives as characters); Vue nodes mode renders nodes as DOM above the canvas.

**Measurement before enforcing** (workflows on 8188): the reference and run 9 (organized) break no structural rule; each has 20 untyped `DICT` stage ports, 10 stages without a Check, and no signature — the to-do the next steps remove. Runs 7 and 8 (not organized) have 26 and 31 nodes outside any stage: the structural problem stage-scoped tools make impossible. Lesson: "has an output node" is not a check — every table node is an output node to show its table.

**Typed sockets (done).** Matching data is typed: `TABLE` (records), `PAIRS` (compared, not decided), `DECISIONS` (matched / different), `CUSTOMERS` (records grouped). The value is unchanged (the run-folder record); only the socket says what it holds, so ComfyUI refuses e.g. an unsettled branch into Cluster guard. Typed inputs also accept `DICT` — an Agent Tool's result, the escape hatch for agent-written steps — and the Agent Tool accepts all of them. **Stub** (`MatchType`) stands in for a stage not built yet and passes its data on, typed as its input. The reference: untyped stage ports 20 → 0, F1 unchanged (0.926); ports into union inputs read `DECISIONS,DICT`.

**Stages are the steps (done).** The graph is now the single structure:
- *Contract → skeleton.* A step may declare typed ports: `gives` (port → socket type) and `takes` (an earlier step's `step.port`, or a type for data entering), and a gate `check` on one of its outputs. Approval builds the skeleton (`buildSkeleton`): one stage per step, numbered by dependency depth (independent steps share a number and a column — lanes), wired, a Stub passing data through, a Check per gate, committed as a version. A revised contract adds stages and ports; a removed step's stage is marked orphaned, never deleted; a retyped port is reported, not rewired.
- *Edits are stage-scoped.* Every operation names its `stage`; inside, `input`/`output` are the stage's ports by name. `add` without a stage is refused, so nodes outside stages cannot happen; stages cannot be removed except by revising the contract. A Check follows whatever feeds the output port it watches. Removing a Stub needs no confirmation.
- *Gates are Check nodes.* `Check` judges the value a stage gives (rows at least, non-empty, contains, equals, regex, images) and reports pass/fail with evidence, never raising. A step is verified when the Checks in its stage pass on the latest run of exactly the current graph (outputs keyed `stage:node`).
- *Derived, not stored.* A node's step is the stage it sits in; tags, step groups and organize/unpack are gone. Status, drift (loose nodes, orphaned stages, port types differing from the contract) and the to-do list (`architecture.py`: untyped ports, stages without a Check, no signature) come from the saved workflow. Versions diffs look inside stages.
- The reference workflow meets every structural and completeness rule (a Check per stage); only the release signature remains, for the API step.
- *Real-agent check.* Run 11 (first on stages): all 10 stages built in their lanes, nothing loose, aligned — but 50 tiny edit batches (remove the Stub, add, wire, connect the output, wire the Check by hand; re-wiring stages the skeleton had already wired was refused as destructive) used up the 60 turns before a run. Fix in the tools, not the prompt alone: scaffolding manages itself (a Stub that no longer feeds an output disappears, a Check follows its output, replacing scaffold wires needs no approval, wiring what is already wired is a no-op) and the approval result lists each stage's ports and contents. Run 12: PASS, 9/10 steps verified (review has no check by design), F1 0.919 (P 0.939, R 0.900, best fair run), 270 s, 32 tool calls (12 edit batches) — half the calls and the fastest run so far; its workflow breaks no structural rule and has no untyped port.

### Closing the gaps (2026-10-20)

- **Workflow as an API (R16).** The signature is App mode's own selection (`extra.linearData`): inputs are widgets on top-level nodes (given data such as a folder), outputs are top-level stages; the agent sets it with the `signature` edit, the user publishes from the Contract tab. Publishing refuses until structural and release rules hold, commits a version, and freezes the API-format prompt with the workflow (`<agent-projects>/published/<name>.json`). `POST /agent-workflow-studio/api/<name>/run {"inputs": {…}}` checks input types, runs through ComfyUI's own queue (the workflow rides along as run metadata, so nodes find their project), waits and returns each output stage's results, the version and the run id; `GET …/openapi.json` describes it; `GET /agent-workflow-studio/api` lists them. Local access only; tokens come with remote callers.
- **Hardening proposed by the agent (R3).** `comfy_harden_step` asks the user to freeze a fuzzy step's latest plan into fixed nodes inside its stage; the approval is the user's version.
- **Open questions on a stage (R13).** An open question tied to one step (`{question, step}`) no longer blocks approval: that stage says it is waiting, can fail but cannot be verified until the answer arrives as a contract amendment; untied questions still block.
- **Existing work.** A user's own nodes from before any contract are adopted by a step (`adopts: [ids]`) and become its stage with their wiring kept.
- **Other domains (R18).** `tests/eval/scenarios/domains.json` (built by `build_domains.py`, answers from the clean truth): sales report from dirty orders, multilingual feedback triage (LLM), invoice extraction from four layouts, a data-quality report; the harness places input files and checks expected rows in the named result file.
- **Scale and onboarding.** The Customers tab is searched and paged on the server. The installer installs the pinned Pi CLI (`versions.lock`) privately when none is on PATH, and `run-local.sh` uses it.
- **Visible nodes for everyday tables.** Outside record matching the agent wrote code for reporting and checks (sales report: two Agent Tools; data quality: one), because the table nodes lacked them. Added *Parse numbers*, *Remove duplicates*, *Compute column* (a small expression language parsed by hand — arithmetic on numbers only, a few functions, no attributes, calls or `eval`), *Summarize (group by)* and *Data checks* (readable rules: `missing_phone: phone is empty`, `dup_id: id is duplicate`). Rerun: the sales report is nine visible node kinds in four verified stages, the data-quality report Read table → Data checks → Write table; invoice extraction from free-form layouts stays an approved agent tool (the marked exception).
- **Evals (deepseek-v4-flash, isolated instances): 10/10.** uppercase, join, edit_existing (the user's nodes adopted into a stage), image, fuzzy_llm, fuzzy_node, sales_report, feedback_triage, invoice_extract, data_quality — all in stages, aligned, steps verified.
- **The reference workflow is an API.** Its signature: input `seed` (the Synthetic data card's promoted widget — publishing follows it to every node inside it feeds), output the *8 · Publish results* stage. It breaks no architecture rule (structural, completeness or release). Published on 8188 as `nayose`; `curl -X POST …/agent-workflow-studio/api/nayose/run -d '{"inputs": {"seed": 11}}'` ran the frozen workflow on a fresh dataset (13,977 mentions → nayose_result.csv, its Check passing) at version f68acd31.

### Plan → contract → HLD → LLD (2026-10-20)

One artifact gains resolution. **L0 intent** (requests, examples, promises, decisions) and **L1 HLD** (per step: `takes` → purpose `using` `uses` → `gives` → consumers, `because` `why`, with a `pattern`; `data` entities with key, fields and refs) live in the contract and are approved together: the approval screen renders the HLD sentences, the data and a **design review** (typed outputs, every result checked, how and why, a pattern per step, every hand-off a named entity, the API defined, an example per must-request, AI steps behind a check). **L2 LLD** is the stages' insides. **L3 evidence** is runs, Check results (kept with the workflow by fingerprint, so status survives a restart), acceptance records and versions.

- *Derived, so it cannot drift:* the skeleton builds stages titled from the HLD, the API's input nodes from promised inputs, the signature from promised outputs' `from`, an API output node per promised output, a Check per gate; cards show the HLD sentence; wires out of stages show the entity name; the Data tab shows the ERD with fields as seen.
- *Prevented:* the bridge receives the approved wiring with every agent edit and refuses an undeclared wire between stages, removing a designed wire or an API input ("propose an amended contract"). An amendment marks changed stages stale until their Checks pass. Declared fields a run did not carry are drift.
- *Patterns:* a design-patterns skill (architecture patterns: pipes and filters, medallion, matching cascade, report, extraction, triage, data-quality gate, synthetic test data; step patterns from enterprise integration; principles) the agent designs from and cites in decisions.
- *API-native:* examples run as acceptance tests through the API path (`comfy_run_examples`, Contract tab) and gate publishing on exactly that graph; semantic versions (API → major, design → minor, else patch), pinned calls (`name@1`), jobs for long runs, written files as download links, OpenAPI with jobs.
- *Nodes say what they changed* ("3,852 values changed, e.g. name: 髙橋 → 高橋"); wires are labelled only where the amount changes.

### Loop engineering, test data, following a record (2026-10-20)

Agentic workflows are improved in loops; an improvised loop drifts, repeats failed ideas and grades its own work. The studio engineers it (`loop.py`):

- *Verifiable goal:* every step verified by its Checks on the latest run of exactly this graph, and every contract example passing as an acceptance test. Unrun examples count as not passing.
- *Journal:* `comfy_loop_step` opens an iteration with a hypothesis and the planned change; the next status closes it with the measured state and a verdict the studio computes (improved / worse / same / not measured). Each iteration is a record in the project repo, so the Contract tab shows the timeline and tried ideas are not retried.
- *Controller:* deterministic — stop when the goal is met, revert when the last change made things worse (until restored), ask the user after `stall` iterations without progress or when the `iterations` budget is spent (defaults 6 and 2; the contract's `loop` overrides).
- *Outer loop:* a published API can be watched: its examples run on a schedule and a failure is recorded.

Test data: `Generate records` (recipe, seed) → `Damage values` (recipe; outputs the damaged rows, the truth and a change log) → the process → `Compare to truth` (accuracy per column) → a Check `at_least`.

Following a record: every table node remembers its last output table by node id; the Data tab searches them and lists, in run order, each step that had the record with only the values that changed, and where it disappeared. Decision rules send a fixed (hash-ordered) sample of each branch's automatic decisions to review; the agreement table turns reviews into a 95% lower bound of how often each branch is right.

Customer ids are stable: the cluster guard gives each customer the id most of its records had on the last run (larger customers first) and new ids only to new customers, and its headline says how many kept their id, are new or gone, and how many records moved.

The design can be exported as one Markdown page (Contract tab → Export design) to share: request, HLD, data, each stage, decisions, review, status, loop and API.

