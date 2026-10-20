"""Check: a step's gate as a node. It reads one value (a stage's output) and says whether it proves
the step worked; it reports, never raises, so a failing check shows instead of stopping the run.
Step status is derived from the Check nodes inside the step's stage on the latest run.
"""

from __future__ import annotations

import csv
import hashlib
import io as text_io
import json
import re
from pathlib import Path
from typing import Any

from comfy_api.latest import io

KINDS = [
    "non_empty",
    "rows_at_least",
    "at_least",
    "contains",
    "equals",
    "regex",
    "image_count_at_least",
]


def rows_of(value: Any) -> int | None:
    """How many rows / pairs / items a value holds, when it says."""
    if isinstance(value, dict):
        if isinstance(value.get("rows"), list):
            return len(value["rows"])
        if isinstance(value.get("count"), int):
            return value["count"]
        if value.get("file") and value.get("_run_folders"):  # a run-folder record: count its file
            from .nayose_nodes import find_file

            try:
                path = find_file(value["file"], value)
                return sum(
                    1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
                ) - (path.suffix == ".csv")
            except (OSError, ValueError):
                return None
    if isinstance(value, list | tuple):
        return len(value)
    shape = getattr(value, "shape", None)
    return int(shape[0]) if shape else None


def text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:200_000]
    except (TypeError, ValueError):
        return str(value)[:200_000]


def judge(value: Any, kind: str, expect: str) -> tuple[bool, str]:
    """(passed, evidence) for one check."""
    if kind == "image_count_at_least":
        n = rows_of(value) if getattr(value, "shape", None) is not None else 0
        return n >= int(expect or 1), f"{n} image(s)"
    if (
        kind == "at_least"
    ):  # a number (e.g. an accuracy from Compare to truth) at least the expected
        number = value if isinstance(value, int | float) and not isinstance(value, bool) else None
        if number is None:
            return False, f"not a number: {text_of(value)[:60]}"
        return number >= float(expect or 0), f"{number:g} (need ≥ {expect})"
    if kind in ("non_empty", "rows_at_least"):
        n = rows_of(value)
        need = 1 if kind == "non_empty" else int(expect or 1)
        if n is None:
            empty = value is None or not text_of(value).strip()
            return (not empty) and need <= 1, "a value" if not empty else "nothing"
        return n >= need, f"{n:,} row(s)"
    text = text_of(value)
    passed = {
        "contains": lambda: str(expect) in text,
        "equals": lambda: text.strip() == str(expect).strip(),
        "regex": lambda: re.search(str(expect), text) is not None,
    }[kind]()
    return passed, text[:200]


class Check(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowCheck",
            display_name="Check",
            category="agent_workflow_studio",
            search_aliases=["gate", "assert", "verify", "test", "proof"],
            description=(
                "A step's gate: checks the data a stage gives (rows at least, non-empty, contains, ...) and "
                "shows pass or fail with the evidence. The step counts as verified when its checks pass."
            ),
            inputs=[
                io.AnyType.Input(
                    "value", display_name="Value", tooltip="The stage output to check."
                ),
                io.Combo.Input(
                    "kind",
                    options=KINDS,
                    default="non_empty",
                    display_name="Must hold",
                    tooltip="What must hold.",
                ),
                io.String.Input(
                    "expect",
                    display_name="Expect",
                    default="",
                    tooltip="The number or text the check needs (not for non_empty).",
                ),
                io.String.Input(
                    "says",
                    display_name="Says",
                    default="",
                    tooltip="How we know the step worked, in plain words (from the contract).",
                ),
            ],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, value: Any, kind: str, expect: str, says: str) -> io.NodeOutput:
        try:
            passed, evidence = judge(value, kind, expect)
        except (ValueError, re.error) as error:
            passed, evidence = False, f"check could not run: {error}"
        line = f"{'✓ passed' if passed else '✗ failed'} · {says or kind} · {evidence}"
        return io.NodeOutput(
            ui={
                "aws_check": [{"passed": passed, "kind": kind, "evidence": evidence}],
                "text": (line,),
                "aws_headline": (line,),
            }
        )


MAX_ROWS = 10_000  # an API answer stays a reasonable size; the full table is in the written file


def plain(value: Any) -> Any:
    """The value as JSON an API caller can use: text and numbers as they are, a table as columns and
    rows (capped), a run-folder record as its file and counts."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        return {
            "columns": value.get("columns"),
            "rows": value["rows"][:MAX_ROWS],
            "total_rows": len(value["rows"]),
        }
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value[:MAX_ROWS]]
    shape = getattr(value, "shape", None)
    return {"shape": list(shape)} if shape is not None else str(value)[:2000]


# The file types ComfyUI's App mode previews as text (its getMediaTypeFromFilename); images etc. need no help.
PREVIEWABLE = {"txt", "md", "markdown", "json", "csv", "yaml", "yml", "xml", "log"}


def app_file(name: str, value: Any, output_dir: Path) -> dict:
    """The promised output as a file App mode can show: a file the workflow already wrote under the
    output folder as it is, else the value written as CSV (a table), JSON (data) or text."""
    root = output_dir.resolve()
    if isinstance(value, str) and value.strip():
        found = (root / value.strip()).resolve()
        if found.is_file() and root in found.parents and found.suffix[1:].lower() in PREVIEWABLE:
            return {
                "filename": found.name,
                "subfolder": str(found.parent.relative_to(root)),
                "type": "output",
            }
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        buffer = text_io.StringIO()
        columns = value.get("columns") or sorted({k for r in value["rows"] for k in r})
        writer = csv.DictWriter(buffer, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(value["rows"])
        text, ext = buffer.getvalue(), "csv"
    elif isinstance(value, dict | list):
        text, ext = json.dumps(plain(value), ensure_ascii=False, indent=1, default=str), "json"
    else:
        text, ext = str(value), "txt"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "output"
    folder = root / "app_outputs"
    folder.mkdir(parents=True, exist_ok=True)
    # Named by content: each distinct result is its own file, so runs never overwrite each other.
    filename = f"{stem}-{hashlib.sha1(text.encode()).hexdigest()[:10]}.{ext}"
    (folder / filename).write_text(text, encoding="utf-8")
    return {"filename": filename, "subfolder": "app_outputs", "type": "output"}


class ApiOutput(io.ComfyNode):
    """A promised output's value, returned by the workflow's API (placed by the skeleton)."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowApiOutput",
            display_name="API output",
            category="agent_workflow_studio",
            search_aliases=["output", "return", "result", "api"],
            description=(
                "Returns the value at a stage's output to callers of the workflow's API, under the promised "
                "output's name, and shows it in App mode as a file (CSV for a table, JSON for data, text)."
            ),
            inputs=[
                io.AnyType.Input(
                    "value", display_name="Value", tooltip="The stage output the API returns."
                ),
                io.String.Input(
                    "name", display_name="Name", default="", tooltip="The promised output's name."
                ),
            ],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, value: Any, name: str) -> io.NodeOutput:
        import folder_paths

        data = plain(value)
        shown = app_file(name, value, Path(folder_paths.get_output_directory()))
        size = (
            f"{data['total_rows']:,} rows"
            if isinstance(data, dict) and "total_rows" in data
            else str(data)[:80]
        )
        line = f"API output {name}: {size}"
        return io.NodeOutput(
            ui={
                "aws_output": [{"name": name, "value": data}],
                "files": [shown],  # what App mode shows as this output's result
                "text": (line,),
                "aws_headline": (line,),
            }
        )


def input_folders() -> list[str]:
    """The folders in ComfyUI's input folder (where callers and the upload button put data)."""
    import folder_paths

    root = Path(folder_paths.get_input_directory())
    found = (
        sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        if root.is_dir()
        else []
    )
    return found or [""]


class DataFolder(io.ComfyNode):
    """A promised data input: a folder of files in ComfyUI's input folder (claim check)."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowDataFolder",
            display_name="Data folder",
            category="agent_workflow_studio",
            description="A folder of data files in ComfyUI's input folder, for the stages that read it. "
            "Choose one, or upload files into a new one with the button (in App mode too); an API "
            "call names the folder it uploaded with POST /upload/image.",
            inputs=[
                # Re-listed every time the schema is read (as ComfyUI's LoadImage lists images).
                io.Combo.Input(
                    "folder",
                    options=input_folders(),
                    display_name="Folder",
                    tooltip="A folder inside ComfyUI's input folder.",
                ),
            ],
            outputs=[io.String.Output(display_name="folder", tooltip="The folder's name.")],
        )

    @classmethod
    def execute(cls, folder: str) -> io.NodeOutput:
        import folder_paths

        root = Path(folder_paths.get_input_directory()).resolve()
        path = (root / folder).resolve()
        if not folder or root not in path.parents or not path.is_dir():
            raise ValueError(
                "Choose a folder inside ComfyUI's input folder, or upload files first."
            )
        return io.NodeOutput(folder)


NODES = [Check, ApiOutput, DataFolder]
