---
name: comfyui-workflow-contracts
description: Write, follow and amend the workflow contract (intent), its design (HLD, steps as typed stages with gates) and each stage's LLD; use step states, checks and the to-do list to work efficiently.
---
# Workflow contracts

Every workflow you build or extend, and every set of files you produce for the user, has three layers, each approved or checked in turn and stored in the workflow itself: the **contract** (intent, `comfy_propose_contract`), the **design** (HLD, `comfy_propose_design`: each step a stage node) and each stage's **LLD** (`comfy_build_step`: the nodes inside, each tagged with the HLD clause it realizes). They trace to each other in one vocabulary: a stage *serves* requests (its `covers`), a node *realizes* a part of its stage (`takes <port>`, `does`, `gives <port>`), and a Check or an example *verifies* it. Step status is derived from the canvas and runs, never stored. The user may be non-technical: write every plain field so they can check it at a glance.

## Writing the contract (`comfy_propose_contract`): what the user wants

- **purpose**: 1-3 sentences: who it is for, what problem it solves, what good looks like.
- **requests**: one per thing the user wants. `title`: a few words naming it (≤ 40 characters, e.g. `Own cleaning lane per system`): the user sees it on the canvas wherever the request is referenced, so it must make sense alone. `you_said` quotes the user's words exactly; `we_understood` restates it as "When <situation>, the workflow <does what>." Priority `must` / `should` / `could`. Split compound wishes into separate requests.
- **Nuance**: turn subjective wishes ("friendly tone", "looks good") into `examples`, or into a gate the user confirms. Contradictions and unknowns go to `open_questions` and are asked with `comfy_ask_user` before proposing. A question that only one step depends on, while the rest can be built, may stay open tied to that step (`{"question": …, "step": …}`): that stage shows it is waiting and cannot be verified until the user answers (amend the contract with the answer as a decision). Any other open question blocks approval. Things the user wants later go to `not_now`.
- **examples**: concrete `given` → `expect` pairs using the promised input/output names. Prefer 2-4 that show the edges (empty input, unusual case), not just the happy path.
- **promises**: the inputs the user provides and the outputs they get, each with a kind (text, image, ...) and ideally an example value.
- **decisions** here are the user's (`by: user`); yours go in the design.

## Writing the design (`comfy_propose_design`): how it is done

- **outputs**: which step port delivers each promised output: `{"<output name>": "<step>.<port>"}`.
- **steps** (at most 12, in data-flow order): a plain `purpose`, `covers` the requests it serves, and an `approach`:
  - `fixed`: installed deterministic nodes do it (search the catalog first and name them in `nodes`).
  - `ai_planned`: a text step whose exact operations are unclear: a **Fuzzy · Plan with nodes** node.
  - `ai_judgment`: classification, extraction, rewriting: a **Pi CLI · Structured LLM** node with an output schema.
  - Data jobs are pipelines: make each stage its own step (load and clean, find candidates, compare and score, decide, review, report), each built as its own Agent Tool or nodes, so the canvas shows how the result was made. Two steps for a whole matching or analytics job hides the work. Matching and nayose jobs mirror the architecture, one step each, with the step ids from the agent guide: profile_data → one cleansing lane per source system → find_candidates → deterministic_matching → probabilistic_matching → your_review → consolidate_customers → publish_results; decided pairs leave each matching stage through its matched/different branches straight to consolidation. The decision is never logic buried in a tool, so the user can read and edit how pairs are decided and see the whole decision tree.
- **existing work**: if the canvas already holds the user's own nodes (not `given`), a step `adopts` them by id: on approval they become that step's stage with their wiring kept, and you edit them there.
- **ports**: every step is a stage with typed ports. `gives` names its outputs and their socket types (`TABLE`, `PAIRS`, `DECISIONS`, `CUSTOMERS`, `STRING`, `IMAGE`, …; `*` while unknown); `takes` names its inputs, each an earlier step's output (`"profile_data.folder"`) or a type for data entering the workflow. Steps that take from the same step are parallel lanes. On approval the canvas gets this skeleton, wired.
- **gate** on every step: `says` how we know it worked, in plain words. Add a `check` whenever data can prove it: `{"output": <a port in gives>, "kind": rows_at_least | non_empty | contains | equals | regex | image_count_at_least, "value": …}`. It becomes a Check node on that output inside the stage; the step is verified when its Checks pass on the latest run. The check must test that the data is **right**, not only that it exists: `non_empty` or `rows_at_least 1` is refused by the design review. Check the expected count (`rows_at_least 1000`), a value it must hold (`contains`, `regex`, `equals`), or a measured number the step outputs (`at_least`).
- **decisions**: your design choices, one line each with `why` and, if useful, `instead_of` (`by: agent`).
- Every `must` and `should` request needs a covering step; otherwise move it to `not_now` (amend the contract). Keep it short: one line per entry.

## Building against the contract

- The promises are the workflow's App: the design's approval adds a top-level input node per promised input and a top-level output node per promised output (fed by the stage that delivers it) and sets ComfyUI's App mode to them. A promised output's value is shown in App mode as a file (CSV for a table, JSON for data, text), and the API returns it by name. The Contract tab reports any difference between the promises and App mode as a gap.
- The design's approval creates one stage per step, with its typed ports, a Stub passing data through and a Check per gate. Build each stage with `comfy_build_step` (its LLD): nodes keyed by short names, each with its `type`, its settings in `set` and what it `realizes` of the step (`takes <port>`, `does`, `gives <port>`, `why`, `decision <id>`), and wires from `input.<port>` through `<node>.<socket>` to `output.<port>`. The studio refuses an LLD that leaves a clause unrealized or a port unwired. A node's `promote` (a list of its setting names) puts the step's key knobs on the stage card (thresholds, limits, keys), so the user changes them without opening the stage; promote the few settings a person tunes, not every setting. The Stub disappears and the Check follows by itself; stages are already wired to each other.
- Inside a stage, `comfy_edit_workflow` changes settings, titles and positions only; to change nodes or wiring, build the stage again. Each build returns a `graph_check` with drift and a to-do list; fix every problem before running and work through the to-dos.
- If an output does not fit an input, use `comfy_find_converter` rather than rewiring around it.

## Step states (derived, never written)

| State | Means | Next |
| --- | --- | --- |
| planned | the stage holds only its Stub and Checks | build it |
| built | real nodes in the stage, its Checks not yet passed on this graph | run, then `comfy_check_gates` |
| fuzzy | built with an unhardened Fuzzy node | run and check; tell the user they can harden it once the plan is right |
| verified | its gate passed on the latest run | nothing |
| failing | its gate failed on the latest run | fix the smallest part of the step, run, re-check |

Any structural edit resets gate outcomes, so verified steps return to built until re-checked. Use `comfy_contract_status` to see every step's state, nodes outside every stage and the to-do list; report progress to the user as "N of M steps verified", together with its `gaps` (must-haves not yet proven by an example on this version, Checks that differ from the agreed one): the user sees the same list in the Contract tab.

## The workflow's CI

Every workflow is an app with its own tests, stored in it and versioned with it: its examples (acceptance: the contract), each stage's Checks (integration: the design), and the LLD rules on every stage (design: ports wired, every part realized, no node that traces to nothing). `comfy_run_examples` runs them all on exactly this graph, plus a regression check against the last release, and keeps the run as a version. Run it before you report the work done; report its summary as it is and fix what fails. The user publishes only a green run; you never publish.

## Quality gates for data jobs

Gates that only prove a file exists say nothing about quality. Add at least one gate per data job that a bad result fails, computed without any answer key: e.g. "every source has ≥ 50% of its records linked", "no customer groups more than 30 records", "no customer has two different birth dates", "review band ≤ 2,000 pairs". Have the step output the measured values (a text line or a count) so a Check can test them and the user can see them.

## When the work and the contract disagree

Every structural edit's `graph_check` also reports the contract: `aligned`, or `drift` with reasons (nodes outside every stage, a stage whose step is gone, a port whose type differs from the contract, a fixed step using a Fuzzy node, graph problems). Resolve every drift before running: move or remove stray nodes, retype or rewire the port, or amend the design or the contract.


If a request turns out to be impossible, wrong or incomplete, do not silently build something else. Explain it, ask if needed, and propose an amendment: the whole contract again with `comfy_propose_contract`, or the whole design again with `comfy_propose_design`, with a decision recording the change. The user approves amendments like the first version.

## Working efficiently

- Ask all questions you need in one `comfy_ask_user` call; do not ask about details you can decide sensibly (record them as agent decisions).
- Search the catalog and read schemas before proposing the design, so its steps can be built from real nodes.
- Do not re-read the canvas after every edit: the edit result and its `graph_check` already describe the change.
- Check gates once per run, not repeatedly. A gate that reports `no_output` means its Check did not run: connect what feeds the stage's output.
- Workflows execute backwards from output nodes; a node that reaches no output never runs.
