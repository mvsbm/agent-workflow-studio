# Requirements

What Agent Workflow Studio must be, in the owner's terms, with how each is checked and where it
stands. Status: ✅ met · 🟡 partly · ⬜ open. DESIGN.md says how; this file says what and why.

Every requirement is verified: a test CI runs names it (`Verifies: R8` in the test file), or the
requirement says how it is verified instead (*Verified by:* …). `tests/unit/test_traceability.py`
fails CI when one is neither, or when a test names a requirement that is not here.

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

## 3b. From plan to running design

**R27 · Plan → contract → HLD → LLD, one pipeline for every job.** The user and the agent agree the
intent, then a high-level design in plain sentences (takes → does, using → gives → to, because), then
the agent builds the low-level design inside each stage. Every element traces to its parent (request →
step → stage → node / Check; entity → port → field). ✅ (skeleton, cards and Data view derive from it)

**R28 · The graph is the HLD, LLD and ERD.** Top level = HLD (stage cards with their sentence, wires
named by entity); inside a stage = LLD; the Data tab = ERD (entities, keys, relations, fields as seen). ✅

**R29 · Engineering by design.** The agent designs with named architecture and step patterns and checks
engineering principles (single responsibility, typed boundaries, determinism, idempotency, privacy),
records choices as decisions (ADR-style), and passes a design review before proposing. ✅

**R30 · API-native by design.** The contract's promised inputs and outputs are the API from the start
(input nodes, signature and API outputs come with the skeleton); examples are acceptance tests that gate
publishing; versions follow what changed (API → major, design → minor, inside a stage → patch); long
calls run as jobs; written files come back as downloads. ✅

**R31 · Drift is prevented, not only reported.** Edits that contradict the approved design are refused
(undeclared wires between stages, removing designed wires or API inputs); structure is generated from the
design; design changes are amendments the user approves, and the stages they touch are marked stale until
their Checks pass again; declared data fields the run does not carry are flagged. ✅

**R32 · The improve loop is engineered, not improvised.** The goal is verifiable (every step verified
on the latest run, every example passing); each iteration is a version with the agent's hypothesis and
change, the state measured before and after, and a verdict the studio computes; a deterministic
controller says continue, stop, revert or ask the user, within a budget (iterations, stalls). In
production a watch re-runs the examples on a schedule. ✅

**R33 · Test data with known answers.** Synthetic records from a recipe, damaged on purpose with the
truth and a change log kept, and accuracy measured against the truth. ✅

**R34 · Follow one record and check the automation.** Any record can be followed through the last
run, step by step, with only the values that changed; each automatic decision branch sends a fixed
sample to review, and your reviews give a lower bound for how often that branch is right. Customer
ids stay the same across runs, and each run says what changed since the last. ✅

## 4. Use

**R16 · A workflow is an API.** A finished workflow is published at a fixed version and called with
data in and data out; its signature is App mode's inputs/outputs. Data comes in through ComfyUI's own
upload (the caller uploads files, then passes the folder name); long calls are ComfyUI's native jobs.
APIs serve this machine by design (customer data stays here); remote callers go through a reverse
proxy with authentication in front of ComfyUI. ✅

**R17 · Logic is easy to configure and monitor.** Rules are added, removed or skipped in plain
text (or by asking the agent); each rule's counts, change since the last run and agreement with
your reviews are shown. ✅

**R18 · Standalone.** Someone else can install it and have the agent build a transparent workflow
for their own problem, not only record matching. ✅ (10/10 evals incl. sales report, feedback triage,
invoice extraction, data quality; installer brings the Pi CLI) — open: a second model, a license
*Verified by:* the real-agent evals (`tests/eval`, domain scenarios) and CI installing from scratch.

## 5. The record-matching (名寄せ) benchmark

**R19 · Better than deterministic matching.** On realistic dirty Japanese insurance data (OCR,
typos, missing values, old addresses, maiden names, partial updates, family members, look-alikes),
beat a Trillium-style pattern grid; target precision ≥ 0.95 and recall ≥ 0.85, confirmed on
held-out data. ✅ (reference P 0.961 / R 0.893; held-out 0.956/0.882 and 0.970/0.886; grid F1 0.583)
*Verified by:* the nayose evals and `tests/e2e/build_reference.cjs` (scored on held-out seeds).

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
*Verified by:* construction: every test instance comes from `tests/e2e/fixture.py` (temp directory,
free port); CI has no 8188 at all.

**R25 · Verified end to end with the real agent.** After changes, real-agent runs on 8188 are
inspected for process and outcome, not only unit tests. ✅ (practice)
*Verified by:* practice (each change's report lists the real-agent runs).

**R26 · Plan with small tests first.** Difficult work starts with research and small experiments
(now capability tests). ✅ (practice)
*Verified by:* practice.

**R35 · Every workflow has its own CI/CD.** A workflow's tests live in it: its examples
(acceptance), each stage's Checks (integration), the design rules on every stage (ports wired, every
part realized, no untraced node) and no regression since the last release. One run checks them all
on exactly this graph and is kept as a version; publishing needs a green run; each release says why
its version moved; an API can be rolled back to an earlier release. ✅

**R36 · The studio is traced like its workflows.** Every requirement here is verified by a test CI
runs, or says how it is verified; the studio's own CI fails otherwise. ✅

**R38 · APIs follow a design standard the tools enforce.** Names say what they hold, every input
and output is described and exercised by an example, records come in as data (uploaded, never one
text value), no credential is an input, every table output names its fields; the contract and design
tools refuse a proposal that breaks a rule, the workflow's CI lints the generated OpenAPI, and the
version follows what changed for callers (a breaking change is major, with the breaks listed). ✅

**R37 · The agent works in the studio's scope.** Data and text work: the agent finds and adds only
the studio's own nodes and ComfyUI's text and utility nodes; image, video, audio and model nodes
stay installed for people but out of the agent's reach. Paid cloud API nodes are not loaded and the
frontend makes no internet calls. ✅
