"""Synthetic test data as visible stages: generate records from a readable recipe, damage them with
a readable recipe (keeping the clean truth and a log of every change), and compare a result to the
truth. Seeded, so the same recipe gives the same data. Known answers turn "is it better?" into a
number a Check — and the improve loop — can use.
"""

from __future__ import annotations

import datetime as dt
import random
import unicodedata
from typing import Any

from comfy_api.latest import io

from .data_preview import table as preview_table
from .nayose_bench import generate as bench
from .table_nodes import (
    CATEGORY,
    Table,
    check_table,
    columns_of,
    config,
    make,
    need_columns,
    report,
)

FIELD_KINDS = (
    "id",
    "person_ja",
    "date",
    "choice",
    "number",
    "phone",
    "email",
    "address_ja",
    "text",
)
DAMAGE_KINDS = ("missing", "typo", "width", "ocr", "case", "spaces", "format", "outlier")


def generate_rows(recipe: dict, count: int, seed: int) -> list[dict]:
    """Rows from a field recipe: {field: {kind: …, …}}; see FIELD_KINDS."""
    rng = random.Random(seed)
    fields = recipe.get("fields") or recipe
    if not isinstance(fields, dict) or not fields:
        raise ValueError("The recipe lists fields: e.g. id: {kind: id, prefix: C}.")
    rows = []
    for n in range(1, count + 1):
        row: dict[str, Any] = {}
        for name, spec in fields.items():
            spec = spec if isinstance(spec, dict) else {"kind": spec}
            kind = spec.get("kind")
            if kind == "id":
                row[name] = f"{spec.get('prefix', '')}{n:0{int(spec.get('digits', 5))}d}"
            elif kind == "person_ja":
                surname = rng.choice(bench.SURNAMES)
                given = rng.choice(bench.GIVEN)
                row[name] = f"{surname[0]} {rng.choice(given[3])}"
                row[f"{name}_kana"] = f"{surname[1]} {given[0]}"
            elif kind == "date":
                start = dt.date.fromisoformat(str(spec.get("from", "1950-01-01")))
                end = dt.date.fromisoformat(str(spec.get("to", "2005-12-31")))
                row[name] = (
                    start + dt.timedelta(days=rng.randint(0, (end - start).days))
                ).isoformat()
            elif kind == "choice":
                values = list(spec.get("values") or [])
                if not values:
                    raise ValueError(f"{name}: a choice needs values.")
                row[name] = rng.choices(values, weights=spec.get("weights"))[0]
            elif kind == "number":
                low, high = spec.get("min", 0), spec.get("max", 100)
                decimals = int(spec.get("decimals", 0))
                row[name] = (
                    round(rng.uniform(low, high), decimals)
                    if decimals
                    else rng.randint(int(low), int(high))
                )
            elif kind == "phone":
                row[name] = (
                    f"0{rng.randint(3, 9)}-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"
                )
            elif kind == "email":
                base = str(row.get(spec.get("from", ""), f"user{n}")).lower().replace(" ", ".")
                row[name] = f"{base}@{spec.get('domain', 'example.com')}"
            elif kind == "address_ja":
                pref, _, areas, _ = rng.choice(bench.PLACES)
                row[name] = (
                    f"{pref}{rng.choice(areas)[1]}{rng.randint(1, 9)}丁目{rng.randint(1, 30)}-{rng.randint(1, 20)}"
                )
            elif kind == "text":
                row[name] = str(spec.get("template", "{n}")).format(n=n)
            else:
                raise ValueError(f"{name}: kind must be one of {', '.join(FIELD_KINDS)}.")
        rows.append(row)
    return rows


def damage_value(kind: str, value: Any, rng: random.Random) -> Any:
    text = "" if value is None else str(value)
    if kind == "missing":
        return ""
    if not text:
        return text
    if kind == "typo" and len(text) > 1:
        i = rng.randrange(len(text) - 1)
        return (
            text[:i] + text[i + 1] + text[i] + text[i + 2 :]
            if rng.random() < 0.5
            else text[:i] + text[i + 1 :]
        )
    if kind == "width":
        wide = unicodedata.normalize("NFKC", text) != text
        return (
            unicodedata.normalize("NFKC", text)
            if wide
            else bench.half(text)
            if any("ァ" <= c <= "ヺ" for c in text)
            else text.translate(bench.WIDE)
        )
    if kind == "ocr":
        spots = [i for i, c in enumerate(text) if c in bench.OCR]
        if spots:
            i = rng.choice(spots)
            return text[:i] + bench.OCR[text[i]] + text[i + 1 :]
        return text
    if kind == "case":
        return text.lower() if text != text.lower() else text.upper()
    if kind == "spaces":
        return f" {text.replace(' ', '  ')} "
    if kind == "format":
        try:
            d = dt.date.fromisoformat(text)
            return rng.choice(
                [
                    f"{d.year}/{d.month}/{d.day}",
                    f"{d.month:02d}/{d.day:02d}/{d.year}",
                    f"{d.year}年{d.month}月{d.day}日",
                ]
            )
        except ValueError:
            return text.replace("-", "")
    if kind == "outlier":
        try:
            return float(text) * 10 if "." in text else int(text) * 10
        except ValueError:
            return text
    raise ValueError(f"Damage kind must be one of {', '.join(DAMAGE_KINDS)}.")


class GenerateRecords(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowGenerateRecords",
            display_name="Generate records (synthetic)",
            category=CATEGORY,
            search_aliases=["synthetic", "fake data", "test data", "generate"],
            description="Synthetic records from a readable recipe (id, person_ja, date, choice, number, phone, email, address_ja, text), seeded.",
            inputs=[
                io.String.Input(
                    "recipe",
                    display_name="Recipe",
                    multiline=True,
                    default="id: {kind: id, prefix: C}\nname: {kind: person_ja}\nbirth: {kind: date, from: 1950-01-01, to: 2005-12-31}\nphone: {kind: phone}",
                    tooltip="One field per line: name: {kind: …}. person_ja also writes <name>_kana.",
                ),
                io.Int.Input(
                    "count",
                    display_name="Rows",
                    default=100,
                    min=1,
                    max=200_000,
                    tooltip="How many records.",
                ),
                io.Int.Input(
                    "seed",
                    display_name="Seed",
                    default=7,
                    min=0,
                    max=2**31 - 1,
                    tooltip="Same seed, same data.",
                    control_after_generate=False,  # a fixed seed: re-runs give the same data
                ),
            ],
            outputs=[Table.Output(display_name="records")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, recipe: str, count: int, seed: int) -> io.NodeOutput:
        out = make("records", generate_rows(config(recipe, "Recipe"), count, seed))
        return io.NodeOutput(out, ui=report("Generate records", None, out, {"seed": seed}))


class DamageValues(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowDamageValues",
            display_name="Damage values (synthetic)",
            category=CATEGORY,
            search_aliases=["noise", "dirty", "corrupt", "test data", "damage"],
            description=(
                "Makes clean records dirty the way real data is, from a readable recipe per column (rates): "
                + ", ".join(DAMAGE_KINDS)
                + "; and `rows: {duplicate: rate}`. Keeps the clean truth and logs every change."
            ),
            inputs=[
                Table.Input("table", display_name="Clean records", tooltip="The truth to damage."),
                io.String.Input(
                    "recipe",
                    display_name="Damage",
                    multiline=True,
                    default="name: {typo: 0.03, ocr: 0.02, missing: 0.01}\nbirth: {format: 0.2}\nrows: {duplicate: 0.03}",
                    tooltip="column: {kind: rate, …}; rows: {duplicate: rate}.",
                ),
                io.Int.Input(
                    "seed",
                    display_name="Seed",
                    default=7,
                    min=0,
                    max=2**31 - 1,
                    tooltip="Same seed, same damage.",
                    control_after_generate=False,  # a fixed seed: re-runs give the same data
                ),
            ],
            outputs=[
                Table.Output(display_name="damaged"),
                Table.Output(display_name="truth", tooltip="The clean records: the answers."),
                Table.Output(
                    display_name="log", tooltip="Every change: row, column, kind, before, after."
                ),
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, recipe: str, seed: int) -> io.NodeOutput:
        t, spec, rng = check_table(table), config(recipe, "Damage"), random.Random(seed)
        need_columns(t, [c for c in spec if c != "rows"])
        damaged, log = [], []
        for index, row in enumerate(t["rows"]):
            new = dict(row)
            for column, kinds in spec.items():
                if column == "rows":
                    continue
                for kind, rate in (kinds or {}).items():
                    if rng.random() < float(rate):
                        before, new[column] = (
                            new.get(column),
                            damage_value(kind, new.get(column), rng),
                        )
                        if str(before) != str(new[column]):
                            log.append(
                                {
                                    "row": index + 1,
                                    "column": column,
                                    "kind": kind,
                                    "before": before,
                                    "after": new[column],
                                }
                            )
            damaged.append(new)
            if rng.random() < float((spec.get("rows") or {}).get("duplicate", 0)):
                damaged.append(dict(new))
                log.append(
                    {
                        "row": index + 1,
                        "column": "(row)",
                        "kind": "duplicate",
                        "before": "",
                        "after": "copied",
                    }
                )
        out = make(t["name"], damaged, t["columns"])
        counts: dict[str, int] = {}
        for entry in log:
            counts[f"{entry['column']} {entry['kind']}"] = (
                counts.get(f"{entry['column']} {entry['kind']}", 0) + 1
            )
        ui = report(
            "Damage values",
            t,
            out,
            {"changes": f"{len(log):,}", **{k: f"{v:,}" for k, v in counts.items()}},
        )
        ui["aws_tables"].insert(1, preview_table("Changes", log, 20))
        ui["aws_wires"] = [
            f"{len(damaged):,} rows",
            f"{len(t['rows']):,} rows",
            f"{len(log):,} changes",
        ]
        return io.NodeOutput(
            out, t, make("damage log", log, ["row", "column", "kind", "before", "after"]), ui=ui
        )


class CompareToTruth(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowCompareToTruth",
            display_name="Compare to truth",
            category=CATEGORY,
            search_aliases=["accuracy", "score", "evaluate", "ground truth", "test"],
            description="How often a result matches the known answers, per column and overall, with examples of misses. Feed its accuracy to a Check (at_least).",
            inputs=[
                Table.Input("result", display_name="Result", tooltip="What the workflow produced."),
                Table.Input("truth", display_name="Truth", tooltip="The known answers."),
                io.String.Input(
                    "key",
                    display_name="Key",
                    default="id",
                    tooltip="Columns that identify a record in both.",
                ),
                io.String.Input(
                    "columns",
                    display_name="Compare",
                    default="",
                    tooltip="Columns to compare (empty: all shared).",
                ),
            ],
            outputs=[
                io.Float.Output(display_name="accuracy"),
                Table.Output(display_name="per column"),
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, result: Any, truth: Any, key: str, columns: str) -> io.NodeOutput:
        got, want = check_table(result, "Result"), check_table(truth, "Truth")
        keys = columns_of(key)
        need_columns(got, keys)
        need_columns(want, keys)
        cols = columns_of(columns) or [
            c for c in want["columns"] if c in got["columns"] and c not in keys
        ]
        by_key = {tuple(str(r.get(k)) for k in keys): r for r in got["rows"]}
        stats = {c: [0, 0] for c in cols}
        misses = []
        for row in want["rows"]:
            other = by_key.get(tuple(str(row.get(k)) for k in keys))
            for c in cols:
                stats[c][1] += 1
                ok = (
                    other is not None
                    and str(other.get(c) or "").strip() == str(row.get(c) or "").strip()
                )
                stats[c][0] += ok
                if not ok and len(misses) < 20:
                    misses.append(
                        {
                            **{k: row.get(k) for k in keys},
                            "column": c,
                            "expected": row.get(c),
                            "got": None if other is None else other.get(c),
                        }
                    )
        right, total = sum(s[0] for s in stats.values()), sum(s[1] for s in stats.values())
        accuracy = right / total if total else 0.0
        per = make(
            "accuracy",
            [
                {
                    "column": c,
                    "right": s[0],
                    "of": s[1],
                    "accuracy": round(s[0] / s[1], 4) if s[1] else 0,
                }
                for c, s in stats.items()
            ],
        )
        line = f"accuracy {accuracy:.1%} · " + " · ".join(
            f"{c} {s[0] / s[1]:.0%}" for c, s in stats.items() if s[1]
        )
        ui = {
            "aws_tables": [
                preview_table("Accuracy per column", per["rows"]),
                preview_table("Misses", misses, 20),
            ],
            "text": (line,),
            "aws_headline": (line[:200],),
            "aws_wires": [f"{accuracy:.1%}", ""],
        }
        return io.NodeOutput(round(accuracy, 6), per, ui=ui)


NODES = [GenerateRecords, DamageValues, CompareToTruth]
