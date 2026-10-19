# Requirements

What Agent Workflow Studio must be, in the owner's terms, with how each is checked and where it
stands. Status: ✅ met · 🟡 partly · ⬜ open. DESIGN.md says how; this file says what and why.

## 1. Purpose

**R1 · An agent that builds real workflows with you, on the canvas.** A Pi-backed agent sees the
live ComfyUI graph, plans with you, then creates, edits, runs and debugs the workflow itself.
*Check:* real-agent evals reach every contract step verified. ✅ (nayose runs 9–10: 10/10)

**R2 · The chat is the meta layer.** Planning, orchestration and debugging happen in the chat;
the work itself is the graph. ✅

**R3 · Fuzzy work hardens into deterministic work.** Steps start as LLM / agent-written logic where
needed and are hardened into deterministic, visible nodes over time. ✅ (the agent proposes hardening
with `comfy_harden_step`, you approve; reporting, cleaning and checks have visible nodes)

## 2. Transparency (the core value)

**R4 · No black boxes.** Every step's logic is visible on the canvas as nodes and settings a person
can read; agent-written code is the marked exception and must explain itself. ✅

**R5 · The graph mirrors the architecture.** Stages are named after the process (profile data →
cleanse per source → find candidates → deterministic → probabilistic → your review → consolidate →
publish), source systems are parallel lanes, and branches (matched / different / unsettled) are
real wires. ✅ (reference workflow; agents follow it)

**R6 · Everything is visible in ComfyUI, at every zoom.** Data in → out, samples, decision trees,
per-rule counts; stage cards on top, proper DAGs inside; wires say what flows; zoomed out each node
reads as one line. ✅

**R7 · Auditable decisions.** For any result you can see why: each customer's records, the chain
of matches that joined them, and what was kept apart; every decision's rule and evidence. ✅

**R8 · Everything is versioned and reversible.** Every change (agent or user) is a git commit in the
workflow's own local repository, with history, diffs and restore. ✅

## 3. Architecture (native, typed, derived)

**R9 · ComfyUI-native.** Built on ComfyUI's own mechanisms (nodes, typed sockets, subgraphs, App
mode, the run queue), no parallel workflow format. ✅

**R10 · One graph, everything derived.** The contract holds intent only; its steps become stage
subgraphs with typed ports on approval; status, cards, labels, to-dos and the API are derived from
the graph, never stored twice. ✅

**R11 · Typed end to end.** Data between stages is typed (TABLE, PAIRS, DECISIONS, CUSTOMERS, ...);
ComfyUI refuses wrong wires; generic steps keep the type they are given. ✅

**R12 · The architecture is enforced by the tools, not the prompt.** The agent edits inside stages;
work outside the structure is impossible; tool results carry the to-do list. ✅

**R13 · Work in progress is a legal state.** Rules apply by maturity: structural always,
completeness before a step is verified, release before publishing; stubs, untyped ports and open
questions are to-dos, not errors. ✅

**R14 · Gates prove steps on the data.** A step is verified when its checks pass on the latest run
of exactly the current graph. ✅

**R15 · Platform assumptions are tested.** Every ComfyUI behaviour the design relies on has a
capability test with its fallback, run before everything else. ✅

## 4. Use

**R16 · A workflow is an API.** A finished workflow is published at a fixed version and called with
data in and data out; its signature is App mode's inputs/outputs. ✅ (local callers; tokens for remote ones later)

**R17 · Logic is easy to configure and monitor.** Rules are added, removed or skipped in plain
text (or by asking the agent); each rule's counts, change since the last run and agreement with
your reviews are shown. ✅

**R18 · Standalone.** Someone else can install it and have the agent build a transparent workflow
for their own problem, not only record matching. ✅ (10/10 evals incl. sales report, feedback triage,
invoice extraction, data quality; installer brings the Pi CLI) — open: a second model, a license

## 5. The record-matching (名寄せ) benchmark

**R19 · Better than deterministic matching.** On realistic dirty Japanese insurance data (OCR,
typos, missing values, old addresses, maiden names, partial updates, family members, look-alikes),
beat a Trillium-style pattern grid; target precision ≥ 0.95 and recall ≥ 0.85, confirmed on
held-out data. ✅ (reference P 0.961 / R 0.893; held-out 0.956/0.882 and 0.970/0.886; grid F1 0.583)

**R20 · Fair evaluation.** The answer key never reaches the agent; scores are measured, never tuned
against. ✅

## 6. Safety and how we work

**R21 · Credentials never leak.** Never in node inputs, workflows, logs, command arguments or run
records; keychain secrets are never printed. ✅

**R22 · Customer data stays on the machine.** LLM help on real records uses local (loopback) models
only. ✅

**R23 · Agent-written code is approved and confined.** You see and approve the exact code; it runs
sandboxed, reading only its inputs. ✅

**R24 · Tests never touch your instance.** Tests run in isolated ComfyUI instances; on 8188 only the
studio's own templates and eval workflows are written. ✅

**R25 · Verified end to end with the real agent.** After changes, real-agent runs on 8188 are
inspected for process and outcome, not only unit tests. ✅ (practice)

**R26 · Plan with small tests first.** Difficult work starts with research and small experiments
(now capability tests). ✅ (practice)
