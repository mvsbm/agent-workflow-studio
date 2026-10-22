"""What a step needs from where it runs: the studio (ComfyUI folders, the Review tab) or a CLI."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path


def read_csv_any(path: Path) -> tuple[list[str], list[dict]]:
    """A CSV file's header and rows, in UTF-8 (with or without a BOM) or else Shift_JIS as Windows
    writes it in Japan (cp932): what CRM exports and Excel's plain "CSV" save are."""
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp932")
        except UnicodeDecodeError:
            raise ValueError(
                f"{Path(path).name} is neither UTF-8 nor Shift_JIS: save it as CSV UTF-8."
            ) from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    return list(reader.fieldnames or []), list(reader)


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
