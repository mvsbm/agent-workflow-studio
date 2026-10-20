---
name: comfyui-node-creation
description: Create or extend live ComfyUI workflows with installed nodes, exact schemas, minimal edits and verified results.
---
# ComfyUI node creation

You are in the ComfyUI application. Workflow creation defaults to the actual live canvas, not a downloadable JSON file. Only offer JSON export when explicitly requested. A capability question ('can you create a workflow here?') should get a direct yes about native nodes/connections/Run; ask what the workflow should do if unspecified, not whether to use this app. Do not replace existing work or invent a demo to answer a vague question.

Use this procedure by default when creating or extending a workflow.
1. Read `comfy_read_workflow`: identify the root workflow, selected nodes, existing work and intended outputs. Reuse compatible nodes first.
2. Search `comfy_node_catalog`, then request exact `class_types` schemas for the chosen installed nodes. Names in an example or saved graph are not installation evidence. Search capabilities in descriptions/aliases and schema choices too: an exporter may advertise `csv` as a format rather than in its name. Inspect the exact exporter schema before claiming it is absent.
3. Check required/optional inputs, widget names, enum choices, types, output indices and list semantics with the connector-management skill. Do not invent a converter or a missing widget.
4. Inside a contract's stage, build with `comfy_build_step` (the stage's whole LLD at once). Elsewhere, apply the smallest connected addition through `comfy_edit_workflow` (at most 32 operations). Build the complete connected source/generator → processing/validation → output/export chain with multiple nodes as required, not just a controls or preview node. Give new nodes batch-local keys; reuse those keys in widget/connect operations. The 32-operation limit is per batch, not a node/workflow-size limit; use returned IDs and fresh keys in later batches. Place the additions near related nodes without overlaps.
5. Read back the actual graph and verify the expected nodes, widgets, links and output sink. Run only when requested or needed for agreed acceptance; the Run tool enforces the user's approval preference.
6. Inspect actual execution results before claiming success. Distinguish a submitted job, a completed job and a useful output. For dataset/CSV generation, the graph must contain the real generation and output/export nodes, and execution results must identify the artifact. A controls/preview node alone does not generate a dataset. A workspace CSV written by Pi is a standalone file, not an executed node output and not a substitute for a requested workflow run. Never fabricate sample rows to conceal a missing generator/exporter; report the blocker and use approved node development. If a standalone workspace file was explicitly requested, label it workspace-only and provide its relative path and Workflow files download location.

If the needed node is absent, explain the missing capability and tell the user and offer a manual alternative through native node search; do not generate code to fill the gap. Workspace Python/Rust files are inert source, not installed nodes. Never unlock host shell, install dependencies, mint approval tokens or activate generated code. Native Auto Queue may run canvas edits independently; warn before editing when inert changes matter.
