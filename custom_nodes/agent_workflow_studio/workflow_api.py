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
        if isinstance(item.get("choices"), list) and value not in item["choices"]:
            raise ValueError(
                f"Input {name} must be one of: {', '.join(map(str, item['choices']))}."
            )
        for node, widget in targets(item):
            prompt[str(node)]["inputs"][widget] = value
    return prompt


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


def signature_key(signature: dict) -> tuple:
    """What callers depend on: input names and types, output names."""
    return (
        sorted((i["name"], str(i.get("type"))) for i in signature.get("inputs", [])),
        sorted(o["name"] for o in signature.get("outputs", [])),
    )


def next_version(previous: dict | None, signature: dict, design: str) -> str:
    """Semantic version from what changed: the API (inputs/outputs) → major, the design (HLD) →
    minor, anything else (inside a stage) → patch."""
    if not previous or "semver" not in previous:
        return "1.0.0"
    major, minor, patch = (int(x) for x in previous["semver"].split("."))
    if signature_key(previous["signature"]) != signature_key(signature):
        return f"{major + 1}.0.0"
    if previous.get("design") != design:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


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


def _openapi_run(spec: dict, base: str = "") -> dict:
    """An OpenAPI description of the call, generated from the signature."""
    properties = {
        item["name"]: {
            **({"type": JSON_TYPES[item["type"]]} if item.get("type") in JSON_TYPES else {}),
            **({"enum": item["choices"]} if isinstance(item.get("choices"), list) else {}),
            **({"default": item["default"]} if "default" in item else {}),
            "description": item.get("description") or f"{item.get('title', '')} · {item['widget']}",
        }
        for item in spec["signature"].get("inputs", [])
    }
    outputs = {
        item["name"]: {"type": "object", "description": item.get("title", "")}
        for item in spec["signature"]["outputs"]
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
                    "summary": f"Run {spec['name']} at version {spec.get('version', '')[:8]}",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "inputs": {
                                            "type": "object",
                                            "properties": properties,
                                            "additionalProperties": False,
                                        }
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "The run finished.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"enum": ["success", "error"]},
                                            "version": {"type": "string"},
                                            "prompt_id": {"type": "string"},
                                            "outputs": {"type": "object", "properties": outputs},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


def openapi(spec: dict, base: str = "") -> dict:
    """An OpenAPI description of the call (run, or start a job and poll it), from the signature."""
    doc = _openapi_run(spec, base)
    doc["info"]["version"] = spec.get("semver") or doc["info"]["version"]
    run = doc["paths"][f"/agent-workflow-studio/api/{spec['name']}/run"]["post"]
    doc["paths"][f"/agent-workflow-studio/api/{spec['name']}/jobs"] = {
        "post": {
            "summary": "Start the same call as a job (for long runs); poll it at the returned address",
            "requestBody": run["requestBody"],
            "responses": {"200": {"description": "The job started: {job, status, poll}."}},
        }
    }
    doc["paths"]["/agent-workflow-studio/api/jobs/{job}"] = {
        "get": {
            "summary": "A job's status, and when done the same result as run",
            "parameters": [
                {"name": "job", "in": "path", "required": True, "schema": {"type": "string"}}
            ],
            "responses": run["responses"],
        }
    }
    return doc


def install_api_routes() -> None:
    import asyncio
    import time

    import aiohttp
    from aiohttp import web
    from server import PromptServer

    from .architecture import COMPLETENESS, check
    from .project_repo import ProjectRepo, projects_root
    from .routes import _is_local_request
    from .workflow_contract import hld

    routes, root = PromptServer.instance.routes, projects_root()

    def load(name: str) -> dict:
        """The latest published version, or name@1 / name@1.2 / name@1.2.3 for a pinned one."""
        name, _, wanted = name.partition("@")
        path = published_dir(root) / f"{name}.json"
        if not NAME.fullmatch(name) or not path.is_file():
            raise ValueError(f"No published API named {name!r}.")
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
            raise ValueError(f"{name} has no published version {wanted}.")
        return json.loads(versions[-1].read_text(encoding="utf-8"))

    async def guarded(request, handler):
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        try:
            return web.json_response(await handler())
        except (ValueError, json.JSONDecodeError) as error:
            return web.json_response({"error": str(error)}, status=400)

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
            # The contract's examples are acceptance tests: they must have passed on exactly this graph.
            if (repo.load_contract() or {}).get("examples"):
                last = last_acceptance(repo)
                if (
                    not last
                    or last["prompt_hash"] != prompt_hash(spec["prompt"])
                    or last["passed"] < last["total"]
                ):
                    raise ValueError(
                        "Run the contract's examples (acceptance tests) on this exact workflow first; all must pass."
                    )
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
                "design": design,
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
                "endpoint": f"/agent-workflow-studio/api/{spec['name']}/run",
                "openapi": f"/agent-workflow-studio/api/{spec['name']}/openapi.json",
                "todo": [
                    f"{v['where']}: {v['problem']}" for v in found if v["kind"] == COMPLETENESS
                ],
            }

        return await guarded(request, handler)

    def last_acceptance(repo) -> dict | None:
        found = repo.records("acceptance")
        return found[-1] if found else None

    @routes.post("/agent-workflow-studio/projects/{workflow_id}/acceptance")
    async def acceptance(request):
        async def handler():
            spec = await request.json()
            repo = ProjectRepo(root, request.match_info["workflow_id"])
            examples = (repo.load_contract() or {}).get("examples") or []
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
                            "watch",
                        )
                    }
                    | {
                        "inputs": [i["name"] for i in spec["signature"].get("inputs", [])],
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

    async def run_prompt(prompt: dict, workflow: dict, timeout: float) -> tuple[str, dict | None]:
        """Queue a prompt on ComfyUI's own queue and wait: (prompt id, history, or None if still running)."""
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
            prompt_id, waited = queued["prompt_id"], 0.0
            while waited < timeout:
                async with session.get(f"{base}/history/{prompt_id}") as response:
                    history = (await response.json()).get(prompt_id)
                if history and history.get("status", {}).get("completed") is not None:
                    return prompt_id, history
                await asyncio.sleep(0.5)
                waited += 0.5
        return prompt_id, None

    async def execute(spec: dict, inputs: dict, timeout: float) -> dict:
        """One call: fill the inputs, run, return the promised outputs, files and the version that ran."""
        prompt = bind_inputs(spec, inputs)
        version = {"semver": spec.get("semver"), "version": spec["version"][:8]}
        prompt_id, history = await run_prompt(prompt, spec["workflow"], timeout)
        if history is None:
            return {"status": "running", "prompt_id": prompt_id, **version}
        status, outputs = history["status"], history.get("outputs") or {}
        errors = [m[1] for m in status.get("messages", []) if m[0] == "execution_error"]
        return {
            "status": "success" if status.get("status_str") == "success" else "error",
            **version,
            "prompt_id": prompt_id,
            "data": collect_data(outputs),
            "files": collect_files(outputs),
            "checks": collect_checks(outputs),
            "outputs": collect_outputs(spec, outputs),
            **(
                {
                    "errors": [
                        {k: e.get(k) for k in ("node_type", "exception_message")} for e in errors
                    ]
                }
                if errors
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
            bind_inputs(spec, inputs)  # refuse bad inputs now
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
        ok = result["status"] == "success" and all(c["passed"] for c in checks)
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

    # Long runs as jobs: start, then poll. Kept in memory: a restart forgets unfinished jobs.
    jobs: dict[str, dict] = {}

    @routes.post("/agent-workflow-studio/api/{name}/jobs")
    async def start_job(request):
        async def handler():
            import uuid

            spec = load(request.match_info["name"])
            inputs, timeout = await call_body(request)
            bind_inputs(spec, inputs)  # refuse bad inputs now, not later
            job = uuid.uuid4().hex[:16]
            jobs[job] = {
                "status": "running",
                "semver": spec.get("semver"),
                "version": spec["version"][:8],
            }

            async def work():
                try:
                    jobs[job] = await execute(spec, inputs, max(timeout, 3600))
                except Exception as error:  # noqa: BLE001 — a job reports its failure instead of vanishing
                    jobs[job] = {
                        "status": "error",
                        "errors": [{"exception_message": str(error)[:500]}],
                    }

            asyncio.get_running_loop().create_task(work())
            return {
                "job": job,
                "status": "running",
                "poll": f"/agent-workflow-studio/api/jobs/{job}",
            }

        return await guarded(request, handler)

    @routes.get("/agent-workflow-studio/api/jobs/{job}")
    async def job_status(request):
        async def handler():
            if request.match_info["job"] not in jobs:
                raise ValueError("No such job (jobs are kept until the studio restarts).")
            return {"job": request.match_info["job"], **jobs[request.match_info["job"]]}

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
