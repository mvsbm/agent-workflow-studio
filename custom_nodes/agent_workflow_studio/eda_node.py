"""Profile data: the first look at a data set (EDA), shown on the canvas.

For every file in a folder: rows and columns; for every column: how full it is, how many distinct
values, the value *patterns* (each character as its kind: 9 digit, A/a letter, カ katakana,
ｶ half-width kana, ひ hiragana, 漢 kanji), so mixed formats show at a glance — e.g. a birth date
column holding ``S99.99.99``, ``9999/99/99`` and ``9999999`` — and a few examples.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from comfy_api.latest import io

from .data_preview import data_files, read_rows, table

MAX_ROWS = 50_000


def char_class(c: str) -> str:
    if c.isdigit():
        return "9"
    if "a" <= c.lower() <= "z":
        return "A" if c.isupper() else "a"
    if "ぁ" <= c <= "ゖ":
        return "ひ"
    if "ァ" <= c <= "ヺ" or c == "ー":
        return "カ"
    if "ｦ" <= c <= "ﾟ":
        return "ｶ"
    if unicodedata.category(c) == "Lo":
        return "漢"
    if c.isspace():
        return "␣"
    return c


def pattern(value: str) -> str:
    """The shape of a value, runs of the same kind shortened: 'ﾔﾏﾀﾞ ﾀﾛｳ' -> 'ｶ+␣ｶ+'."""
    shape = "".join(char_class(c) for c in value.strip())
    return re.sub(r"(.)\1+", r"\1+", shape)[:24] or "(empty)"


def profile_rows(rows: list[dict]) -> list[dict]:
    columns = list(dict.fromkeys(k for row in rows[:200] for k in row))
    out = []
    for column in columns[:40]:
        values = [str(row.get(column) or "").strip() for row in rows]
        filled = [v for v in values if v]
        shapes = Counter(pattern(v) for v in filled)
        out.append(
            {
                "column": column,
                "filled": f"{len(filled) / max(len(values), 1):.0%}",
                "distinct": f"{len(set(filled)):,}",
                "patterns": ", ".join(
                    f"{p} ({n / max(len(filled), 1):.0%})" for p, n in shapes.most_common(3)
                ),
                "pattern count": len(shapes),
                "examples": " | ".join(list(dict.fromkeys(filled))[:3]),
            }
        )
    return out


class ProfileData(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowProfileData",
            display_name="Profile data (EDA)",
            category="agent_workflow_studio",
            search_aliases=["eda", "profile", "explore", "data quality", "patterns"],
            description=(
                "A first look at a data set: per file rows and columns; per column how full it is, distinct values, "
                "value patterns (9 digit, A/a letter, カ katakana, ｶ half-width kana, ひ hiragana, 漢 kanji) and examples. "
                "Mixed formats and dirty columns show at a glance. Passes the folder on."
            ),
            inputs=[
                io.String.Input(
                    "folder",
                    display_name="Folder",
                    default="",
                    tooltip="A folder inside ComfyUI's input directory (connect a data source's folder).",
                ),
            ],
            outputs=[
                io.String.Output(
                    display_name="folder", tooltip="The same folder, for the next stage."
                )
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, folder: str) -> io.NodeOutput:
        import folder_paths

        base = Path(folder_paths.get_input_directory()).resolve()
        target = (base / folder.strip()).resolve()
        if base not in (target, *target.parents) or not target.is_dir():
            raise ValueError("Folder must be a folder inside ComfyUI's input directory.")
        files = data_files(target)
        if not files:
            raise ValueError(f"No CSV, JSONL or JSON files in {folder!r}.")
        summary, tables = [], []
        for path in files[:8]:
            rows = read_rows(path)[:MAX_ROWS]
            columns = profile_rows(rows)
            summary.append(
                {
                    "file": path.name,
                    "rows": f"{len(rows):,}",
                    "columns": len(columns),
                    "mixed-format columns": sum(1 for c in columns if c["pattern count"] > 3),
                }
            )
            tables.append(table(f"{path.name} · columns", columns, 40, 90))
        text = "PROFILE · " + " · ".join(
            f"{s['file']} {s['rows']} rows, {s['columns']} columns, {s['mixed-format columns']} mixed-format"
            for s in summary
        )
        # The text lets a contract gate check this node directly.
        return io.NodeOutput(
            target.name,
            ui={
                "text": (text,),
                "aws_tables": [table("Files", summary), *tables],
                "aws_wires": [f"{len(summary)} files"],
                "aws_headline": (
                    " · ".join(
                        f"{s['file']} {s['rows']} rows, {s['mixed-format columns']} mixed"
                        for s in summary
                    ),
                ),
            },
        )


NODES: list[Any] = [ProfileData]
