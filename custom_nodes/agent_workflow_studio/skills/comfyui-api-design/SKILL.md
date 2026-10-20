---
name: comfyui-api-design
description: "Design a workflow's API like an API engineer: promise inputs and outputs that say what they hold, take records as uploaded data (claim check), type every table output with its fields, and keep the API compatible. Use when writing the contract's promises and the design's outputs and data."
---
# Designing a workflow's API

Every workflow with a contract is an API: its promised inputs and outputs become App mode's interface, the published endpoint, and a generated OpenAPI description. Callers read that description, not the canvas, so design it on purpose. The studio checks the rules below when you propose the contract and the design, again in the workflow's checks (`comfy_run_examples`), and when the user publishes; a failing rule names what to fix.

## Patterns (the studio implements them; design for them)

| Pattern | What it means here |
|---|---|
| Request–Response | A short call: `POST /agent-workflow-studio/api/<name>/run` waits for the result. |
| Async Request–Reply | A long call is a ComfyUI job: start it (`…/jobs`), then poll or cancel it at `/api/jobs/<id>`. Nothing to design: every workflow supports both. |
| Claim Check | Records (CSV, TSV, JSONL, exports) never travel as one text value. The caller uploads the files with ComfyUI's own upload (`POST /upload/image`, `type=input`, `subfolder=<folder>`), then passes the folder (kind `folder`) or file name (kind `file`). A Read table node in the first stage reads them. |
| Data contract | Each table output's columns come from a design entity (`data`: `description`, `key`, `fields`). Name the entity like the output or the port that delivers it. |
| Versioned API | The studio versions every release from what changed for callers: removing, renaming or retyping an input or output, or a new input without a default, is breaking (major); a new optional input, a new output or a design change is minor; anything inside a stage is a patch. Prefer additions to changes. |
| Idempotent call | The same inputs on the same version give the same result. Keep steps deterministic, or behind a Check when they are not. |

## Rules (each one blocks a proposal until it passes)

1. **Names say what they hold.** Inputs and outputs are snake_case nouns: `orders`, `customer_file`, `revenue_by_region`, never `value`, `input`, `data`, `result`, `output`.
2. **Every input and output has a one-line `description`** a caller can act on: what it holds and, for data, its shape ("The order exports, one CSV per system").
3. **Records come in as data**: kind `folder` or `file`, never `table` / `csv` / `records` as one text value.
4. **No credential is an input** (`password`, `token`, `api_key`, `secret`): credentials stay in the keychain.
5. **The examples exercise the whole interface**: every input appears in some example's `given`, every output in some `expect`. They are the API's contract tests.
6. **Every table or file output names its fields**: an entity in `data` with `fields` (and `key` for a table).
7. **Every data input is read by a step** (`takes {<port>: "input.<name>"}`).

## Worked example: a CSV in, a typed table out

Contract (intent):

```json
"promises": {
  "inputs":  [{"name": "orders", "kind": "folder", "description": "The order exports to total, one CSV per system.", "example": "orders_2026_10"}],
  "outputs": [{"name": "revenue_by_region", "kind": "table", "description": "Revenue per region, largest first."}]
},
"examples": [{"id": "E1", "covers": ["R1"], "given": {"orders": "orders_sample"}, "expect": {"revenue_by_region": "north"}}]
```

Design (HLD):

```json
"steps": [{"id": "total", "takes": {"orders": "input.orders"}, "gives": {"revenue_by_region": "TABLE"}, "...": "..."}],
"data": {"revenue_by_region": {"description": "One row per region", "key": ["region"], "fields": ["region", "revenue"]}},
"outputs": {"revenue_by_region": "total.revenue_by_region"}
```

LLD of `total`: a Read table node (`set: {"file": "orders.csv"}`, realizes `takes orders`, `does`), wired `input.orders → read.folder`, then Summarize (`revenue: sum(amount)` by `region`) wired to `output.revenue_by_region`.

The caller then uploads `orders.csv` into `input/orders_2026_10/` and calls `{"inputs": {"orders": "orders_2026_10"}}`; the answer's `data.revenue_by_region` has `columns` `["region", "revenue"]`, the rows, and the whole table as a file under `files`.
