# Studio standards

How the studio names and handles data, compute, jobs and orchestration, borrowing Google Cloud's
model. **Generated** from `custom_nodes/agent_workflow_studio/standards/` by
`python -m custom_nodes.agent_workflow_studio.standards` (from the ComfyUI folder): edit the
registry, not this file.

## The model

| Google Cloud | Studio | Where |
|---|---|---|
| Cloud Storage buckets, lifecycle rules | Storage locations: `input` (landing: uploads), `runs` (each job's working folders under output/), `projects` (each workflow's versioned repository), `published` (API releases). Retention: *Keep run outputs for N days* (newest of each kept). | `outputs.py`, `project_repo.py` |
| BigQuery table schemas, Dataplex entry types | **Dataset classes**: a registered row schema, format and socket, per domain. | `standards/datasets/*.json` |
| A BigQuery table | A **dataset**: an instance of a class with a **name**, the entity the workflow's design names (a `record_clusters` dataset named `customers`). Passed by reference. | the design's `data` |
| Cloud Run services | **Nodes**: the studio's own, reviewed, shared by every workflow, typed by the registry. | `custom_nodes/agent_workflow_studio/*.py` |
| Cloud Functions | **Tools**: workflow-scoped code, approved by hash, sandboxed, typed and tested; a proven tool **graduates** into a node. | `agent_tools.py`, `graduation.py` |
| Jobs (Cloud Run Jobs, BigQuery jobs) | A **job**: one workflow execution (ComfyUI's prompt id, its native `/api/jobs/{id}`), with the run records it leaves. | `workflow_api.py` |
| Workflows, Composer DAGs | The workflow's **stages** in dependency order (contract → design → stages → nodes). | the canvas |
| Cloud Scheduler | **Watch**: runs a published API on a schedule. | `workflow_api.py` |
| Cloud Build | **Run checks**: the workflow's CI (design, tool tests, stage Checks, examples, no regression). | `pipeline.py` |
| Revisions, traffic rollback | **Releases**: semver per published API, callers pin `name@x.y.z`, **Roll back**. | `workflow_api.py` |

Deferred until a need shows: per-location lifecycle rules, one job record merging today's run
records, `workflows/{id}/…` resource names.

## Types

A **type** says what data *is*; a workflow gives each dataset its *name*. Ports are typed by
socket (what ComfyUI wires); an API's inputs and outputs by kind (what a caller sends). Old names
keep working as aliases. A type that is not registered is refused, never guessed.

### Core types (closed)

| Type | Socket | API kind | API form | Aliases | What it is |
|---|---|---|---|---|---|
| STRING | STRING | text | value | text, string, str | Text. |
| INT | INT | integer | value | integer, int, count | A whole number. |
| FLOAT | FLOAT | number | value | number, float, decimal | A number. |
| BOOLEAN | BOOLEAN | boolean | value | boolean, bool, flag | Yes or no. |
| JSON | DICT | json | value | json, object, dict, DICT | A small structured value (an object): a summary, settings, a step's result. |
| TABLE | TABLE | table | value | table, rows, records, csv | A small table carried inline: its rows themselves. Large data is a DATASET instead. |
| DATASET | (per class) | dataset | reference | dataset | Data passed by reference: records stored as a file in a job's run folder, of a registered dataset class, with a name. The base of every dataset class; never carries rows inline. |
| FILE | STRING | file | upload | file | One data file the caller uploads first (ComfyUI's POST /upload/image into the input folder), then names. |
| FOLDER | STRING | folder | upload | folder, directory | A folder of data files the caller uploads first (into ComfyUI's input folder), then names. |

### Dataset classes (open: one file per domain)

| Domain | Class | Socket | Storage | File | Was | What it is |
|---|---|---|---|---|---|---|
| matching | record_pairs | RECORD_PAIRS | file | pairs.jsonl | PAIRS | Candidate pairs of records, compared field by field (one level per field), not decided yet. |
| matching | pair_decisions | PAIR_DECISIONS | file | decisions.csv | DECISIONS | Decided pairs: match or different, with the rule or score band that decided and why. |
| matching | record_clusters | RECORD_CLUSTERS | file | clusters.csv | CUSTOMERS | Records grouped into clusters (one entity each), built strongest-first from the decisions, refusing merges decided different. |
| synthetic | synthetic_people | SYNTHETIC_PEOPLE | memory | – | NAYOSE_PEOPLE | The generated people: the truth the scorer compares against. Passed between the generator's nodes in memory. |
| synthetic | synthetic_activity | SYNTHETIC_ACTIVITY | memory | – | NAYOSE_FACTS | The activity facts derived from the people, before they are written as damaged system exports. Passed in memory. |

A dataset passed by reference is `{kind: studio#dataset, class, name, uri, format, row_count,
subset, inputs}` (`datasets.py`): never its rows inline. `subset` limits it to the ids listed in
that file (a branch left open); `inputs` is its lineage. A step reads exactly the file it is
given. A memory dataset stays inside one process, so it cannot cross into a tool or an API.

### Adding a domain

Add `standards/datasets/<domain>.json` with its classes (description, socket, storage, and for
file storage its format, file and row schema), then regenerate this file. The registry refuses
a name, socket or alias two things claim.
