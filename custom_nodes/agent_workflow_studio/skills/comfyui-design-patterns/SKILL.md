---
name: comfyui-design-patterns
description: Design a workflow like an engineer before building it — pick the architecture pattern for the job, name the data entities, apply engineering principles to every step, record the choices as decisions, and make it an API by design. Use while planning (the HLD) and when amending a design.
---
# Designing a workflow: patterns, principles, API by design

Plan → contract → HLD → LLD. While drafting the HLD, choose a **pattern** for the whole job and for each step, name the **data entities**, and check the **principles** below. Record each significant choice as a decision (`decision`, `why`, `instead_of`, `pattern`, `principle`) — that is the design's audit trail (an ADR). Do not invent patterns; pick from these, adapt, and say why.

## Architecture patterns (pick one for the job)

| Pattern | Use when | Stages | Entities | Typical checks |
|---|---|---|---|---|
| **Pipes and filters** | Any transform chain | read → clean → transform → write | the record type at each hand-off | rows in ≈ rows out, required fields filled |
| **Medallion (raw → clean → curated)** | Several messy sources become one trusted set | one lane per source (raw→clean) → merge → curate → publish | raw rows, cleaned records, curated output | each source contributes; no row lost silently |
| **Matching cascade (entity resolution)** | Same thing recorded many ways (名寄せ, dedup) | profile → lanes → find candidates (blocking) → certain rules → learned scoring → human review → consolidate → publish | mention, pair, decision, customer | every source linked; largest group bounded; no customer with two birth dates |
| **Report (aggregate)** | Totals / KPIs from transactions | read → clean → derive columns → summarize → publish | transaction, summary | totals equal the sum of parts; groups complete |
| **Extraction** | Fields out of free text / documents | read → extract (rules or LLM) → validate → publish | document, extracted record | required fields present; formats valid |
| **Classification / triage** | Route items to categories | read → classify (rules or LLM) → review band → publish | item, label | every item labelled; labels in the allowed set |
| **Data quality gate** | Check data before use | read → data checks → report (→ block or pass) | record, check result | counts per rule; examples per rule |
| **Synthetic test data** | Test a process with known answers | entities → records per source → damage (readable recipe) → answer key kept hidden | entity, record, answer | damage rates as asked; key never reaches the agent |

## Step patterns (name one per step)

- **Message translator** — normalize, map values, parse dates/numbers (one kind of fix per node).
- **Content-based router** — Decision rules branches (matched / different / passed on).
- **Splitter / aggregator** — Stack roles; Summarize.
- **Idempotent receiver** — Remove duplicates by key, so re-runs and re-sends do not double count.
- **Review queue (dead letter)** — what automation cannot decide goes to the user, visibly.
- **Claim check** — large data travels as files in run folders; wires carry small handles.
- **LLM behind a typed boundary** — a fuzzy step takes and gives typed data and sits behind a Check; harden it later (strangler fig).

## Principles (check every step)

- **Single responsibility** — one step, one transformation the user would name.
- **Typed boundaries / design by contract** — `takes` and `gives` typed; a Check is the postcondition.
- **Determinism and reproducibility** — seeds for randomness, pinned scorecards, no hidden state; the same input gives the same output.
- **Idempotency** — running twice changes nothing more.
- **Fail visibly** — problems go to Checks, Data checks or the review queue, never silently dropped.
- **Least privilege and privacy** — customer data stays on the machine; LLM steps use local models for real records.
- **Simplicity (KISS / YAGNI)** — no stage, option or entity nobody asked for.

## API by design

The contract's promised inputs and outputs are the workflow's API. Give every promised output a `from` (`"step.port"`), use every promised input in a step's `takes` (`"input.<name>"`), and write each must-request as an example (`given` uses the promised input names, `expect` the promised output names): the examples become acceptance tests that must pass before publishing. Entities named in `data` are the API's data schemas.

## Before proposing: the design review

The contract tool returns the review; fix every ✗ you can before the user sees it:
every step gives typed data · every result is checked on its data · every step says how (`uses`) and why · every step names its pattern · every step reads as one short sentence (≤ 220 characters: purpose, uses and why are a few words each) · every hand-off is a named entity · the API is defined · every must-request has an example · AI steps sit behind a check.
