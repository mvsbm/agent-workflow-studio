---
name: comfyui-workflow-contracts
description: Write, follow and amend the workflow contract (requests, examples, steps as typed stages, gates, decisions), build inside the stages, and use step states, checks and the to-do list to work efficiently.
---
# Workflow contracts

Every workflow you build or extend, and every set of files you produce for the user, has one contract, approved by the user and versioned as `contract.yaml`. It records intent only; step status and technical details are derived from the canvas and runs. The user may be non-technical: write every plain field so they can check it at a glance.

## Writing the contract (`comfy_propose_contract`)

- **purpose**: 1-3 sentences: who it is for, what problem it solves, what good looks like.
- **requests**: one per thing the user wants. `you_said` quotes the user's words exactly; `we_understood` restates it as "When <situation>, the workflow <does what>." Priority `must` / `should` / `could`. Split compound wishes into separate requests.
- **Nuance**: turn subjective wishes ("friendly tone", "looks good") into `examples`, or into a gate the user confirms. Contradictions and unknowns go to `open_questions` and are asked with `comfy_ask_user` before proposing. A question that only one step depends on, while the rest can be built, may stay open tied to that step (`{"question": …, "step": …}`): that stage shows it is waiting and cannot be verified until the user answers (amend the contract with the answer as a decision). Any other open question blocks approval. Things the user wants later go to `not_now`.
- **examples**: concrete `given` → `expect` pairs using the promised input/output names. Prefer 2-4 that show the edges (empty input, unusual case), not just the happy path.
- **promises**: the inputs the user provides and the outputs they get, each with a kind (text, image, ...) and ideally an example value.
- **steps** (at most 12, in data-flow order): a plain `purpose`, `covers` the requests it serves, and an `approach`:
  - `fixed`: installed deterministic nodes do it (search the catalog first and name them in `nodes`).
  - `ai_planned`: a text step whose exact operations are unclear: a **Fuzzy · Plan with nodes** node.
  - `ai_judgment`: classification, extraction, rewriting: a **Pi CLI · Structured LLM** node with an output schema.
  - Data jobs are pipelines: make each stage its own step (load and clean, find candidates, compare and score, decide, review, report), each built as its own Agent Tool or nodes, so the canvas shows how the result was made. Two steps for a whole matching or analytics job hides the work. Matching and nayose jobs mirror the architecture, one step each, with the step ids from the agent guide: profile_data → one cleansing lane per source system → find_candidates → deterministic_matching → probabilistic_matching → your_review → consolidate_customers → publish_results; decided pairs leave each matching stage through its matched/different branches straight to consolidation. The decision is never logic buried in a tool, so the user can read and edit how pairs are decided and see the whole decision tree.
- **existing work**: if the canvas already holds the user's own nodes (not `given`), a step `adopts` them by id: on approval they become that step's stage with their wiring kept, and you edit them there.
- **ports**: every step is a stage with typed ports. `gives` names its outputs and their socket types (`TABLE`, `PAIRS`, `DECISIONS`, `CUSTOMERS`, `STRING`, `IMAGE`, …; `*` while unknown); `takes` names its inputs, each an earlier step's output (`"profile_data.folder"`) or a type for data entering the workflow. Steps that take from the same step are parallel lanes. On approval the canvas gets this skeleton, wired.
- **gate** on every step: `says` how we know it worked, in plain words. Add a `check` whenever data can prove it: `{"output": <a port in gives>, "kind": rows_at_least | non_empty | contains | equals | regex | image_count_at_least, "value": …}`. It becomes a Check node on that output inside the stage; the step is verified when its Checks pass on the latest run.
- **decisions**: one line each with `why` and, if useful, `instead_of`. `by: user` only for answers the user actually gave; your own choices are `by: agent`.
- Every `must` and `should` request needs a covering step; otherwise move it to `not_now`. Keep the contract short: one line per entry.

## Building against the contract

- Approval creates one stage per step, with its typed ports, a Stub passing data through and a Check per gate. Build inside each stage (`"stage": "<step id>"` on every operation): real nodes wired from the input ports to the output ports, one stage per batch. The Stub disappears and the Check follows by itself; stages are already wired to each other. Nodes cannot be added outside a stage.
- One edit batch per stage (or several stages) is right; tiny batches waste turns. Each structural edit returns a `graph_check` with drift and a to-do list; fix every problem before running and work through the to-dos.
- If an output does not fit an input, use `comfy_find_converter` rather than rewiring around it.

## Step states (derived, never written)

| State | Means | Next |
| --- | --- | --- |
| planned | the stage holds only its Stub and Checks | build it |
| built | real nodes in the stage, its Checks not yet passed on this graph | run, then `comfy_check_gates` |
| fuzzy | built with an unhardened Fuzzy node | run and check; tell the user they can harden it once the plan is right |
| verified | its gate passed on the latest run | nothing |
| failing | its gate failed on the latest run | fix the smallest part of the step, run, re-check |

Any structural edit resets gate outcomes, so verified steps return to built until re-checked. Use `comfy_contract_status` to see every step's state, nodes outside every stage and the to-do list; report progress to the user as "N of M steps verified".

## Quality gates for data jobs

Gates that only prove a file exists say nothing about quality. Add at least one gate per data job that a bad result fails, computed without any answer key: e.g. "every source has ≥ 50% of its records linked", "no customer groups more than 30 records", "no customer has two different birth dates", "review band ≤ 2,000 pairs". Have the step output the measured values (a text line or a count) so a Check can test them and the user can see them.

## When the work and the contract disagree

Every structural edit's `graph_check` also reports the contract: `aligned`, or `drift` with reasons (nodes outside every stage, a stage whose step is gone, a port whose type differs from the contract, a fixed step using a Fuzzy node, graph problems). Resolve every drift before running: move or remove stray nodes, retype or rewire the port, or amend the contract.


If a request turns out to be impossible, wrong or incomplete, do not silently build something else. Explain it, ask if needed, and propose an amended contract with `comfy_propose_contract` (the whole contract again, with a decision recording the change). The user approves amendments like the first version.

## Working efficiently

- Ask all questions you need in one `comfy_ask_user` call; do not ask about details you can decide sensibly (record them as agent decisions).
- Search the catalog and read schemas before proposing, so the contract names real nodes.
- Do not re-read the canvas after every edit: the edit result and its `graph_check` already describe the change.
- Check gates once per run, not repeatedly. A gate that reports `no_output` means its Check did not run: connect what feeds the stage's output.
- Workflows execute backwards from output nodes; a node that reaches no output never runs.
