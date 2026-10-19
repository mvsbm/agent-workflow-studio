"""Check: a step's gate as a node. It reads one value (a stage's output) and says whether it proves
the step worked; it reports, never raises, so a failing check shows instead of stopping the run.
Step status is derived from the Check nodes inside the step's stage on the latest run.
"""

from __future__ import annotations

import json
import re
from typing import Any

from comfy_api.latest import io

KINDS = ["non_empty", "rows_at_least", "contains", "equals", "regex", "image_count_at_least"]


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
    if isinstance(value, list | tuple | str):
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


NODES = [Check]
