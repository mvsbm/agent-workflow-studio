"""The studio's type standard, read from standards/: one registry every part of the studio asks.

Two scopes, both data (JSON), so Python, the frontend and the agents read the same thing:
- core types (standards/types.json): closed and domain-free. STRING, INT, FLOAT, BOOLEAN, JSON,
  TABLE (small rows inline), DATASET (data by reference), FILE and FOLDER (uploaded, then named).
- dataset classes (standards/datasets/<domain>.json): open; a domain adds its own file and never
  edits the core. A class has a row schema, a socket and its storage: a file in a job's run folder
  (by reference: the DATASET shape), or memory (in-process only: it cannot cross into a tool or an
  API).
A workflow names its own datasets of these classes in its design's data model (the third scope):
"customers" is a name, of class record_clusters; no type is called that.

Ports are typed by socket (what ComfyUI wires: TABLE, RECORD_PAIRS, DICT, …); an API's inputs and
outputs by kind (what a caller sends: text, number, table, folder, …). Old names keep working as
aliases (PAIRS → RECORD_PAIRS, count → integer).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

HERE = Path(__file__).resolve().parent / "standards"
CLASS_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
SOCKET = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")
STORAGE = ("file", "memory")
API_FORMS = ("value", "upload", "reference")


@dataclass(frozen=True)
class Spec:
    """One core type or dataset class."""

    name: str
    scope: str  # "type" or "class"
    description: str
    socket: str | None
    schema: dict
    aliases: tuple[str, ...] = ()
    kind: str | None = None  # a core type's API kind
    api: str | None = None
    app_input: str | None = None
    domain: str | None = None
    storage: str | None = None  # a class's: file or memory
    format: str | None = None
    file: str | None = None
    row: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Registry:
    specs: dict[str, Spec]  # by name
    names: dict[str, str]  # every name, socket, kind and alias → the spec's name

    def resolve(self, name: object) -> Spec | None:
        found = self.names.get(name) if isinstance(name, str) else None
        return self.specs[found] if found else None


def _check(path: str, schema: object, problems: list[str]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        problems.append(f"{path}: not a valid JSON Schema ({error.message})")


def build(types: dict, domains: list[dict]) -> Registry:
    """The registry from its documents, or ValueError naming every rule they break."""
    problems: list[str] = []
    specs: dict[str, Spec] = {}
    for name, t in (types.get("types") or {}).items():
        where = f"types.json {name}"
        if not SOCKET.match(name):
            problems.append(f"{where}: a core type is named in capitals")
        if not t.get("description"):
            problems.append(f"{where}: needs a description")
        if t.get("api") not in API_FORMS:
            problems.append(f"{where}: api is one of {', '.join(API_FORMS)}")
        _check(where, t.get("schema"), problems)
        specs[name] = Spec(
            name,
            "type",
            t.get("description", ""),
            t.get("socket"),
            t.get("schema") or {},
            tuple(t.get("aliases") or ()),
            t.get("kind"),
            t.get("api"),
            t.get("app_input"),
        )
    for document in domains:
        domain = document.get("domain")
        for name, c in (document.get("classes") or {}).items():
            where = f"datasets/{domain}.json {name}"
            storage = c.get("storage", "file")
            if not CLASS_NAME.match(name):
                problems.append(f"{where}: a class is named in lowercase words")
            if not c.get("description"):
                problems.append(f"{where}: needs a description")
            if not SOCKET.match(c.get("socket") or ""):
                problems.append(f"{where}: needs a socket, in capitals")
            if storage not in STORAGE:
                problems.append(f"{where}: storage is one of {', '.join(STORAGE)}")
            if storage == "file":
                if not c.get("format") or not c.get("file"):
                    problems.append(f"{where}: a file dataset names its format and file")
                _check(f"{where} row", c.get("row"), problems)
            if name in specs:
                problems.append(f"{where}: {name} is already defined")
            specs[name] = Spec(
                name,
                "class",
                c.get("description", ""),
                c.get("socket"),
                specs["DATASET"].schema if "DATASET" in specs else {},
                tuple(c.get("aliases") or ()),
                domain=domain,
                storage=storage,
                format=c.get("format"),
                file=c.get("file"),
                row=c.get("row") or {},
            )
    names: dict[str, str] = {}
    for spec in specs.values():
        own = {spec.name, *spec.aliases, *([spec.kind] if spec.kind else [])}
        # A class is also known by its socket; core sockets are shared (FILE and FOLDER are STRING).
        if spec.scope == "class" and spec.socket:
            own.add(spec.socket)
        for name in own:
            if names.get(name, spec.name) != spec.name:
                problems.append(f"{name} names both {names[name]} and {spec.name}")
            names[name] = spec.name
    sockets = [s.socket for s in specs.values() if s.scope == "class"]
    if len(sockets) != len(set(sockets)):
        problems.append("two dataset classes share a socket")
    if problems:
        raise ValueError("The type registry breaks its rules: " + "; ".join(problems))
    return Registry(specs, names)


@cache
def registry() -> Registry:
    types = json.loads((HERE / "types.json").read_text(encoding="utf-8"))
    domains = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((HERE / "datasets").glob("*.json"))
    ]
    return build(types, domains)


# --- what the rest of the studio asks -------------------------------------------------------------
def resolve(name: object) -> Spec | None:
    """A type or class by its name, socket, kind or an alias (old names keep working)."""
    return registry().resolve(name)


def classes() -> list[Spec]:
    return [s for s in registry().specs.values() if s.scope == "class"]


def handoff_sockets() -> set[str]:
    """Data handed between steps that deserves a named entity in the design: tables and datasets."""
    return {"TABLE", *(s.socket for s in classes())}


def dataset_sockets(storage: str | None = "file") -> list[str]:
    """The sockets of the dataset classes (of one storage, or all), for inputs taking any dataset."""
    return [s.socket for s in classes() if storage is None or s.storage == storage]


def port_types() -> list[str]:
    """What a tool's port may be typed with: the sockets data can cross a process boundary in."""
    core = [s.socket for s in registry().specs.values() if s.scope == "type" and s.socket]
    return list(dict.fromkeys([*core, *dataset_sockets("file")]))


def port_socket(name: object) -> str | None:
    """The socket a port typed `name` uses (an alias or a class name included), if it may cross."""
    spec = resolve(name)
    if spec is None or spec.socket is None or spec.storage == "memory":
        return None
    return spec.socket


def api_kinds() -> list[str]:
    """The kinds an API's inputs and outputs may have (what a caller sends or gets)."""
    return [
        s.kind
        for s in registry().specs.values()
        if s.scope == "type" and s.kind and s.api in ("value", "upload")
    ]


def api_kind(name: object) -> str | None:
    """The canonical API kind for a kind or its alias (count → integer), or None if unknown."""
    spec = resolve(name)
    return spec.kind if spec and spec.scope == "type" and spec.kind in api_kinds() else None


def value_problems(socket: str, value: object) -> list[str]:
    """Where a value does not have its port's type (an empty list when it does)."""
    spec = resolve(socket)
    if spec is None:
        return [f"{socket} is not a registered type"]
    if spec.scope == "class":
        if not isinstance(value, dict) or value.get("kind") != "studio#dataset":
            return [
                f"should be a {spec.name} dataset reference "
                '({"kind": "studio#dataset", "class", "uri", …}), got '
                + (type(value).__name__ if not isinstance(value, dict) else "a plain dict")
            ]
        if value.get("class") != spec.name:
            return [f"should be a {spec.name} dataset, got {value.get('class')!r}"]
    errors = sorted(Draft202012Validator(spec.schema).iter_errors(value), key=str)
    return [f"should be {spec.name}: {errors[0].message}"] if errors else []


@cache
def custom(name: str):
    """The ComfyUI socket type of a dataset class (or TABLE), for node schemas: io.Custom(socket)."""
    from comfy_api.latest import io

    spec = resolve(name)
    if spec is None or not spec.socket:
        raise ValueError(f"{name} is not a registered type with a socket")
    return io.Custom(spec.socket)


def socket_aliases(live: set[str] = frozenset()) -> dict[str, str]:
    """Old socket names → current ones, so saved workflows read with the current names. A name an
    installed node still uses (`live`, another pack's own type) is that pack's, and stays."""
    return {
        alias: s.socket
        for s in registry().specs.values()
        for alias in s.aliases
        if s.socket and SOCKET.match(alias) and alias != s.socket and alias not in live
    }


def live_sockets() -> set[str]:
    """Every socket type the installed nodes use (ComfyUI's node classes, every pack)."""
    import nodes

    found: set[str] = set()
    for cls in nodes.NODE_CLASS_MAPPINGS.values():
        try:
            found.update(t for t in getattr(cls, "RETURN_TYPES", ()) if isinstance(t, str))
            for group in (cls.INPUT_TYPES() or {}).values():
                for spec in (group or {}).values():
                    if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], str):
                        found.update(spec[0].split(","))
        except Exception:  # a node's schema that cannot be read here names no socket
            continue
    return found


def as_dict(live: set[str] = frozenset()) -> dict:
    """The registry for the frontend and the agents."""
    return {
        "types": [
            {
                "name": s.name,
                "scope": s.scope,
                "description": s.description,
                "socket": s.socket,
                "kind": s.kind,
                "api": s.api,
                "app_input": s.app_input,
                "domain": s.domain,
                "storage": s.storage,
                "aliases": list(s.aliases),
            }
            for s in registry().specs.values()
        ],
        "socket_aliases": socket_aliases(live),
    }


# --- the standard as a document (STANDARDS.md; tests/unit/test_standards.py keeps it current) ----
DOC = HERE.parent.parent.parent / "STANDARDS.md"
MODEL = """# Studio standards

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
"""


def render() -> str:
    """STANDARDS.md, from the registry."""
    lines = [MODEL, "### Core types (closed)", ""]
    lines += [
        "| Type | Socket | API kind | API form | Aliases | What it is |",
        "|---|---|---|---|---|---|",
    ]
    for s in registry().specs.values():
        if s.scope == "type":
            lines.append(
                f"| {s.name} | {s.socket or '(per class)'} | {s.kind} | {s.api} | "
                f"{', '.join(s.aliases)} | {s.description} |"
            )
    lines += ["", "### Dataset classes (open: one file per domain)", ""]
    lines += [
        "| Domain | Class | Socket | Storage | File | Was | What it is |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in classes():
        lines.append(
            f"| {s.domain} | {s.name} | {s.socket} | {s.storage} | {s.file or '–'} | "
            f"{', '.join(s.aliases)} | {s.description} |"
        )
    lines += [
        "",
        "A dataset passed by reference is `{kind: studio#dataset, class, name, uri, format, row_count,",
        "subset, inputs}` (`datasets.py`): never its rows inline. `subset` limits it to the ids listed in",
        "that file (a branch left open); `inputs` is its lineage. A step reads exactly the file it is",
        "given. A memory dataset stays inside one process, so it cannot cross into a tool or an API.",
        "",
        "### Adding a domain",
        "",
        "Add `standards/datasets/<domain>.json` with its classes (description, socket, storage, and for",
        "file storage its format, file and row schema), then regenerate this file. The registry refuses",
        "a name, socket or alias two things claim.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    DOC.write_text(render(), encoding="utf-8")
    print(f"Wrote {DOC}")
