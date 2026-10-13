---
name: comfyui-node-organization
description: Organize native ComfyUI nodes into readable layouts using measured node sizes, meaningful titles and undoable position-only edits.
---
# ComfyUI node organization

Use these practices by default for new nodes; apply to existing nodes when the user asks for organization.
1. Read the live graph, selection, links, positions and sizes. Preserve unrelated user work. Organize the selected branch unless the request covers the whole graph.
2. Arrange dependencies left to right: sources, processing, then output/preview sinks. Align related stages; separate disconnected branches vertically.
3. Use measured node widths/heights, with roughly 80–120 pixels between stages and 40–80 pixels between rows. Reserve space for large previews. For feedback cycles, keep the loop together instead of assuming the graph is a DAG.
4. Apply `move` operations with `node` and finite `position: [x, y]` values. Apply optional `title` operations with a short descriptive title; retain class identity and node IDs. New nodes can use the `add.position` field.
5. Keep every widget value, connector, link, execution mode and approval property unchanged. Layout is not permission to simplify or delete the workflow. Do not move nodes out of existing group boundaries without agreement.
6. Read back to verify non-overlapping bounding boxes and unchanged topology. Batches are atomic/undoable and limited to 32 edits; split larger layouts into verified batches.

The bridge does not provide group creation or resizing: do not invent such operations. Use the native GUI for those actions. Native Auto Queue can react to edits even when they are visual-only; warn when that matters.
