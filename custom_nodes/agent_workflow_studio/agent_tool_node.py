"""Agent Tool node: runs a user-approved agent tool from this workflow's history, confined."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from comfy_api.latest import io

from .agent_tools import NAME, protections, run_folder, run_tool, value_problems
from .data_preview import data_files, flow_tables
from .datasets import is_ref
from .node_progress import node_id_for, report_node_progress
from .project_repo import ProjectRepo, projects_root
from .standards import custom, dataset_sockets, port_socket


class AgentToolNode(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowAgentTool",
            display_name="Agent Tool",
            category="agent_workflow_studio/tools",
            search_aliases=["tool", "python", "agent", "code"],
            description=(
                "Runs a tool the agent wrote and you approved for this workflow (shown in Versions). "
                "Only the approved code runs, in a separate confined process with a time limit."
            ),
            inputs=[
                io.String.Input(
                    "tool",
                    display_name="Tool",
                    default="",
                    tooltip="Name of an approved tool in this workflow.",
                ),
                io.String.Input(
                    "text",
                    display_name="Text input",
                    multiline=False,
                    optional=True,
                    default="",
                    tooltip="Passed to the tool as inputs['text'].",
                ),
                io.MultiType.Input(
                    "data",
                    # Any dataset the standard registers (a new domain's classes included).
                    types=[io.Dict, *(custom(s) for s in dataset_sockets())],
                    display_name="Data input",
                    optional=True,
                    tooltip="Connect a DICT; its keys are passed to the tool as inputs.",
                ),
                io.String.Input(
                    "input_folder",
                    display_name="Input folder",
                    optional=True,
                    default="",
                    tooltip="A folder inside ComfyUI's input directory the tool may read (not change), "
                    "as TOOL_INPUT_DIR. Empty: no input files.",
                ),
            ],
            outputs=[io.Dict.Output(display_name="result"), io.String.Output(display_name="text")],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        # Approved code can change between runs under the same name; always run.
        return float("nan")

    @classmethod
    def execute(
        cls, tool: str, text: str = "", data: Any = None, input_folder: str = ""
    ) -> io.NodeOutput:
        if not NAME.fullmatch(tool or ""):
            raise ValueError("Set Tool to the name of an approved tool.")
        workflow = (getattr(getattr(cls, "hidden", None), "extra_pnginfo", None) or {}).get(
            "workflow"
        )
        if not isinstance(workflow, dict) or not isinstance(workflow.get("id"), str):
            raise ValueError("Agent tools run only inside a saved workflow with a project history.")
        repo = ProjectRepo(projects_root(), workflow["id"])
        code, manifest = repo.load_tool(tool)
        if is_ref(data):
            # A dataset arrives whole, as the reference, under the tool's input of its class (or
            # "data"); its run folders stay readable for the sandbox.
            port = next(
                (
                    p["name"]
                    for p in manifest.get("inputs") or []
                    if port_socket(p["kind"]) == port_socket(data["class"])
                ),
                "data",
            )
            inputs = {port: data, "_run_folders": list(data.get("_run_folders") or [])}
        else:
            inputs = {**(data if isinstance(data, dict) else {})}
        inputs |= {"text": text} if text else {}
        # The user's decisions from the Review tab, so tools can learn from and measure against them.
        if labels := repo.load_labels():
            inputs["review_labels"] = labels
        import folder_paths

        read_dir = None
        if input_folder.strip():
            base = Path(folder_paths.get_input_directory()).resolve()
            read_dir = (base / input_folder.strip()).resolve()
            if base not in (read_dir, *read_dir.parents) or not read_dir.is_dir():
                raise ValueError("Input folder must be a folder inside ComfyUI's input directory.")
        # Earlier tools in the chain hand over files through their run folders (read-only here).
        tools_root = (Path(folder_paths.get_output_directory()) / "agent_tools" / repo.id).resolve()
        upstream = [Path(str(p)).resolve() for p in inputs.get("_run_folders") or []]
        if any(tools_root not in p.parents or not p.is_dir() for p in upstream):
            raise ValueError("Upstream run folders must be this workflow's own tool runs.")
        node_id = node_id_for(cls)
        report_node_progress(node_id, f"Agent tool {tool}: running. {protections()}")
        folder = run_folder(Path(folder_paths.get_output_directory()), repo.id, tool)
        # A typed tool (manifest version 2) keeps to its declared ports, like a node's sockets.
        typed = manifest.get("version") == 2
        if typed and (missing := value_problems("input", inputs, manifest["inputs"])):
            raise ValueError(
                f"Tool {tool}: " + "; ".join(missing) + ". Connect it through `data` or `text`."
            )
        started = time.time()
        status, outputs, error = "succeeded", None, None
        try:
            outputs = run_tool(code, inputs, folder, read_dir=read_dir, upstream=upstream)
            if typed and (wrong := value_problems("output", outputs, manifest["outputs"])):
                raise RuntimeError(f"Tool {tool} broke its declared ports: " + "; ".join(wrong))
            outputs["_run_folders"] = [*map(str, upstream), str(folder.resolve())]
        except RuntimeError as exc:
            status, error = "failed", str(exc)[:500]
        files = (
            sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
            if folder.exists()
            else []
        )
        record = {
            "version": 1,
            "kind": "agent_tool",
            "status": status,
            "tool": tool,
            "sha256": manifest["sha256"],
            "node": {"id": node_id, "class_type": "AgentWorkflowAgentTool"},
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "duration_seconds": round(time.time() - started, 3),
            "input": inputs,
            "input_folder": input_folder.strip() or None,
            "output": outputs,
            "error": error,
            "files": files,
            "run_folder": str(folder),
        }
        try:
            sha = repo.add_record(
                record, f"Run · Agent tool {tool} #{node_id} · {status}", workflow
            )
            recorded = f"Run record: version {sha[:8]}."
        except (OSError, RuntimeError, ValueError) as exc:
            recorded = f"Run record: not saved ({type(exc).__name__})."
        if error:
            raise RuntimeError(error)
        summary = "\n".join(
            [
                f"AGENT TOOL · {tool} · {round(time.time() - started, 1)} s",
                f"Output keys: {', '.join(outputs) or 'none'}",
            ]
            + ([f"Files in {folder.name}: {', '.join(files[:8])}"] if files else [])
            + [recorded]
        )
        text_out = (
            outputs.get("text")
            if isinstance(outputs.get("text"), str)
            else json.dumps(outputs, ensure_ascii=False)[:20000]
        )
        try:  # what this step did, readable on the node: operation, data in → out, samples
            tables = flow_tables(
                {
                    "tool": tool,
                    "what it does": manifest.get("description", ""),
                    "approved code": manifest["sha256"][:12],
                    "seconds": round(time.time() - started, 1),
                },
                [
                    *(data_files(read_dir) if read_dir else []),
                    *(data_files(upstream[-1]) if upstream else []),
                ],
                data_files(folder),
                outputs.get("explain"),
            )
        except (OSError, ValueError) as exc:
            tables = [
                {"title": f"Data preview unavailable: {exc}"[:200], "columns": [], "rows": []}
            ]
        # The tool's own result text is on the node, so a gate can check this node directly.
        shown = f"{summary}\n{text_out[:2000]}" if text_out else summary
        return io.NodeOutput(outputs, text_out, ui={"text": (shown,), "aws_tables": tables})


__all__ = ["AgentToolNode"]
