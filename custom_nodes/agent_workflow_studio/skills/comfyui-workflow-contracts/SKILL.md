---
name: comfyui-workflow-contracts
description: Write, follow and amend the workflow contract (requests, examples, steps, gates, decisions), tie nodes to steps, and use step states and graph checks to work efficiently.
---
# Workflow contracts

Every workflow you build or extend has one contract, approved by the user and versioned as `contract.yaml`. It records intent only; step status and technical details are derived from the canvas and runs. The user may be non-technical: write every plain field so they can check it at a glance.

## Writing the contract (`comfy_propose_contract`)

- **purpose**: 1-3 sentences: who it is for, what problem it solves, what good looks like.
- **requests**: one per thing the user wants. `you_said` quotes the user's words exactly; `we_understood` restates it as "When <situation>, the workflow <does what>." Priority `must` / `should` / `could`. Split compound wishes into separate requests.
- **Nuance**: turn subjective wishes ("friendly tone", "looks good") into `examples`, or into a gate the user confirms. Contradictions and unknowns go to `open_questions` and are asked with `comfy_ask_user` before proposing; never approve with open questions. Things the user wants later go to `not_now`.
- **examples**: concrete `given` → `expect` pairs using the promised input/output names. Prefer 2-4 that show the edges (empty input, unusual case), not just the happy path.
- **promises**: the inputs the user provides and the outputs they get, each with a kind (text, image, ...) and ideally an example value.
- **steps** (at most 12, in data-flow order): a plain `purpose`, `covers` the requests it serves, and an `approach`:
  - `fixed`: installed deterministic nodes do it (search the catalog first and name them in `nodes`).
  - `ai_planned`: a text step whose exact operations are unclear: a **Fuzzy · Plan with nodes** node.
  - `ai_judgment`: classification, extraction, rewriting: a **Pi CLI · Structured LLM** node with an output schema.
- **gate** on every step: `says` how we know it worked, in plain words. Add a `check` whenever an output can prove it; `node` must be the exact title of an **output node** (Preview, Save, ...) because other nodes produce no run outputs. Good gate places are the checkpoints `comfy_analyze_graph` reports; to prove an intermediate value, add a Preview titled for the gate.
- **decisions**: one line each with `why` and, if useful, `instead_of`. `by: user` only for answers the user actually gave; your own choices are `by: agent`.
- Every `must` and `should` request needs a covering step; otherwise move it to `not_now`. Keep the contract short: one line per entry.

## Building against the contract

- Give every node you `add` the `step` id it belongs to, and the titles the gates name. Untagged nodes show up as work outside the contract.
- One edit batch per step is usually right. Each structural edit returns a `graph_check`; fix every problem it lists before running.
- If an output does not fit an input, use `comfy_find_converter` rather than rewiring around it.

## Step states (derived, never written)

| State | Means | Next |
| --- | --- | --- |
| planned | no nodes tagged for the step yet | build it |
| built | nodes exist, gate not yet checked | run, then `comfy_check_gates` |
| fuzzy | built with an unhardened Fuzzy node | run and check; tell the user they can harden it once the plan is right |
| verified | its gate passed on the latest run | nothing |
| failing | its gate failed on the latest run | fix the smallest part of the step, run, re-check |

Any structural edit resets gate outcomes, so verified steps return to built until re-checked. Use `comfy_contract_status` to see every step's state and the nodes outside any step; report progress to the user as "N of M steps verified".

## When the work and the contract disagree

If a request turns out to be impossible, wrong or incomplete, do not silently build something else. Explain it, ask if needed, and propose an amended contract with `comfy_propose_contract` (the whole contract again, with a decision recording the change). The user approves amendments like the first version.

## Working efficiently

- Ask all questions you need in one `comfy_ask_user` call; do not ask about details you can decide sensibly (record them as agent decisions).
- Search the catalog and read schemas before proposing, so the contract names real nodes.
- Do not re-read the canvas after every edit: the edit result and its `graph_check` already describe the change.
- Check gates once per run, not repeatedly. A gate that reports `no_output` points at a non-output node: fix the contract or the title, not the run.
- Workflows execute backwards from output nodes; a node that reaches no output never runs.
