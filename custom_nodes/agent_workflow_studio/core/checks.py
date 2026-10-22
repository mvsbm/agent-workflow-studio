"""How a step's gate and a contract example are judged, the same in the studio and in an exported
CLI: a Check's kinds (rows at least, contains, ...), a dataset answered as its rows, and an
example's expected values found in what the run delivered."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

MAX_ROWS = 10_000  # an answer stays a reasonable size; the full table is in the written file
KINDS = [
    "non_empty",
    "rows_at_least",
    "at_least",
    "contains",
    "equals",
    "regex",
    "image_count_at_least",
]


def file_rows(path: Path, fmt: str = "") -> dict:
    """A data file (CSV or JSON lines) as its columns and rows (capped) and how many there are."""
    path = Path(path)
    with open(path, encoding="utf-8-sig", newline="") as file:
        if (fmt or path.suffix[1:]) == "csv":
            reader = csv.DictReader(file)
            rows = [row for _, row in zip(range(MAX_ROWS), reader, strict=False)]
            total = len(rows) + sum(1 for _ in reader)
            columns = list(reader.fieldnames or [])
        else:
            lines = [line for line in file if line.strip()]
            rows, total = [json.loads(line) for line in lines[:MAX_ROWS]], len(lines)
            columns = list(rows[0]) if rows else []
    return {"columns": columns, "rows": rows, "total_rows": total}


def rows_of(value: Any) -> int | None:
    """How many rows / pairs / items a value holds, when it says (a data file is counted)."""
    if isinstance(value, Path) and value.is_file():
        return file_rows(value)["total_rows"]
    if isinstance(value, dict):
        if isinstance(value.get("rows"), list):
            return len(value["rows"])
        if isinstance(value.get("row_count"), int):  # a dataset reference says its size
            return value["row_count"]
        if isinstance(value.get("count"), int):
            return value["count"]
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


def judge(value: Any, kind: str, expect: str, rows=rows_of) -> tuple[bool, str]:
    """(passed, evidence) for one check; `rows` counts a value's rows."""
    if kind == "image_count_at_least":
        n = rows(value) if getattr(value, "shape", None) is not None else 0
        return n >= int(expect or 1), f"{n} image(s)"
    if kind == "at_least":  # a number (e.g. an accuracy) at least the expected
        number = value if isinstance(value, int | float) and not isinstance(value, bool) else None
        if number is None:
            return False, f"not a number: {text_of(value)[:60]}"
        return number >= float(expect or 0), f"{number:g} (need ≥ {expect})"
    if kind in ("non_empty", "rows_at_least"):
        n = rows(value)
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


def _loose(value: object) -> str:
    return " ".join(str(value).split()).lower()


def _cells(value: object) -> str:
    """Cell values in order, whatever separates them: "North,1500; South,800" = "North 1500, South 800"."""
    return " " + re.sub(r"[\s,;|]+", " ", str(value)).strip().lower() + " "


def rows_text(value: object) -> str:
    """A table as a person writes it in an example: "North 1500, South 800" (numbers plain)."""
    rows = value.get("rows") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        return ""

    def plain(v: object) -> str:
        return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)

    return ", ".join(
        " ".join(plain(v) for v in row.values()) for row in rows if isinstance(row, dict)
    )


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
            ok = _loose(want) in found or _cells(want) in _cells(rows_text(got))
        if not ok:
            misses.append(
                f"{key}: expected {want!r}, got {str(got)[:120] if got is not None else 'nothing'}"
            )
    return misses
