---
name: comfyui-node-organization
description: Keep the canvas readable — one group per contract step, short numbered titles, a preview under every stage, automatic left-to-right layout, and given nodes left alone.
---
# ComfyUI node organization

The user reads the canvas to understand the work. Organize for that reader.

**Layout is automatic.** After every edit that adds or connects nodes the canvas is re-laid out: data flows left to right in columns by depth, a preview sits directly under the node it shows, notes go above. Do not compute coordinates; omit `position` when adding nodes. Use `{"op": "arrange"}` to tidy on request. Each contract step's nodes are framed by a group titled with the step (from the `step` tag on `add`), so tag every node you add.

**Titles.** Every node you add gets a short title saying what it does in the user's terms, numbered by step: `2 · Find candidate pairs`, not `AgentWorkflowAgentTool`. Previews are titled for what they prove: `2 · Candidate pairs (sample)`, and a gate's preview carries the exact title the gate names.

**Show each thing once.** An Agent Tool node already shows its operation, every file in and out with rows and columns, a sample, and its own result text. Do not add Preview nodes that repeat that. A gate can check the tool node itself (its title); add a Preview only for something the tool node does not show. Do not add notes that repeat what titles show.

**Finish with stages.** When every step is built and its gate passes, run `{"op": "organize"}` (the studio also does it at the end of your reply once every step has nodes and the latest run succeeded): each step becomes one stage subgraph, and the top level becomes one row of stage cards joined by the main data path, each showing what the stage did, its key numbers and its status. The user double-clicks a card to see inside. To change a packed step: `unpack` it, edit, `organize` again.

**Decisions are their own node.** In matching or triage, the decision is a Decision rules node between the compare stage and what uses the decisions, never inside a tool.

**Given nodes** (`given: true` in `comfy_read_workflow`) were provided by the user or a template. Build on and connect to them; do not remove, rename or rewire them. They may move with the automatic layout.

**When asked to reorganize existing work:** only titles and layout change. Keep every widget value, link, mode and property; layout is not permission to simplify or delete. Read back afterwards to confirm the topology is unchanged.
