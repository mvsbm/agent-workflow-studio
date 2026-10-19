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


def openapi(spec: dict, base: str = "") -> dict:
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


def install_api_routes() -> None:
    import asyncio
    import time

    import aiohttp
    from aiohttp import web
    from server import PromptServer

    from .architecture import COMPLETENESS, check
    from .project_repo import ProjectRepo, projects_root
    from .routes import _is_local_request

    routes, root = PromptServer.instance.routes, projects_root()

    def load(name: str) -> dict:
        path = published_dir(root) / f"{name}.json"
        if not NAME.fullmatch(name) or not path.is_file():
            raise ValueError(f"No published API named {name!r}.")
        return json.loads(path.read_text(encoding="utf-8"))

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
            sha = await asyncio.to_thread(
                repo.commit, spec["workflow"], f"Publish API {spec['name']}", "user"
            )
            record = {
                **{k: spec[k] for k in ("name", "prompt", "workflow", "signature")},
                "description": str(spec.get("description", ""))[:500],
                "workflow_id": repo.id,
                "version": sha,
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            published_dir(root).mkdir(parents=True, exist_ok=True)
            (published_dir(root) / f"{spec['name']}.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
            return {
                "name": spec["name"],
                "version": sha[:8],
                "endpoint": f"/agent-workflow-studio/api/{spec['name']}/run",
                "openapi": f"/agent-workflow-studio/api/{spec['name']}/openapi.json",
                "todo": [
                    f"{v['where']}: {v['problem']}" for v in found if v["kind"] == COMPLETENESS
                ],
            }

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
                        for k in ("name", "workflow_id", "version", "published_at", "description")
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

    @routes.post("/agent-workflow-studio/api/{name}/run")
    async def run(request):
        async def handler():
            spec = load(request.match_info["name"])
            body = await request.json() if request.can_read_body else {}
            prompt = bind_inputs(spec, (body or {}).get("inputs") or {})
            timeout = min(float((body or {}).get("timeout", 600)), 3600)
            base = f"http://127.0.0.1:{PromptServer.instance.port}"
            async with aiohttp.ClientSession() as session:
                # The workflow rides along as ComfyUI's own run metadata: nodes find their project by it.
                payload = {
                    "prompt": prompt,
                    "extra_data": {"extra_pnginfo": {"workflow": spec["workflow"]}},
                }
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
                        break
                    await asyncio.sleep(0.5)
                    waited += 0.5
                else:
                    return {
                        "status": "running",
                        "prompt_id": prompt_id,
                        "version": spec["version"][:8],
                    }
            status = history["status"]
            errors = [m[1] for m in status.get("messages", []) if m[0] == "execution_error"]
            return {
                "status": "success" if status.get("status_str") == "success" else "error",
                "version": spec["version"][:8],
                "prompt_id": prompt_id,
                "outputs": collect_outputs(spec, history.get("outputs") or {}),
                **(
                    {
                        "errors": [
                            {k: e.get(k) for k in ("node_type", "exception_message")}
                            for e in errors
                        ]
                    }
                    if errors
                    else {}
                ),
            }

        return await guarded(request, handler)


__all__ = ["bind_inputs", "check_spec", "collect_outputs", "install_api_routes", "openapi"]
