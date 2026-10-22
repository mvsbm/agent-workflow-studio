"""Running a workflow outside ComfyUI (an exported repo): a context and run folder per step, the
stage Checks and promised outputs it collects, and your reviews as a CSV round trip (the next pairs
to decide come out as a CSV; your filled-in decisions go back in)."""

from __future__ import annotations

import csv
import json
import shutil
import time
from pathlib import Path

from .checks import file_rows, judge
from .context import Context

DECIDED = {
    "same": "same",
    "different": "different",
    "yes": "same",
    "no": "different",
    "s": "same",
    "d": "different",
}


class Runtime:
    def __init__(self, out: Path, input_dir: Path, labels: dict | None = None):
        self.out, self.input_dir, self.labels = Path(out), Path(input_dir), labels or {}
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        self.contexts: list[Context] = []

    def ctx(self, name: str) -> Context:
        """A step's context: its own run folder under out/runs (earlier runs stay beside it)."""
        run = self.out / "runs" / f"{name}-{self.stamp}{len(self.contexts):03d}"
        run.mkdir(parents=True, exist_ok=True)
        context = Context(self.input_dir, run, self.labels)
        self.contexts.append(context)
        return context

    @property
    def lines(self) -> list[str]:
        return [line for c in self.contexts for line in c.lines]

    @property
    def checks(self) -> list[dict]:
        return [c.extra["check"] for c in self.contexts if "check" in c.extra]

    @property
    def outputs(self) -> dict:
        return dict(c.extra["output"] for c in self.contexts if "output" in c.extra)


# --- the studio's own nodes that only connect: their export -----------------------------------------
def data_folder(ctx: Context, folder: str) -> tuple:
    return (folder,)


def check(ctx: Context, value, kind: str, expect: str, says: str) -> tuple:
    passed, evidence = judge(value, kind, expect)
    ctx.extra["check"] = {"passed": passed, "says": says or kind, "evidence": evidence}
    ctx.report(f"{'✓ passed' if passed else '✗ failed'} · {says or kind} · {evidence}")
    return ()


def api_output(ctx: Context, value, name: str) -> tuple:
    ctx.extra["output"] = (name, value)
    return ()


def plain(value):
    """A promised output as an answer: a data file as its rows, anything else as it is."""
    return file_rows(value) if isinstance(value, Path) and value.is_file() else value


def write_outputs(rt: Runtime, out: Path) -> dict[str, str]:
    """Each promised output in out/: a data file copied under the output's name, else JSON or text.
    Returns what to tell the user: the file, or the text itself (a folder it names, as a real path)."""
    written = {}
    for name, value in rt.outputs.items():
        if isinstance(value, Path) and value.is_file():
            path = out / f"{name}{value.suffix}"
            shutil.copyfile(value, path)
        elif isinstance(value, str):
            named = rt.input_dir / value if value and (rt.input_dir / value).exists() else None
            text = str(named or value)
            (out / f"{name}.txt").write_text(text, encoding="utf-8")
            written[name] = text
            continue
        else:
            path = out / f"{name}.json"
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
            )
        written[name] = str(path)
    return written


# --- your reviews ---------------------------------------------------------------------------------
def read_reviews(path: Path) -> dict[str, str]:
    """pair id -> same | different, from a review CSV whose decision column you filled in."""
    if not path or not Path(path).is_file():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {
            r["id"]: DECIDED[(r.get("decision") or "").strip().lower()]
            for r in csv.DictReader(f)
            if r.get("id") and (r.get("decision") or "").strip().lower() in DECIDED
        }


def write_reviews(path: Path, labels: dict[str, str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "decision"])
        writer.writerows(sorted(labels.items()))


def review_batch(rt: Runtime, path: Path) -> int:
    """The pairs the run asks you about (each step's review.json), as one CSV with an empty
    decision column and both records side by side."""
    pairs = [
        p
        for c in rt.contexts
        if (c.run_dir / "review.json").is_file()
        for p in json.loads((c.run_dir / "review.json").read_text(encoding="utf-8"))["pairs"]
    ]
    if not pairs:
        path.unlink(missing_ok=True)
        return 0
    fields = list(
        dict.fromkeys(k for p in pairs for side in ("a", "b") for k in (p.get(side) or {}))
    )
    with open(
        path, "w", encoding="utf-8-sig", newline=""
    ) as f:  # with a BOM: Excel reads it as UTF-8
        writer = csv.writer(f)
        writer.writerow(
            [
                "id",
                "decision",
                "likely",
                "why",
                *(f"a_{k}" for k in fields),
                *(f"b_{k}" for k in fields),
            ]
        )
        for p in pairs:
            writer.writerow(
                [p["id"], "", p.get("score", ""), f"{p.get('branch', '')}: {p.get('reason', '')}"]
                + [(p.get("a") or {}).get(k, "") for k in fields]
                + [(p.get("b") or {}).get(k, "") for k in fields]
            )
    return len(pairs)
