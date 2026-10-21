"""The API design standard, as code: the rules a workflow's interface must pass, enforced where the
agent works (contract, design), in the workflow's CI (the generated OpenAPI) and when publishing
(compatibility decides the version). Rules in the style of API style guides (Zalando, Google AIP)
and linters (Spectral); compatibility in the style of oasdiff. Pure: no ComfyUI imports.
"""

from __future__ import annotations

import re

from .standards import api_kind, handoff_sockets, resolve

NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
# Names that say nothing about what they hold (a caller cannot tell one from another).
GENERIC = {"value", "values", "input", "inputs", "output", "outputs", "data", "result", "results"}
GENERIC |= {"out", "in", "x", "y", "item", "items", "thing", "stuff", "param", "arg"}
SECRET = re.compile(r"pass(word)?|token|secret|api_?key|credential|private_?key", re.I)
# Kinds come from the type standard (standards.py); a contract's kinds are canonical once validated.
# Records come in as data uploaded to ComfyUI's input folder (claim check), never as one text value.


def _kind(item: dict) -> str | None:
    return api_kind(str(item.get("kind", "")).strip().lower())


def _uploaded(item: dict) -> bool:
    """A data input the caller uploads first, then names (FILE, FOLDER: api form "upload")."""
    spec = resolve(_kind(item))
    return bool(spec and spec.api == "upload")


def _named(items: list[str], what: str) -> str:
    return f" — {what}: {', '.join(items)}" if items else ""


def contract_rules(contract: dict) -> list[tuple[bool, str]]:
    """The interface as the contract promises it (checked before the user is asked to approve)."""
    promises = contract.get("promises") or {}
    inputs, outputs = promises.get("inputs") or [], promises.get("outputs") or []
    ports = inputs + outputs
    given = {k for e in contract.get("examples") or [] for k in e.get("given") or {}}
    expected = {k for e in contract.get("examples") or [] for k in e.get("expect") or {}}
    vague = [p["name"] for p in ports if not NAME.fullmatch(p["name"]) or p["name"] in GENERIC]
    undescribed = [p["name"] for p in ports if not (p.get("description") or "").strip()]
    as_text = [i["name"] for i in inputs if _kind(i) == "table"]
    secrets = [i["name"] for i in inputs if SECRET.search(i["name"])]
    untested = [f"input {i['name']}" for i in inputs if i["name"] not in given]
    untested += [f"output {o['name']}" for o in outputs if o["name"] not in expected]
    return [
        (
            not vague,
            "API: every input and output is a snake_case name that says what it holds"
            + _named(vague, "rename"),
        ),
        (
            not undescribed,
            "API: every input and output has a one-line description"
            + _named(undescribed, "describe"),
        ),
        (
            not as_text,
            "API: records come in as data (kind folder or file: the caller uploads them to ComfyUI's "
            "input folder and passes the name), never as one text value"
            + _named(as_text, "change"),
        ),
        (
            not secrets,
            "API: no credential is an input (credentials stay in the keychain)"
            + _named(secrets, "remove"),
        ),
        (
            not untested,
            "API: the examples exercise the whole interface (every input in a given, every output "
            "in an expect)" + _named(untested, "use in an example"),
        ),
    ]


def design_rules(contract: dict) -> list[tuple[bool, str]]:
    """The interface as the design delivers it: typed data out, every data input read by a step."""
    steps = {s["id"]: s for s in contract.get("steps") or []}
    data = contract.get("data") or {}
    promises = contract.get("promises") or {}
    untyped = []
    for o in promises.get("outputs") or []:
        step, _, port = (o.get("from") or "").partition(".")
        typed_port = (steps.get(step, {}).get("gives") or {}).get(port) in handoff_sockets()
        if typed_port or _kind(o) in ("table", "file"):
            entity = data.get(o["name"]) or data.get(port) or {}
            if not entity.get("fields"):
                untyped.append(o["name"])
    read = {
        v[6:]
        for s in steps.values()
        for v in (s.get("takes") or {}).values()
        if v.startswith("input.")
    }
    unread = [
        i["name"] for i in promises.get("inputs") or [] if _uploaded(i) and i["name"] not in read
    ]
    return [
        (
            not untyped,
            "API: every table or file output names its fields (an entity in data named like the "
            "output or its port, with fields and key)" + _named(untyped, "add fields for"),
        ),
        (
            not unread,
            "API: every data input is read by a step (takes input.<name>)" + _named(unread, "read"),
        ),
    ]


def _inputs(signature: dict) -> dict[str, dict]:
    return {i["name"]: i for i in signature.get("inputs") or []}


def compatibility(old: dict | None, new: dict) -> dict[str, list[str]]:
    """What changed for callers between two signatures. Breaking: an input or output removed, an
    input's type changed or its choices narrowed, a new input without a default. Additive: a new
    input with a default, a new output, choices widened."""
    if not old:
        return {"breaking": [], "additive": []}
    before, after = _inputs(old), _inputs(new)
    breaking, additive = [], []
    for name, item in before.items():
        if name not in after:
            breaking.append(f"input {name} removed")
            continue
        now = after[name]
        if str(item.get("type")) != str(now.get("type")):
            breaking.append(f"input {name} changed type {item.get('type')} → {now.get('type')}")
        was, choices = set(item.get("choices") or []), set(now.get("choices") or [])
        if was and choices and not was <= choices:
            breaking.append(
                f"input {name} no longer accepts {', '.join(map(str, sorted(was - choices)))}"
            )
        elif was and choices > was:
            additive.append(f"input {name} accepts more choices")
    for name, item in after.items():
        if name not in before:
            (additive if "default" in item else breaking).append(
                f"input {name} added" + ("" if "default" in item else " (required)")
            )
    old_out = {o["name"] for o in old.get("outputs") or []}
    new_out = {o["name"] for o in new.get("outputs") or []}
    breaking += [f"output {n} removed" for n in sorted(old_out - new_out)]
    additive += [f"output {n} added" for n in sorted(new_out - old_out)]
    return {"breaking": breaking, "additive": additive}


def openapi_lint(doc: dict) -> list[str]:
    """What a caller of the generated description would miss: untyped or undescribed inputs,
    inputs without an example or default, outputs without a schema, operations without an id or
    documented errors."""
    problems = []
    for path, methods in (doc.get("paths") or {}).items():
        for method, op in methods.items():
            if not op.get("operationId"):
                problems.append(f"{method.upper()} {path}: no operationId")
            if not any(code.startswith("4") for code in op.get("responses") or {}):
                problems.append(f"{method.upper()} {path}: errors are not documented")
            body = ((op.get("requestBody") or {}).get("content") or {}).get("application/json")
            props = (((body or {}).get("schema") or {}).get("properties") or {}).get("inputs", {})
            for name, spec in (props.get("properties") or {}).items():
                if not (spec.get("type") or spec.get("enum")):
                    problems.append(f"input {name}: no type")
                if not spec.get("description"):
                    problems.append(f"input {name}: no description")
                if "default" not in spec and "examples" not in spec:
                    problems.append(f"input {name}: no example or default")
    run = next(iter(doc.get("paths", {}).values()), {}).get("post", {})
    schema = (
        ((run.get("responses") or {}).get("200") or {})
        .get("content", {})
        .get("application/json", {})
    ).get("schema", {})
    for name, spec in (
        (schema.get("properties") or {}).get("data", {}).get("properties") or {}
    ).items():
        if not (spec.get("type") or spec.get("properties")):
            problems.append(f"output {name}: no schema")
    return list(dict.fromkeys(problems))
