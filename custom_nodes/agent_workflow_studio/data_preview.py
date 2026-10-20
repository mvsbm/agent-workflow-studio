"""Small display-only views of data files, shown inside nodes as tables (``ui.aws_tables``)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

MAX_ROWS, MAX_CELL = 20, 80
DATA_SUFFIXES = {".csv", ".jsonl", ".json", ".tsv"}


def table(title: str, rows: list[dict], limit: int = MAX_ROWS, cell: int = MAX_CELL) -> dict:
    """The first ``limit`` rows, long cells shortened."""
    columns = list(dict.fromkeys(k for row in rows[:limit] for k in row))

    def cut(value: Any) -> str:
        text = (
            ""
            if value is None
            else value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False)
        )
        return text if len(text) <= cell else text[: cell - 1] + "…"

    return {
        "title": title if len(rows) <= limit else f"{title} (first {limit} of {len(rows):,})",
        "columns": columns,
        "rows": [[cut(row.get(c)) for c in columns] for row in rows[:limit]],
    }


def counts_table(title: str, counts: dict) -> dict:
    return table(title, [{"what": k, "count": v} for k, v in counts.items()], 40)


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Not UTF-8 or Shift_JIS text.")


def read_rows(path: Path, limit: int | None = None) -> list[dict]:
    """Rows of a CSV/TSV, JSONL or JSON (list, or an object holding one) file."""
    raw = path.read_bytes() if limit is None else path.read_bytes()[: 4 * 1024 * 1024]
    text = decode(raw if limit is None else raw[: raw.rfind(b"\n") + 1] or raw)
    if path.suffix == ".jsonl":
        lines = [line for line in text.splitlines() if line.strip()]
        return [json.loads(line) for line in lines[:limit]]
    if path.suffix == ".json":
        data = json.loads(decode(path.read_bytes()))
        data = (
            data
            if isinstance(data, list)
            else next((v for v in data.values() if isinstance(v, list)), [data])
        )
        return [row if isinstance(row, dict) else {"value": row} for row in data[:limit]]
    reader = csv.DictReader(io.StringIO(text), delimiter="\t" if path.suffix == ".tsv" else ",")
    return [row for _, row in zip(range(limit), reader, strict=False)] if limit else list(reader)


def shape(path: Path) -> dict:
    """Rows and columns of a data file, counted cheaply (rows = lines, minus a CSV header)."""
    with open(path, "rb") as file:
        lines = sum(chunk.count(b"\n") for chunk in iter(lambda: file.read(1 << 20), b""))
    sample = (
        read_rows(path, 5)
        if path.suffix != ".json" or path.stat().st_size < 8 * 1024 * 1024
        else []
    )
    if path.suffix == ".json" and sample:
        rows = len(read_rows(path))
    else:
        rows = max(0, lines - (1 if path.suffix in (".csv", ".tsv") else 0))
    columns = list(dict.fromkeys(k for row in sample for k in row))
    return {"rows": rows, "columns": columns, "sample": sample}


def data_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix in DATA_SUFFIXES)


def compact(value: Any) -> str:
    """A record on one line: its values joined, keys dropped (the column headers say what they are)."""
    if isinstance(value, dict):
        return " · ".join(str(v) for v in value.values() if v not in (None, ""))
    return "" if value is None else str(value)


def explain_tables(explain: Any) -> list[dict]:
    """What the tool says it did, in plain words with counts, and before → after examples."""
    if not isinstance(explain, dict) or not explain.get("steps"):
        return [
            {
                "title": "⚠ This tool does not say what it did: return explain = {steps, examples}.",
                "columns": [],
                "rows": [],
            }
        ]
    steps = [
        {
            "#": i,
            "what it did": s.get("what", ""),
            "records": f"{s['count']:,}" if isinstance(s.get("count"), int) else s.get("count", ""),
        }
        for i, s in enumerate(explain.get("steps") or [], 1)
        if isinstance(s, dict)
    ]
    examples = [
        {
            "before": compact(e.get("before")),
            "after": compact(e.get("after")),
            "why": e.get("why", ""),
        }
        for e in (explain.get("examples") or [])
        if isinstance(e, dict)
    ]
    tables = [table("What this step did", steps, 20, 140)]
    if examples:
        tables.append(table("Before → after", examples, 8, 120))
    return tables


def flow_tables(
    operation: dict, inputs: list[Path], outputs: list[Path], explain: Any = None
) -> list[dict]:
    """What a step did: its own explanation first, then the files in and out, one sample, run details."""
    rows, samples = [], []
    for direction, files in (("in", inputs), ("out", outputs)):
        for path in files[:12]:
            try:
                info = shape(path)
            except (OSError, ValueError, json.JSONDecodeError):
                rows.append(
                    {"": direction, "file": path.name, "rows": "?", "columns": "unreadable"}
                )
                continue
            rows.append(
                {
                    "": direction,
                    "file": path.name,
                    "rows": f"{info['rows']:,}",
                    "columns": len(info["columns"]),
                }
            )
            if direction == "out" and not samples:  # one sample: the main output
                samples.append(table(f"{path.name} · sample", info["sample"], 5))
    facts = [{"": key, "value": value} for key, value in operation.items()]
    return [
        *explain_tables(explain),
        table("Data in → out", rows, 30),
        *samples,
        table("Run details", facts),
    ]
