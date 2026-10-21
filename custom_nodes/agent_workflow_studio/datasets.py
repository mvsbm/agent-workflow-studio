"""Datasets passed by reference (the type standard's DATASET, STANDARDS.md): a step writes its data as
a file in its run folder and hands on a reference, never the rows; the next step reads exactly the
file it was given. Replaces looking a file up by name in the run folders (the newest one won).

    {"kind": "studio#dataset", "class": "record_pairs", "name": "pairs",
     "uri": "/…/output/agent_tools/<workflow>/compare_fields-…/pairs.jsonl", "format": "jsonl",
     "row_count": 57624, "subset": "/…/unsettled.csv", "inputs": ["/…/pairs.jsonl"],
     "_run_folders": [...]}

`subset` (optional): only the rows of `uri` whose id is in that file (a branch of pairs left open).
`inputs`: the datasets this one was made from (lineage). `_run_folders`: the run folders upstream,
where a step finds its lineage's other files (an earlier stage's decisions, the review) and which a
tool's sandbox may read.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .standards import resolve

KIND = "studio#dataset"


def ref(
    cls: str,
    name: str,
    path: Path,
    row_count: int | None = None,
    *,
    subset: Path | None = None,
    upstream: Any = None,
    run_folders: list[str] | None = None,
    **extra: Any,
) -> dict:
    """A reference to a dataset of a registered class, written at `path`."""
    spec = resolve(cls)
    if spec is None or spec.scope != "class" or spec.storage != "file":
        raise ValueError(f"{cls} is not a registered file dataset class")
    made_from = [upstream["uri"]] if is_ref(upstream) else []
    if run_folders is None:  # the lineage: the upstream run folders, as handed on
        run_folders = list(upstream.get("_run_folders") or []) if isinstance(upstream, dict) else []
    return {
        "kind": KIND,
        "class": spec.name,
        "name": name,
        "uri": str(Path(path).resolve()),
        "format": spec.format,
        **({"row_count": row_count} if row_count is not None else {}),
        **({"subset": str(Path(subset).resolve())} if subset else {}),
        **({"inputs": made_from} if made_from else {}),
        "_run_folders": run_folders,
        **extra,
    }


def is_ref(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") == KIND and bool(value.get("uri"))


def dataset_file(value: Any, default: str = "") -> Path:
    """The file a dataset value names: its reference's uri; for a value from before references, the
    file it names (or `default`) in its run folders."""
    if is_ref(value):
        path = Path(value["uri"])
        if not path.is_file():
            raise ValueError(f"The {value['class']} dataset {value.get('name')!r} is gone: {path}")
        return path
    from .nayose_nodes import find_file

    name = (value.get("file") if isinstance(value, dict) else None) or default
    return find_file(name, value)


def subset_file(value: Any) -> Path | None:
    """The id list a value is limited to (a branch left open), or None for the whole dataset."""
    if is_ref(value):
        return Path(value["subset"]) if value.get("subset") else None
    if isinstance(value, dict) and value.get("pairs_subset"):
        from .nayose_nodes import find_file

        return find_file(value["pairs_subset"], value)
    return None


def subset_ids(value: Any) -> set[str] | None:
    path = subset_file(value)
    if path is None:
        return None
    with open(path, encoding="utf-8-sig", newline="") as file:
        return {row["id"] for row in csv.DictReader(file)}


def lineage_file(value: Any, cls: str, default: str) -> Path:
    """A step's input of class `cls`: the value itself when it is that dataset, else the file of
    that name in its lineage (e.g. the pairs behind a pair_decisions value)."""
    if is_ref(value) and value["class"] == cls:
        return dataset_file(value)
    from .nayose_nodes import find_file

    return find_file(default, value)


def ui_datasets(*given: tuple[int, dict]) -> list:
    """The datasets a node gave, by output slot, for its run report (ui.aws_datasets): what the Data
    tab lists for each port (class, name, where, how many rows)."""
    import folder_paths

    root = Path(folder_paths.get_output_directory()).resolve()

    def where(uri: str) -> str:
        path = Path(uri)
        return str(path.relative_to(root)) if root in path.parents else path.name

    return [
        [
            slot,
            {
                "class": value["class"],
                "name": value.get("name", ""),
                "file": where(value["uri"]),
                "format": value.get("format"),
                "row_count": value.get("row_count"),
                **({"subset": where(value["subset"])} if value.get("subset") else {}),
            },
        ]
        for slot, value in given
        if is_ref(value)
    ]
