"""Old run outputs: what a clean-up may remove. Runs never overwrite (numbered result files, one
folder per tool run, content-named App files), so space grows; this removes what is older than the
kept days, and always keeps the newest of each kind, so the latest result of every workflow, tool
and file name stays. Answer keys (nayose_benchmark/) and anything not made by a run are never touched.
"""

from __future__ import annotations

import re
import shutil
from collections import defaultdict
from pathlib import Path

NUMBERED = re.compile(
    r"^(?P<base>.+)_(?P<n>\d{5})_\.(?P<ext>[a-z0-9]+)$"
)  # Write table, ComfyUI saves
APP_FILE = re.compile(r"^(?P<base>.+)-[0-9a-f]{10}\.(?P<ext>[a-z0-9]+)$")  # check_node.app_file
RUN_FOLDER = re.compile(r"^(?P<base>.+)-\d{8}-\d{6}\d{0,3}(?:-\d+)?$")  # agent_tools.run_folder


def _size(path: Path) -> int:
    return (
        sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        if path.is_dir()
        else path.stat().st_size
    )


def candidates(root: Path) -> dict[tuple, list[Path]]:
    """Run outputs grouped so that each group's newest one is kept: (kind, folder, base name)."""
    groups: dict[tuple, list[Path]] = defaultdict(list)
    tools = root / "agent_tools"
    for workflow in tools.iterdir() if tools.is_dir() else []:
        for run in workflow.iterdir() if workflow.is_dir() else []:
            if run.is_dir() and (m := RUN_FOLDER.match(run.name)):
                groups[("run", workflow.name, m["base"])].append(run)
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or "nayose_benchmark" in path.relative_to(root).parts
            or tools in path.parents
        ):
            continue
        if m := NUMBERED.match(path.name):
            groups[("numbered", str(path.parent), m["base"], m["ext"])].append(path)
        elif path.parent.name == "app_outputs" and (m := APP_FILE.match(path.name)):
            groups[("app", str(path.parent), m["base"], m["ext"])].append(path)
    return groups


def cleanup(root: Path, days: float, now: float, dry_run: bool = True) -> dict:
    """Remove run outputs older than `days` (0 or less: keep everything), the newest of each group
    always kept. Returns what was (or, in a dry run, would be) removed and the space it frees."""
    removed, freed = [], 0
    if days > 0 and root.is_dir():
        cutoff = now - days * 86400
        for paths in candidates(root).values():
            paths.sort(key=lambda p: p.stat().st_mtime)
            for path in paths[:-1]:  # the newest stays
                if path.stat().st_mtime >= cutoff:
                    continue
                freed += _size(path)
                removed.append(str(path.relative_to(root)))
                if not dry_run:
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
    return {
        "removed": sorted(removed),
        "count": len(removed),
        "freed_bytes": freed,
        "dry_run": dry_run,
    }
