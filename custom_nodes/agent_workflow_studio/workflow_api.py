"""A workflow as an API: published at a fixed version, called with data in and data out.

The signature is App mode's selection (the workflow's ``extra.linearData``): inputs are widgets on
top-level nodes, outputs are top-level stages (their output nodes' results are returned). Publishing
freezes the runnable (API-format) prompt and the workflow at a version of the project history; a
call fills the inputs, runs it through ComfyUI's own queue and returns the outputs. Local access only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .api_review import compatibility

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
JSON_TYPES = {"STRING": "string", "INT": "integer", "FLOAT": "number", "BOOLEAN": "boolean"}


def published_dir(root: Path) -> Path:
    return root / "published"


def targets(item: dict) -> list:
    """Where an input's value goes: one widget, or every node a subgraph card's widget feeds."""
    return item.get("targets") or [[item.get("node"), item.get("widget")]]


def check_spec(spec: dict) -> dict:
    """The parts of a publish request the server relies on, checked."""
    if not isinstance(spec.get("name"), str) or not NAME.fullmatch(spec["name"]):
        raise ValueError("An API name is short lowercase letters, digits, - or _, e.g. nayose.")
    prompt, signature = spec.get("prompt"), spec.get("signature") or {}
    if not isinstance(prompt, dict) or not prompt:
        raise ValueError("Nothing to publish: the workflow has no runnable nodes.")
    if not signature.get("outputs"):
        raise ValueError("Choose the workflow's outputs in App mode before publishing.")
    names = set()
    for item in signature.get("inputs", []):
        if not all(
            str(node) in prompt and widget in prompt[str(node)]["inputs"]
            for node, widget in targets(item)
        ):
            raise ValueError(
                f"Input {item.get('name')} must be a widget on a top-level node that runs (not inside a stage)."
            )
        names.add(item["name"])
    if len(names) != len(signature.get("inputs", [])):
        raise ValueError("Input names must be unique.")
    for item in signature["outputs"]:
        node = str(item.get("node"))
        if not any(key == node or key.startswith(f"{node}:") for key in prompt):
            raise ValueError(f"Output {item.get('name')} is not a stage or node that runs.")
    return spec


def bind_inputs(spec: dict, values: dict) -> dict:
    """The frozen prompt with the caller's input values filled in (types checked)."""
    prompt = json.loads(json.dumps(spec["prompt"]))
    inputs = {item["name"]: item for item in spec["signature"].get("inputs", [])}
    unknown = set(values) - set(inputs)
    if unknown:
        raise ValueError(
            f"Unknown input(s): {', '.join(sorted(unknown))}. Inputs: {', '.join(inputs) or 'none'}."
        )
    for name, value in values.items():
        item, kind = inputs[name], JSON_TYPES.get(inputs[name].get("type", ""), None)
        ok = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, int | float) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            None: isinstance(value, str | int | float | bool),
        }[kind]
        if not ok:
            raise ValueError(f"Input {name} must be {kind or 'a plain value'}.")
        # A data folder's choices are those listed when it was published; uploads come later
        # (missing_data checks that the folder is there).
        if (
            isinstance(item.get("choices"), list)
            and value not in item["choices"]
            and name not in data_inputs(spec)
        ):
            raise ValueError(
                f"Input {name} must be one of: {', '.join(map(str, item['choices']))}."
            )
        for node, widget in targets(item):
            prompt[str(node)]["inputs"][widget] = value
    return prompt


class NotFound(ValueError):
    """No such API or version (HTTP 404)."""


def data_inputs(spec: dict) -> dict[str, str]:
    """The published inputs that are data (claim check): name → "folder" or "file"."""
    promised = ((spec.get("workflow") or {}).get("extra") or {}).get("aws_contract") or {}
    return {
        i["name"]: str(i["kind"]).lower()
        for i in (promised.get("promises") or {}).get("inputs") or []
        if str(i.get("kind", "")).lower() in ("folder", "file")
    }


def missing_data(spec: dict, values: dict, input_dir: Path) -> list[str]:
    """Data inputs whose folder or file is not in ComfyUI's input folder (each says how to fix it)."""
    base, defaults = (
        input_dir.resolve(),
        {i["name"]: i.get("default") for i in spec["signature"].get("inputs") or []},
    )
    missing = []
    for name, kind in data_inputs(spec).items():
        value = str(values.get(name, defaults.get(name)) or "").strip()
        path = (base / value).resolve()
        found = (
            value
            and path.is_relative_to(base)
            and (path.is_dir() if kind == "folder" else path.is_file())
        )
        if not found:
            missing.append(
                f"{name}: no {kind} {value!r} in ComfyUI's input folder; upload it first with "
                f"POST /upload/image (multipart: image=<file>, type=input, subfolder=<{name} folder>)"
            )
    return missing


def collect_outputs(spec: dict, outputs: dict) -> dict:
    """Each signature output: the results of the output nodes inside it, by node path."""
    result = {}
    for item in spec["signature"]["outputs"]:
        node = str(item["node"])
        result[item["name"]] = {
            key: value
            for key, value in outputs.items()
            if (key == node or key.startswith(f"{node}:")) and isinstance(value, dict)
        }
    return result


def collect_data(outputs: dict) -> dict:
    """The promised outputs' values, from the API output nodes that ran."""
    return {
        item["name"]: item["value"]
        for value in outputs.values()
        if isinstance(value, dict)
        for item in value.get("aws_output") or []
        if isinstance(item, dict) and item.get("name")
    }


def _loose(value: object) -> str:
    return " ".join(str(value).split()).lower()


def compare_example(expect: dict, data: dict, outputs: dict) -> list[str]:
    """What an example expected and the run did not deliver (empty: it passed). An expected value
    is found in the promised output of that name, or else anywhere in the output stages' results."""
    misses = []
    everything = _loose(json.dumps(outputs, ensure_ascii=False, default=str))
    for key, want in expect.items():
        got = data.get(key)
        if got is not None and not isinstance(got, dict | list):
            ok = _loose(got) == _loose(want) or _loose(want) in _loose(got)
        else:
            found = (
                _loose(json.dumps(got, ensure_ascii=False, default=str))
                if got is not None
                else everything
            )
            ok = _loose(want) in found
        if not ok:
            misses.append(
                f"{key}: expected {want!r}, got {str(got)[:120] if got is not None else 'nothing'}"
            )
    return misses


def next_version(previous: dict | None, signature: dict, design: str) -> str:
    """Semantic version from what changed for callers (api_review.compatibility): a breaking
    change → major; an addition or a design (HLD) change → minor; inside a stage only → patch."""
    if not previous:
        return "1.0.0"
    # A release from before version numbers counts as 1.0.0.
    major, minor, patch = (int(x) for x in (previous.get("semver") or "1.0.0").split("."))
    change = compatibility(previous["signature"], signature)
    if change["breaking"]:
        return f"{major + 1}.0.0"
    if change["additive"] or previous.get("design") != design:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def release_notes(previous: dict | None, signature: dict, design: str) -> str:
    """Why this version number, in the words of next_version: what changed for callers."""
    if not previous:
        return "First release."
    change = compatibility(previous["signature"], signature)
    if change["breaking"]:
        added = f" Added: {'; '.join(change['additive'])}." if change["additive"] else ""
        return "Major: breaking for callers — " + "; ".join(change["breaking"]) + "." + added
    if change["additive"]:
        return "Minor: " + "; ".join(change["additive"]) + "; existing calls keep working."
    if previous.get("design") != design:
        return "Minor: the design changed (its stages or their wiring); the API is the same."
    return "Patch: only inside stages (nodes or settings); the design and the API are the same."


def collect_checks(outputs: dict) -> list[dict]:
    """Every Check that ran, wherever it sits: its node path, pass/fail and evidence."""
    return [
        {"node": key, "passed": bool(item.get("passed")), "evidence": item.get("evidence")}
        for key, value in outputs.items()
        if isinstance(value, dict)
        for item in value.get("aws_check") or []
        if isinstance(item, dict)
    ]


def collect_files(outputs: dict) -> list[dict]:
    """Files the run wrote (under output/), as ComfyUI download links."""
    from urllib.parse import urlencode

    files = []
    for value in outputs.values():
        for rel in (value.get("aws_files") or []) if isinstance(value, dict) else []:
            folder, _, name = str(rel).rpartition("/")
            files.append(
                {
                    "path": f"output/{rel}",
                    "url": "/view?"
                    + urlencode({"filename": name, "subfolder": folder, "type": "output"}),
                }
            )
    return files


def prompt_hash(prompt: dict) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(prompt, sort_keys=True).encode()).hexdigest()[:16]


JSON_KINDS = {"text": "string", "string": "string", "number": "number", "float": "number"}
JSON_KINDS |= {"integer": "integer", "int": "integer", "boolean": "boolean", "bool": "boolean"}
JSON_KINDS |= {"file": "string", "folder": "string"}
PROBLEM = {
    "description": "RFC 9457 Problem Details: what was wrong and how to fix it.",
    "content": {
        "application/problem+json": {
            "schema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "title": {"type": "string"},
                    "status": {"type": "integer"},
                    "detail": {"type": "string"},
                },
            }
        }
    },
}


def _design(spec: dict) -> dict:
    """The contract and design the published workflow holds (empty when it has none)."""
    from .native_layers import layers

    return layers(spec.get("workflow")) or {}


def _output_schema(name: str, contract: dict) -> dict:
    """An output's schema: a table's columns from its design entity, else its promised kind."""
    promised = {o["name"]: o for o in (contract.get("promises") or {}).get("outputs") or []}
    item = promised.get(name, {})
    port = (item.get("from") or ".").split(".")[1]
    data = contract.get("data") or {}
    entity = data.get(name) or data.get(port) or {}
    about = item.get("description") or entity.get("description") or ""
    if entity.get("fields"):
        return {
            "type": "object",
            "description": about,
            "properties": {
                "columns": {"type": "array", "items": {"enum": entity["fields"]}},
                "rows": {
                    "type": "array",
                    "items": {"type": "array"},
                    "description": "Up to 10,000 rows inline; the whole table is the file in files.",
                },
                "total_rows": {"type": "integer"},
            },
            **({"x-key": entity["key"]} if entity.get("key") else {}),
        }
    kind = JSON_KINDS.get(str(item.get("kind", "")).lower())
    return {**({"type": kind} if kind else {}), "description": about}


def _openapi_run(spec: dict, base: str = "") -> dict:
    """An OpenAPI description of the call, generated from the signature and the workflow's contract."""
    contract = _design(spec)
    promised = {i["name"]: i for i in (contract.get("promises") or {}).get("inputs") or []}
    data_kinds = data_inputs(spec)
    properties = {}
    for item in spec["signature"].get("inputs", []):
        promise = promised.get(item["name"], {})
        kind = data_kinds.get(item["name"])
        properties[item["name"]] = {
            # A data folder is a name (uploaded first), not one of the folders there were at publish time.
            **(
                {"type": "string"}
                if kind
                else {"type": JSON_TYPES[item["type"]]}
                if item.get("type") in JSON_TYPES
                else {}
            ),
            **(
                {"enum": item["choices"]}
                if isinstance(item.get("choices"), list) and not kind
                else {}
            ),
            **({"default": item["default"]} if "default" in item else {}),
            **({"examples": [promise["example"]]} if "example" in promise else {}),
            **({"format": f"comfyui-input-{kind}"} if kind else {}),
            # A contract's promise says what it holds; never invent that (the API lint asks for it).
            "description": (
                promise.get("description", "")
                if contract
                else f"{item.get('title', '')} · {item['widget']}"
            )
            + (
                f" A {kind} name inside ComfyUI's input folder: upload it first (POST /upload/image)."
                if kind and promise.get("description")
                else ""
            ),
        }
    body = {
        "type": "object",
        "properties": {
            "inputs": {"type": "object", "properties": properties, "additionalProperties": False},
            "timeout": {
                "type": "number",
                "description": "Seconds to wait (default 600, at most 3600).",
            },
        },
    }
    examples = {
        e["id"]: {"summary": e.get("note") or e["id"], "value": {"inputs": e.get("given") or {}}}
        for e in contract.get("examples") or []
    }
    data = {o["name"]: _output_schema(o["name"], contract) for o in spec["signature"]["outputs"]}
    result = {
        "type": "object",
        "properties": {
            "status": {"enum": ["completed", "failed", "in_progress"]},
            "semver": {"type": "string"},
            "version": {"type": "string"},
            "prompt_id": {"type": "string", "description": "The native ComfyUI job id."},
            "data": {"type": "object", "properties": data},
            "files": {
                "type": "array",
                "description": "Files the run wrote, with their native /view download address.",
                "items": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "url": {"type": "string"}},
                },
            },
            "checks": {
                "type": "array",
                "description": "Every Check that ran: the workflow verifying itself.",
            },
            "execution_error": {"type": "object"},
            "poll": {"type": "string"},
            "cancel": {"type": "string"},
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": spec["name"],
            "version": spec.get("version", ""),
            "description": spec.get("description", ""),
        },
        "servers": [{"url": base}] if base else [],
        "paths": {
            f"/agent-workflow-studio/api/{spec['name']}/run": {
                "post": {
                    "operationId": f"run_{spec['name']}",
                    "summary": f"Run {spec['name']} and wait for its result",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": body,
                                **({"examples": examples} if examples else {}),
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "The run finished (completed or failed), or is still in progress.",
                            "content": {"application/json": {"schema": result}},
                        },
                        "400": PROBLEM,
                        "404": PROBLEM,
                    },
                }
            }
        },
    }


def openapi(spec: dict, base: str = "") -> dict:
    """An OpenAPI description of the call (run, or start a native job and poll it), plus the native
    upload a data input needs first (claim check)."""
    doc = _openapi_run(spec, base)
    doc["info"]["version"] = spec.get("semver") or doc["info"]["version"]
    run = doc["paths"][f"/agent-workflow-studio/api/{spec['name']}/run"]["post"]
    doc["paths"][f"/agent-workflow-studio/api/{spec['name']}/jobs"] = {
        "post": {
            "operationId": f"start_{spec['name']}_job",
            "summary": "Start the same call as a native ComfyUI job; poll or cancel it at the returned addresses",
            "requestBody": run["requestBody"],
            "responses": {
                "200": {"description": "Queued: {job, status: pending, poll, cancel}."},
                "400": PROBLEM,
                "404": PROBLEM,
            },
        }
    }
    doc["paths"]["/api/jobs/{job}"] = {
        "get": {
            "operationId": "get_job",
            "summary": "ComfyUI's native job: status (pending, in_progress, completed, failed, cancelled) "
            "and, when done, every node's outputs (the API outputs under aws_output, files under files)",
            "parameters": [
                {"name": "job", "in": "path", "required": True, "schema": {"type": "string"}}
            ],
            "responses": {"200": {"description": "The job."}, "404": PROBLEM},
        }
    }
    doc["paths"]["/api/jobs/{job}/cancel"] = {
        "post": {
            "operationId": "cancel_job",
            "summary": "Cancel a pending or running native job",
            "parameters": [
                {"name": "job", "in": "path", "required": True, "schema": {"type": "string"}}
            ],
            "responses": {
                "200": {"description": "Cancelled (or already finished)."},
                "404": PROBLEM,
            },
        }
    }
    if data_inputs(spec):
        doc["paths"]["/upload/image"] = {
            "post": {
                "operationId": "upload_input",
                "summary": "ComfyUI's native upload, for any data file: put each file into "
                "input/<folder>/, then pass that folder (or file) name as the data input",
                "requestBody": {
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "image": {
                                        "type": "string",
                                        "format": "binary",
                                        "description": "The file (any type).",
                                    },
                                    "type": {"enum": ["input"]},
                                    "subfolder": {
                                        "type": "string",
                                        "description": "The folder name to pass as the input.",
                                    },
                                    "overwrite": {"enum": ["true", "false"]},
                                },
                                "required": ["image"],
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "{name, subfolder, type}"}, "400": PROBLEM},
            }
        }
    return doc


def install_api_routes() -> None:
    import asyncio
    import time

    import aiohttp
    import folder_paths
    from aiohttp import web
    from server import PromptServer

    from .architecture import COMPLETENESS, check
    from .native_layers import layers
    from .project_repo import ProjectRepo, projects_root
    from .routes import _is_local_request
    from .workflow_contract import hld

    routes, root = PromptServer.instance.routes, projects_root()

    def load(name: str) -> dict:
        """The latest published version, or name@1 / name@1.2 / name@1.2.3 for a pinned one."""
        name, _, wanted = name.partition("@")
        path = published_dir(root) / f"{name}.json"
        if not NAME.fullmatch(name) or not path.is_file():
            raise NotFound(f"No published API named {name!r}.")
        if not wanted:
            return json.loads(path.read_text(encoding="utf-8"))
        versions = sorted(
            (
                p
                for p in (published_dir(root) / name).glob("*.json")
                if f"{p.stem}.".startswith(f"{wanted}.")
            ),
            key=lambda p: tuple(int(x) for x in p.stem.split(".")),
        )
        if not versions:
            raise NotFound(f"{name} has no published version {wanted}.")
        return json.loads(versions[-1].read_text(encoding="utf-8"))

    def problem(status: int, title: str, detail: str):
        """An error as RFC 9457 Problem Details (`error` kept for the studio's own panels)."""
        body = {"type": "about:blank", "title": title, "status": status, "detail": detail}
        return web.json_response(
            {**body, "error": detail}, status=status, content_type="application/problem+json"
        )

    async def guarded(request, handler):
        if not _is_local_request(request):
            return problem(403, "Forbidden", "Local access only: these APIs serve this machine.")
        try:
            return web.json_response(await handler())
        except NotFound as error:
            return problem(404, "Not found", str(error))
        except (ValueError, json.JSONDecodeError) as error:
            return problem(400, "Bad request", str(error))

    @routes.post("/agent-workflow-studio/projects/{workflow_id}/publish")
    async def publish(request):
        async def handler():
            spec = check_spec(await request.json())
            # Publishing is a release: the structure must hold and the release items be done.
            found = check(spec.get("workflow") or {})
            blocking = [f"{v['where']}: {v['problem']}" for v in found if v["kind"] != COMPLETENESS]
            if blocking:
                raise ValueError("Not ready to publish: " + "; ".join(blocking))
            repo = ProjectRepo(root, request.match_info["workflow_id"])
            # A release needs a green CI run (design, stage Checks, examples, no regression) of
            # exactly this graph.
            ci = None
            if layers(spec["workflow"]) or repo.load_contract():
                runs = repo.records("pipeline")
                ci = runs[-1] if runs else None
                if not ci or ci["prompt_hash"] != prompt_hash(spec["prompt"]):
                    raise ValueError("Run the checks on this exact workflow first; all must pass.")
                if not ci["green"]:
                    raise ValueError(f"The checks did not all pass: {ci['summary']}.")
            # A publish is always its own version in the history, with what was published.
            published = {"kind": "publish", "name": spec["name"], "signature": spec["signature"]}
            message = f"Publish API {spec['name']}"
            sha = await asyncio.to_thread(
                repo.add_record, published, message, spec["workflow"], "user"
            )
            contract = repo.load_contract()
            # The approved HLD; without a contract there is no design, so only the signature can bump.
            design = prompt_hash({"hld": hld(contract)}) if contract else ""
            latest = published_dir(root) / f"{spec['name']}.json"
            previous = json.loads(latest.read_text(encoding="utf-8")) if latest.is_file() else None
            record = {
                **{k: spec[k] for k in ("name", "prompt", "workflow", "signature")},
                "description": str(spec.get("description", ""))[:500],
                "workflow_id": repo.id,
                "version": sha,
                "semver": next_version(previous, spec["signature"], design),
                "notes": release_notes(previous, spec["signature"], design),
                "design": design,
                # What passed in this release: later releases must not lose it (regression).
                "checks": (ci or {}).get("checks"),
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            (published_dir(root) / spec["name"]).mkdir(parents=True, exist_ok=True)
            data = json.dumps(record, ensure_ascii=False)
            (published_dir(root) / spec["name"] / f"{record['semver']}.json").write_text(
                data, encoding="utf-8"
            )
            latest.write_text(data, encoding="utf-8")
            return {
                "name": spec["name"],
                "semver": record["semver"],
                "version": sha[:8],
                "notes": record["notes"],
                "endpoint": f"/agent-workflow-studio/api/{spec['name']}/run",
                "openapi": f"/agent-workflow-studio/api/{spec['name']}/openapi.json",
                "todo": [
                    f"{v['where']}: {v['problem']}" for v in found if v["kind"] == COMPLETENESS
                ],
            }

        return await guarded(request, handler)

    def latest_published(workflow_id: str) -> dict | None:
        """The newest release of this workflow under any API name, for the regression check."""
        found = [s for _, s in published_specs() if s.get("workflow_id") == workflow_id]
        return max(found, key=lambda s: s.get("published_at", ""), default=None)

    @routes.post("/agent-workflow-studio/projects/{workflow_id}/pipeline")
    async def pipeline_run(request):
        """One CI run of the workflow as it is in the browser; kept as a version of the workflow."""

        async def handler():
            from .pipeline import run

            evidence = await request.json()
            if not isinstance(evidence, dict) or not isinstance(evidence.get("nodes"), list):
                raise ValueError("Send the workflow's evidence (contractEvidence).")
            repo = ProjectRepo(root, request.match_info["workflow_id"])
            contract = layers(evidence.get("workflow")) or repo.load_contract()
            if not contract:
                raise ValueError(
                    "This workflow has no contract: there is nothing to check it against."
                )
            result = run(contract, evidence, repo.records("acceptance"), latest_published(repo.id))
            sha = await asyncio.to_thread(
                repo.add_record,
                {"kind": "pipeline", **result},
                f"Checks · {result['summary']}",
                evidence.get("workflow") or {},
                str(evidence.get("author") or "user"),
            )
            return {**result, "version": sha[:8]}

        return await guarded(request, handler)

    @routes.post("/agent-workflow-studio/api/{name}/rollback")
    async def rollback(request):
        """Point the API back at an earlier release (callers pinned to name@x.y.z are unaffected)."""

        async def handler():
            name, semver = request.match_info["name"], str((await request.json()).get("semver", ""))
            if not NAME.fullmatch(name) or not re.fullmatch(r"\d+\.\d+\.\d+", semver):
                raise ValueError("Give the API name and a version such as 1.2.0.")
            path = published_dir(root) / name / f"{semver}.json"
            if not path.is_file():
                raise NotFound(f"{name} has no version {semver}.")
            spec = json.loads(path.read_text(encoding="utf-8"))
            (published_dir(root) / f"{name}.json").write_text(
                json.dumps(spec, ensure_ascii=False), encoding="utf-8"
            )
            repo = ProjectRepo(root, spec["workflow_id"])
            # A release event in the workflow's history; the workflow itself stays as it is now.
            current = repo.tree / "workflow.json"
            await asyncio.to_thread(
                repo.add_record,
                {"kind": "rollback", "name": name, "semver": semver},
                f"Roll back API {name} to {semver}",
                json.loads(current.read_text(encoding="utf-8"))
                if current.is_file()
                else spec["workflow"],
                "user",
            )
            return {"name": name, "semver": semver}

        return await guarded(request, handler)

    @routes.post("/agent-workflow-studio/projects/{workflow_id}/acceptance")
    async def acceptance(request):
        async def handler():
            spec = await request.json()
            repo = ProjectRepo(root, request.match_info["workflow_id"])
            # The examples of the workflow as it is now (the canvas), else of its last version.
            contract = layers(spec.get("workflow")) or repo.load_contract() or {}
            examples = contract.get("examples") or []
            if not examples:
                raise ValueError("The contract has no examples to run.")
            check_spec({**spec, "name": "acceptance"})
            names = {i["name"] for i in spec["signature"].get("inputs", [])}
            results = []
            for example in examples:
                given = {k: v for k, v in example["given"].items() if k in names}
                prompt_id, history = await run_prompt(
                    bind_inputs(spec, given), spec["workflow"], 900
                )
                outputs = (history or {}).get("outputs") or {}
                ok = bool(history) and history["status"].get("status_str") == "success"
                misses = (
                    compare_example(
                        example["expect"], collect_data(outputs), collect_outputs(spec, outputs)
                    )
                    if ok
                    else ["the run did not succeed"]
                )
                results.append(
                    {
                        "example": example["id"],
                        "passed": not misses,
                        "misses": misses,
                        "unused_given": sorted(set(example["given"]) - names),
                        "prompt_id": prompt_id,
                    }
                )
            passed = sum(r["passed"] for r in results)
            record = {
                "kind": "acceptance",
                "prompt_hash": prompt_hash(spec["prompt"]),
                "passed": passed,
                "total": len(results),
                "results": results,
            }
            message = f"Acceptance · {passed}/{len(results)} examples passed"
            sha = await asyncio.to_thread(repo.add_record, record, message, spec["workflow"])
            return {**record, "version": sha[:8]}

        return await guarded(request, handler)

    @routes.get("/agent-workflow-studio/api")
    async def listing(request):
        async def handler():
            items = []
            for path in sorted(published_dir(root).glob("*.json")):
                spec = json.loads(path.read_text(encoding="utf-8"))
                items.append(
                    {
                        k: spec.get(k)
                        for k in (
                            "name",
                            "workflow_id",
                            "version",
                            "semver",
                            "published_at",
                            "description",
                            "notes",
                            "watch",
                        )
                    }
                    | {
                        # Every release kept, oldest first: what a rollback can return to.
                        "versions": sorted(
                            (p.stem for p in (published_dir(root) / spec["name"]).glob("*.json")),
                            key=lambda v: tuple(int(x) for x in v.split(".")),
                        ),
                        "inputs": [i["name"] for i in spec["signature"].get("inputs", [])],
                        "data_inputs": data_inputs(spec),
                        "outputs": [o["name"] for o in spec["signature"]["outputs"]],
                    }
                )
            return {"apis": items}

        return await guarded(request, handler)

    @routes.get("/agent-workflow-studio/api/{name}/openapi.json")
    async def describe(request):
        async def handler():
            return openapi(load(request.match_info["name"]), f"{request.scheme}://{request.host}")

        return await guarded(request, handler)

    async def queue_prompt(prompt: dict, workflow: dict) -> str:
        """Queue a prompt on ComfyUI's own queue: its id is a native job (/api/jobs/{id})."""
        base = f"http://127.0.0.1:{PromptServer.instance.port}"
        async with aiohttp.ClientSession() as session:
            # The workflow rides along as ComfyUI's own run metadata: nodes find their project by it.
            payload = {"prompt": prompt, "extra_data": {"extra_pnginfo": {"workflow": workflow}}}
            async with session.post(f"{base}/prompt", json=payload) as response:
                queued = await response.json()
        if response.status != 200:
            raise ValueError(
                f"ComfyUI refused the run: {json.dumps(queued.get('error') or queued)[:500]}"
            )
        return queued["prompt_id"]

    async def run_prompt(prompt: dict, workflow: dict, timeout: float) -> tuple[str, dict | None]:
        """Queue a prompt and wait: (prompt id, history, or None if still running)."""
        base = f"http://127.0.0.1:{PromptServer.instance.port}"
        prompt_id, waited = await queue_prompt(prompt, workflow), 0.0
        async with aiohttp.ClientSession() as session:
            while waited < timeout:
                async with session.get(f"{base}/history/{prompt_id}") as response:
                    history = (await response.json()).get(prompt_id)
                if history and history.get("status", {}).get("completed") is not None:
                    return prompt_id, history
                await asyncio.sleep(0.5)
                waited += 0.5
        return prompt_id, None

    def bound(spec: dict, inputs: dict) -> dict:
        """The prompt for one call, or 400 naming every input to fix (data uploaded first)."""
        prompt = bind_inputs(spec, inputs)
        if missing := missing_data(spec, inputs, Path(folder_paths.get_input_directory())):
            raise ValueError("; ".join(missing))
        return prompt

    def job_links(prompt_id: str) -> dict:
        return {"poll": f"/api/jobs/{prompt_id}", "cancel": f"/api/jobs/{prompt_id}/cancel"}

    async def execute(spec: dict, inputs: dict, timeout: float) -> dict:
        """One call: fill the inputs, run, return the promised outputs, files and the version that ran.
        Its status speaks ComfyUI's job vocabulary (completed, failed; in_progress with the native
        job's address when it outlasts the wait)."""
        prompt = bound(spec, inputs)
        version = {"semver": spec.get("semver"), "version": spec["version"][:8]}
        prompt_id, history = await run_prompt(prompt, spec["workflow"], timeout)
        if history is None:
            return {
                "status": "in_progress",
                "prompt_id": prompt_id,
                **version,
                **job_links(prompt_id),
            }
        status, outputs = history["status"], history.get("outputs") or {}
        error = next((m[1] for m in status.get("messages", []) if m[0] == "execution_error"), None)
        return {
            "status": "completed" if status.get("status_str") == "success" else "failed",
            **version,
            "prompt_id": prompt_id,
            "data": collect_data(outputs),
            "files": collect_files(outputs),
            "checks": collect_checks(outputs),
            "outputs": collect_outputs(spec, outputs),
            **(
                {"execution_error": {k: error.get(k) for k in ("node_type", "exception_message")}}
                if error
                else {}
            ),
        }

    async def call_body(request) -> tuple[dict, float]:
        body = (await request.json() if request.can_read_body else {}) or {}
        return body.get("inputs") or {}, min(float(body.get("timeout", 600)), 3600)

    @routes.post("/agent-workflow-studio/api/{name}/run")
    async def run(request):
        async def handler():
            spec = load(request.match_info["name"])
            inputs, timeout = await call_body(request)
            return await execute(spec, inputs, timeout)

        return await guarded(request, handler)

    # The outer loop: a published workflow watched on a schedule; each run's Checks become a version.
    def published_specs() -> list[tuple[Path, dict]]:
        folder = published_dir(root)
        return (
            [(p, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(folder.glob("*.json"))]
            if folder.is_dir()
            else []
        )

    @routes.post("/agent-workflow-studio/api/{name}/watch")
    async def set_watch(request):
        async def handler():
            name = request.match_info["name"]
            spec = load(name)
            body = (await request.json()) or {}
            every = float(body.get("every_minutes", 0))
            inputs = body.get("inputs") or {}
            bound(spec, inputs)  # refuse bad inputs (and missing data) now
            if every and every < 0.05:
                raise ValueError("Watch at most every 3 seconds (every_minutes ≥ 0.05).")
            spec["watch"] = {"every_minutes": every, "inputs": inputs} if every else None
            (published_dir(root) / f"{name}.json").write_text(
                json.dumps(spec, ensure_ascii=False), encoding="utf-8"
            )
            return {"name": name, "watch": spec["watch"]}

        return await guarded(request, handler)

    async def watch_once(path: Path, spec: dict) -> None:
        result = await execute(spec, spec["watch"]["inputs"], 3600)
        checks = result.get("checks") or []
        ok = result["status"] == "completed" and all(c["passed"] for c in checks)
        failing = [c for c in checks if not c["passed"]]
        record = {
            "kind": "watch",
            "name": spec["name"],
            "semver": spec.get("semver"),
            "status": result["status"],
            "passing": ok,
            "checks": f"{len(checks) - len(failing)}/{len(checks)}",
            "failing": failing[:10],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        repo = ProjectRepo(root, spec["workflow_id"])
        message = (
            f"Watch {spec['name']} · {'passing' if ok else 'FAILING'} · checks {record['checks']}"
        )
        # Tell anyone with the studio open (ComfyUI's own websocket): a failure, or the recovery.
        if not ok or (spec["watch"].get("last") or {}).get("passing") is False:
            PromptServer.instance.send_sync(
                "aws.watch",
                {
                    "name": spec["name"],
                    "semver": spec.get("semver"),
                    "passing": ok,
                    "checks": record["checks"],
                    "failing": [str(c.get("node")) for c in failing][:5],
                    "workflow_id": spec["workflow_id"],
                },
            )
        await asyncio.to_thread(repo.add_record, record, message, spec["workflow"])
        latest = json.loads(
            path.read_text(encoding="utf-8")
        )  # the spec may have been republished meanwhile
        if latest.get("watch"):
            latest["watch"]["last"] = {k: record[k] for k in ("at", "passing", "checks", "status")}
            path.write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")

    async def watcher() -> None:
        import logging

        while True:
            await asyncio.sleep(5)
            for path, spec in published_specs():
                watch = spec.get("watch")
                if not watch:
                    continue
                last = (watch.get("last") or {}).get("at")
                due = (
                    not last
                    or time.time() - time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%S"))
                    >= watch["every_minutes"] * 60
                )
                if due:
                    try:
                        await watch_once(path, spec)
                    except Exception:  # noqa: BLE001 — a failed watch is logged; the watcher keeps running
                        logging.exception("Watch %s failed", spec.get("name"))

    async def start_watcher(app) -> None:
        app["aws_watcher"] = asyncio.get_running_loop().create_task(watcher())

    PromptServer.instance.app.on_startup.append(start_watcher)

    # Long runs as native ComfyUI jobs: queued like any run; poll or cancel them at /api/jobs/{id}.
    @routes.post("/agent-workflow-studio/api/{name}/jobs")
    async def start_job(request):
        async def handler():
            spec = load(request.match_info["name"])
            inputs, _ = await call_body(request)
            prompt_id = await queue_prompt(bound(spec, inputs), spec["workflow"])
            return {
                "job": prompt_id,
                "status": "pending",
                "semver": spec.get("semver"),
                "version": spec["version"][:8],
                **job_links(prompt_id),
            }

        return await guarded(request, handler)


__all__ = [
    "bind_inputs",
    "check_spec",
    "collect_data",
    "collect_checks",
    "collect_files",
    "collect_outputs",
    "compare_example",
    "install_api_routes",
    "next_version",
    "openapi",
    "prompt_hash",
]
