---
name: comfyui-node-organization
description: Keep the canvas readable — work inside each step's stage, short titles in the user's terms, show each thing once, decisions as their own node, given nodes left alone.
---
# ComfyUI node organization

The user reads the canvas to understand the work. Most of its order is structural: each contract step is a **stage** (a subgraph card on the top level, its nodes inside), created when the design is approved, wired by its typed ports, laid out automatically left to right. Nodes cannot go outside a stage. What is left to you:

**Titles.** Every node gets a short title saying what it does in the user's terms: `Find candidate pairs`, not `AgentWorkflowFindCandidates`. Stage titles come from the step ids.

**Replace the Stub.** A planned stage holds a Stub that passes its input on. Build it with `comfy_build_step` (its LLD: nodes, their `realizes` tags, wires from the input ports to the output ports); the Stub disappears and the stage's Check follows whatever feeds its output by itself. Give each LLD node a `title`.

**Show each thing once.** Table nodes and Agent Tools already show rows in → out, before → after and samples; wires show what flows along them. Do not add Preview nodes or notes that repeat that.

**Decisions are their own node.** In matching or triage, the decision is a Decision rules node between the compare step and what uses the decisions, never inside a tool.

**Given nodes** (`given: true` in `comfy_read_workflow`) were provided by the user or a template. Build on and connect to them; do not remove, rename or rewire them.

**When asked to reorganize existing work:** only titles and layout change. Keep every widget value, link, mode and property. Read back afterwards to confirm the topology is unchanged.
