"""Agent Tool node: runs a user-approved agent tool from this workflow's history, confined."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from comfy_api.latest import io

from .agent_tools import NAME, protections, run_folder, run_tool
from .node_progress import node_id_for, report_node_progress
from .project_repo import ProjectRepo, projects_root


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
                    multiline=True,
                    optional=True,
                    default="",
                    tooltip="Passed to the tool as inputs['text'].",
                ),
                io.Dict.Input(
                    "data",
                    display_name="Data input",
                    optional=True,
                    tooltip="Connect a DICT; its keys are passed to the tool as inputs.",
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
    def execute(cls, tool: str, text: str = "", data: Any = None) -> io.NodeOutput:
        if not NAME.fullmatch(tool or ""):
            raise ValueError("Set Tool to the name of an approved tool.")
        workflow = (getattr(getattr(cls, "hidden", None), "extra_pnginfo", None) or {}).get(
            "workflow"
        )
        if not isinstance(workflow, dict) or not isinstance(workflow.get("id"), str):
            raise ValueError("Agent tools run only inside a saved workflow with a project history.")
        repo = ProjectRepo(projects_root(), workflow["id"])
        code, manifest = repo.load_tool(tool)
        inputs = {**(data if isinstance(data, dict) else {}), **({"text": text} if text else {})}
        node_id = node_id_for(cls)
        report_node_progress(node_id, f"Agent tool {tool}: running. {protections()}")
        import folder_paths

        folder = run_folder(Path(folder_paths.get_output_directory()), repo.id, tool)
        started = time.time()
        status, outputs, error = "succeeded", None, None
        try:
            outputs = run_tool(code, inputs, folder)
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
        return io.NodeOutput(outputs, text_out, ui={"text": (summary,)})


__all__ = ["AgentToolNode"]
