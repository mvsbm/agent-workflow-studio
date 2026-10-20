---
name: comfyui-connector-management
description: Manage ComfyUI node input/output contracts, connector types, required ports and safe rewiring using authoritative native schemas.
---
# ComfyUI connector and contract management

Use these checks by default before connecting, disconnecting or changing a node contract.
- Inspect both live endpoints and their exact installed schemas. Use actual node IDs (or current-batch keys), output indices and input names/indices. Never guess a port from the displayed title.
- Match native output/input types, required and optional ports, widget-backed inputs, enum values and input/output list semantics. Dynamic/wildcard ports still need native validation; similar type names are not evidence of compatibility.
- Use installed explicit converters only when their schemas prove the conversion. Do not coerce a typed contract into an untyped string merely to make a wire connect.
- An occupied input is a replacement, not an additive edit: native confirmation is required. Preserve the old connection unless replacement was authorized. Never bypass this with remove/recreate tricks.
- For a changed contract, trace all consumers and update only the affected branch. Inspect installed schemas rather than inventing contract formats.
- Apply related edits in one native transaction (maximum 32 operations). An invalid operation must roll back the batch. Read the graph back to confirm every intended link, required input and output sink.
- Preserve approval/activation properties and credentials. Do not copy private properties into tool responses, exported source or chat.

For example, a source's `STRING` output is not an `IMAGE` input. Select a genuinely compatible installed node or report the mismatch; do not force the link.
