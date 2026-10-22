"""What a step needs from where it runs: the studio (ComfyUI folders, the Review tab) or a CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Context:
    input_dir: Path  # where the data folders are
    run_dir: Path  # this step's own folder for the files it writes
    labels: dict = field(default_factory=dict)  # pair id -> "same" | "different" (your reviews)
    lines: list = field(default_factory=list)  # what the step reports, one line each
    facts: dict = field(default_factory=dict)  # what it found, for a table
    extra: dict = field(default_factory=dict)  # details a node shows (e.g. the cluster tree)
    stage: dict | None = None  # the stage card it runs in (the Review tab groups by it)

    def report(self, line: str) -> None:
        self.lines.append(line)

    def data_path(self, folder: str, name: str) -> Path:
        """A file in a data folder; never outside the input folder."""
        root = Path(self.input_dir).resolve()
        path = (root / (folder or "") / name).resolve()
        if root not in path.parents:
            raise ValueError(f"{folder}/{name} is outside the input folder.")
        if not path.is_file():
            raise ValueError(f"No file {name!r} in the data folder {folder!r}.")
        return path
