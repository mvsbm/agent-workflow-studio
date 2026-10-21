"""Table nodes: small, visible data operations, so a data job is a real graph of steps instead of a
script inside one node.

A TABLE flows between nodes ({"name", "columns", "rows"}). Every node shows what it did by itself:
rows in → out, the columns it added or changed, and before → after examples of the values it
changed, so the logic of each step is on the canvas, not in code.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from comfy_api.latest import io

from .data_preview import decode, table
from .datasets import ref, ui_datasets
from .standards import custom, dataset_sockets

# The sockets come from the type standard (standards/): TABLE is a core type; the others are the
# record-matching domain's dataset classes, so ComfyUI refuses a wrong wire. Typed inputs also take
# DICT (an Agent Tool's result), the escape hatch for agent-written steps.
Table = custom("TABLE")
RecordPairs = custom("record_pairs")  # compared pairs, not decided yet
PairDecisions = custom("pair_decisions")  # decided pairs: match or different
RecordClusters = custom("record_clusters")  # records grouped into clusters (one entity each)
CATEGORY = "agent_workflow_studio/table"


# --- shared -----------------------------------------------------------------------------------
def make(name: str, rows: list[dict], columns: list[str] | None = None) -> dict:
    # Every row counts: concatenated tables have columns only some parts carry.
    columns = columns or list(dict.fromkeys(k for row in rows for k in row))
    return {"name": name, "columns": columns, "rows": rows}


def check_table(value: Any, label: str = "table") -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list):
        raise ValueError(f"Connect a table to {label}.")
    return value


def columns_of(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[,\n]", text or "") if c.strip()]


def need_columns(t: dict, columns: list[str]) -> None:
    missing = [c for c in columns if c not in t["columns"]]
    if missing:
        raise ValueError(
            f"{t['name']} has no column {', '.join(missing)}; it has {', '.join(t['columns'][:30])}."
        )


def config(text: str, what: str) -> Any:
    try:
        return yaml.safe_load(text or "") or {}
    except yaml.YAMLError as error:
        raise ValueError(f"{what} is not valid YAML: {error}") from None


# Follow a record: the last table each node gave, by executing node id ("stage:inner").
# shortcut: in memory for the server's life, keyed by node id only (ids repeat across workflows;
# the client keeps the ids of its own graph), upgrade to per-prompt storage if that confuses users.
LAST: dict[str, dict] = {}


def follow(query: str, limit: int = 3) -> list[dict]:
    """Where a record went: per node (in run order), the rows that contain the query."""
    q = query.strip().lower()
    found = []
    for node, t in LAST.items():
        rows = [r for r in t["rows"] if any(q in str(v).lower() for v in r.values())]
        found.append(
            {
                "node": node,
                "table": t["name"],
                "columns": t["columns"],
                "count": len(rows),
                "rows": rows[:limit],
                # which columns held it, so a step whose rows lack them is not called a drop
                "matched": [k for k, v in rows[0].items() if q in str(v).lower()] if rows else [],
            }
        )
    return found


def report(
    title: str,
    before: dict | None,
    after: dict,
    notes: dict | None = None,
    changed: list[str] | None = None,
) -> dict:
    """What this node did: counts, before → after for changed values, and a sample of the result."""
    from comfy_execution.utils import get_executing_context

    if context := get_executing_context():
        LAST.pop(context.node_id, None)  # re-inserted, so the order stays the run order
        LAST[context.node_id] = after  # a reference: ComfyUI's cache holds the same table
    facts = {
        "rows in": f"{len(before['rows']):,}" if before else "—",
        "rows out": f"{len(after['rows']):,}",
        "columns out": len(after["columns"]),
    }
    facts.update(notes or {})
    tables = [table(title, [{"": k, "value": v} for k, v in facts.items()], 30, 140)]
    examples = []
    if before and changed:
        for old, new in zip(before["rows"], after["rows"], strict=False):
            diffs = {
                c: (old.get(c), new.get(c))
                for c in changed
                if str(old.get(c) or "") != str(new.get(c) or "")
            }
            if diffs:
                for column, (a, b) in diffs.items():
                    examples.append({"column": column, "before": a, "after": b})
                if len(examples) >= 8:
                    break
        tables.append(table("Before → after", examples, 8, 80))
    tables.append(table(f"{after['name']} · sample", after["rows"], 5))
    unit = "pairs" if "pair" in after["name"] else "rows"
    count = f"{len(after['rows']):,} {unit}"
    first = next(iter((notes or {}).items()), None)
    # What the node did, in one line: for a fix, how many values changed and one real example.
    changed_values = (
        sum(
            str(old.get(c) or "") != str(new.get(c) or "")
            for old, new in zip(before["rows"], after["rows"], strict=False)
            for c in changed
        )
        if before and changed
        else 0
    )
    if changed_values and examples:
        e = examples[0]
        noun = "value" if changed_values == 1 else "values"
        line = (
            f"{changed_values:,} {noun} changed, e.g. {e['column']}: {e['before']} → {e['after']}"
        )
    else:
        line = (f"{len(before['rows']):,} → {count}" if before else count) + (
            f" · {first[0]} {first[1]}" if first else ""
        )
    same = before is not None and len(before["rows"]) == len(after["rows"])
    return {
        "aws_tables": tables,
        "text": (f"{title.upper()} · " + " · ".join(f"{k} {v}" for k, v in facts.items()),),
        # Read at a glance: a wire is labelled where the amount changes, and the node's one line.
        "aws_wires": ["" if same else count],
        "aws_headline": (line[:160],),
    }


def output_root() -> Path:
    import folder_paths

    return Path(folder_paths.get_output_directory()).resolve()


# --- read and write ---------------------------------------------------------------------------
DATA_FILES = (".csv", ".tsv", ".jsonl", ".json")


def one_data_file(base: Path, folder: str) -> str:
    """The name of the one data file in an input folder (File left empty), or why there is none."""
    where = (base / folder).resolve()
    if base not in where.parents or not where.is_dir():
        raise ValueError(f"No folder {folder!r} in ComfyUI's input folder.")
    found = sorted(
        p.name for p in where.iterdir() if p.is_file() and p.suffix.lower() in DATA_FILES
    )
    if len(found) != 1:
        raise ValueError(
            f"Folder {folder!r} holds {len(found)} data files ({', '.join(found[:6]) or 'none'}); "
            "put one there, or name the File to read."
        )
    return found[0]


class ReadTable(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowReadTable",
            display_name="Read table",
            category=CATEGORY,
            search_aliases=["csv", "load", "import", "read file"],
            description="Reads a CSV/TSV/JSONL file from a folder in ComfyUI's input directory (UTF-8 or Shift_JIS, detected).",
            inputs=[
                io.String.Input(
                    "folder",
                    display_name="Folder",
                    default="",
                    tooltip="Folder inside ComfyUI's input directory (connect a data source's folder).",
                ),
                io.String.Input(
                    "file",
                    display_name="File",
                    default="",
                    tooltip="File name in that folder; empty: the folder's one data file, whatever its name (a new month's export).",
                ),
            ],
            outputs=[Table.Output(display_name="table", tooltip="The rows, for the next step.")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, folder: str, file: str) -> io.NodeOutput:
        import folder_paths

        from .data_preview import read_rows

        base = Path(folder_paths.get_input_directory()).resolve()
        if not file.strip():
            file = one_data_file(base, folder.strip())
        path = (base / folder.strip() / file.strip()).resolve()
        if base not in path.parents or not path.is_file():
            raise ValueError(f"No file {file!r} in input folder {folder!r}.")
        raw = path.read_bytes()
        encoding = "utf-8"
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            encoding = "Shift_JIS (cp932)"
        text = decode(raw)  # raises for anything else
        # A CSV's header names its columns, also when it has no rows (a quiet month's export).
        header = (
            next(
                csv.reader(text.splitlines()[:1], delimiter="\t" if path.suffix == ".tsv" else ","),
                [],
            )
            if path.suffix not in (".jsonl", ".json")
            else []
        )
        rows = read_rows(path)
        out = make(path.stem, rows, [c for c in header if c] or None)
        return io.NodeOutput(
            out, ui=report("Read table", None, out, {"file": path.name, "encoding": encoding})
        )


class WriteTable(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowWriteTable",
            display_name="Write table",
            category=CATEGORY,
            search_aliases=["save csv", "export", "write file", "result"],
            description="Writes a table as a UTF-8 CSV under ComfyUI's output directory, with the columns you list (in that order). Like ComfyUI's save nodes it never overwrites: each run writes its own numbered file (result_00001_.csv), so every API call's download stays its own.",
            inputs=[
                Table.Input("table", display_name="Table", tooltip="The table to write."),
                io.String.Input(
                    "file",
                    display_name="File",
                    default="result.csv",
                    tooltip="Name under the output directory, e.g. nayose/nayose_result.csv; each run adds a number (nayose_result_00001_.csv).",
                ),
                io.String.Input(
                    "columns",
                    display_name="Columns",
                    default="",
                    tooltip="Comma-separated; empty = all columns.",
                ),
            ],
            outputs=[
                io.String.Output(
                    display_name="path", tooltip="Where the file was written (under output/)."
                )
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, file: str, columns: str) -> io.NodeOutput:
        t = check_table(table)
        cols = columns_of(columns) or t["columns"]
        need_columns(t, cols)
        import folder_paths

        root = output_root()
        wanted = (root / file.strip()).resolve()
        if (
            root not in wanted.parents
            or wanted.suffix.lower() != ".csv"
            or "nayose_benchmark" in wanted.parts
        ):
            raise ValueError("Write a .csv file under ComfyUI's output directory.")
        # ComfyUI's own save convention: the next free number for this name, never an overwrite.
        folder, name, counter, _, _ = folder_paths.get_save_image_path(
            str(wanted.relative_to(root).with_suffix("")), str(root)
        )
        path = Path(folder) / f"{name}_{counter:05}_.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(t["rows"])
        out = make(path.stem, [{c: r.get(c) for c in cols} for r in t["rows"]], cols)
        rel = str(path.relative_to(root))
        ui = report("Write table", t, out, {"file": f"output/{rel}"})
        ui["aws_files"] = [rel]  # an API call returns it as a download
        parent = str(Path(rel).parent)
        # ComfyUI's own file record: the chat offers it as a download (/view), as for an API output.
        ui["files"] = [
            {"filename": path.name, "subfolder": "" if parent == "." else parent, "type": "output"}
        ]
        return io.NodeOutput(rel, ui=ui)


# --- shape ------------------------------------------------------------------------------------
class TableFromResult(io.ComfyNode):
    """A code tool's table, for the table nodes: its result holds every declared output by name."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowTableFromResult",
            display_name="Table from tool result",
            category=CATEGORY,
            search_aliases=["tool table", "result to table", "dict to table", "convert"],
            description="Takes one table out of an Agent Tool's result, so table nodes can use it.",
            inputs=[
                io.Dict.Input(
                    "result", display_name="Tool result", tooltip="An Agent Tool's result."
                ),
                io.String.Input(
                    "output",
                    display_name="Output",
                    default="",
                    tooltip="The tool's output name; empty: its only table.",
                ),
            ],
            outputs=[Table.Output(display_name="table")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, result: dict, output: str) -> io.NodeOutput:
        tables = {
            k: v
            for k, v in (result or {}).items()
            if isinstance(v, dict) and isinstance(v.get("rows"), list)
        }
        name = output.strip()
        if name:
            if name not in tables:
                raise ValueError(
                    f"The tool result has no table {name!r}; it has {', '.join(tables) or 'none'}."
                )
        elif len(tables) == 1:
            name = next(iter(tables))
        else:
            raise ValueError(
                f"Name the output to take; the result has tables {', '.join(tables) or 'none'}."
            )
        t = tables[name]
        out = make(t.get("name") or name, t["rows"], t.get("columns"))
        return io.NodeOutput(out, ui=report("Table from tool result", None, out, {"output": name}))


class StackRoles(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowStackRoles",
            display_name="Stack roles",
            category=CATEGORY,
            search_aliases=["unpivot", "melt", "roles", "mentions", "column groups"],
            description=(
                "Turns column groups into rows: a row with holder (KYK_*), insured (HHK_*) and beneficiary (UKT_*) "
                "columns becomes up to three rows with a `role` column and the same field names. Rows whose name "
                "fields are all empty (or a non-person value) are left out."
            ),
            inputs=[
                Table.Input(
                    "table", display_name="Table", tooltip="Rows with one column group per role."
                ),
                io.String.Input(
                    "plan",
                    display_name="Plan",
                    multiline=True,
                    default=(
                        "system: policies          # written into a `system` column\n"
                        "id: REC_ID                # the row's id, kept as record_id\n"
                        "keep: {SHOKEN_NO: policy_ref}   # other columns copied to every role row (renamed)\n"
                        "roles: {holder: KYK_, insured: HHK_, beneficiary: UKT_}\n"
                        "fields: {NM_KJ: name, NM_KN: kana, BIRTH: birth, SEX: sex, ADDR: address, TEL: phone, ZIP: zip}\n"
                        "require_any: [name, kana]\n"
                        "not_a_person: [法定相続人, 未指定]\n"
                    ),
                    tooltip="YAML: system, id, keep, roles (role: column prefix), fields (suffix: field), require_any, not_a_person.",
                ),
            ],
            outputs=[
                Table.Output(
                    display_name="mentions", tooltip="One row per role that names someone."
                )
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, plan: str) -> io.NodeOutput:
        t = check_table(table)
        p = config(plan, "Plan")
        roles, fields = p.get("roles") or {}, p.get("fields") or {}
        if not isinstance(roles, dict) or not roles or not isinstance(fields, dict) or not fields:
            raise ValueError(
                "Plan needs `roles` (role: column prefix) and `fields` (column suffix: field name)."
            )
        need_columns(t, [p["id"]] if p.get("id") else [])
        required, skip = (
            p.get("require_any") or list(fields.values())[:1],
            set(p.get("not_a_person") or []),
        )
        keep = (
            p.get("keep") or []
        )  # a list, or {column: new name} to line columns up across systems
        keep = dict(keep) if isinstance(keep, dict) else {k: k for k in keep}
        rows, per_role = [], Counter()
        for row in t["rows"]:
            for role, prefix in roles.items():
                mention = {
                    field: (row.get(f"{prefix}{suffix}") or "").strip()
                    for suffix, field in fields.items()
                }
                if not any(mention.get(f) and mention.get(f) not in skip for f in required):
                    continue
                rows.append(
                    {
                        "system": p.get("system", t["name"]),
                        "record_id": row.get(p.get("id", ""), ""),
                        "role": role,
                        **{target: row.get(source, "") for source, target in keep.items()},
                        **mention,
                    }
                )
                per_role[role] += 1
        out = make(f"{t['name']} mentions", rows)
        return io.NodeOutput(
            out,
            ui=report("Stack roles", t, out, {f"{r} rows": f"{n:,}" for r, n in per_role.items()}),
        )


class Concatenate(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowConcatenate",
            display_name="Concatenate tables",
            category=CATEGORY,
            search_aliases=["union", "append", "combine", "stack tables"],
            description="Puts the rows of up to four tables one after another; columns are the union of all.",
            inputs=[
                Table.Input(n, display_name=n.upper(), optional=i > 0, tooltip="A table.")
                for i, n in enumerate("abcd")
            ],
            outputs=[Table.Output(display_name="table", tooltip="All rows.")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, a: Any, b: Any = None, c: Any = None, d: Any = None) -> io.NodeOutput:
        parts = [check_table(t) for t in (a, b, c, d) if t is not None]
        out = make("combined", [r for t in parts for r in t["rows"]])
        return io.NodeOutput(
            out,
            ui=report(
                "Concatenate tables",
                None,
                out,
                {t["name"]: f"{len(t['rows']):,} rows" for t in parts},
            ),
        )


class FilterRows(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowFilterRows",
            display_name="Filter rows",
            category=CATEGORY,
            search_aliases=["where", "keep rows", "drop rows", "select rows"],
            description="Keeps the rows where a column is filled / equals / is one of / matches a pattern (or the opposite).",
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows to filter."),
                io.String.Input(
                    "column", display_name="Column", default="", tooltip="The column to test."
                ),
                io.Combo.Input(
                    "test",
                    display_name="Keep rows where",
                    options=["is filled", "equals", "is one of", "matches"],
                    tooltip="The test.",
                ),
                io.String.Input(
                    "value",
                    display_name="Value",
                    default="",
                    tooltip="Value, comma-separated list, or regular expression.",
                ),
                io.Boolean.Input(
                    "invert",
                    display_name="Invert",
                    default=False,
                    tooltip="Keep the rows that fail the test instead.",
                ),
            ],
            outputs=[Table.Output(display_name="kept", tooltip="The rows that passed.")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, column: str, test: str, value: str, invert: bool) -> io.NodeOutput:
        t = check_table(table)
        need_columns(t, [column])
        options = set(columns_of(value))
        pattern = re.compile(value) if test == "matches" else None
        check = {
            "is filled": lambda v: bool(str(v or "").strip()),
            "equals": lambda v: str(v or "") == value,
            "is one of": lambda v: str(v or "") in options,
            "matches": lambda v: bool(pattern.search(str(v or ""))),
        }[test]
        kept = [r for r in t["rows"] if check(r.get(column)) != invert]
        out = make(t["name"], kept, t["columns"])
        return io.NodeOutput(
            out,
            ui=report(
                "Filter rows",
                t,
                out,
                {
                    "rule": f"{'not ' if invert else ''}{column} {test} {value}".strip(),
                    "dropped": f"{len(t['rows']) - len(kept):,}",
                },
            ),
        )


class JoinTables(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowJoinTables",
            display_name="Join tables",
            category=CATEGORY,
            search_aliases=["lookup", "merge", "vlookup", "left join"],
            description="Adds columns from the right table to each left row whose key matches (left join; first match).",
            inputs=[
                Table.Input("left", display_name="Left", tooltip="The rows to keep."),
                Table.Input("right", display_name="Right", tooltip="Where to look values up."),
                io.String.Input(
                    "left_key",
                    display_name="Left key",
                    default="",
                    tooltip="Column in the left table.",
                ),
                io.String.Input(
                    "right_key",
                    display_name="Right key",
                    default="",
                    tooltip="Column in the right table.",
                ),
                io.String.Input(
                    "bring",
                    display_name="Bring columns",
                    default="",
                    tooltip="Comma-separated right columns to add.",
                ),
                io.String.Input(
                    "prefix",
                    display_name="Prefix",
                    default="",
                    tooltip="Put before the added column names.",
                ),
            ],
            outputs=[
                Table.Output(display_name="table", tooltip="Left rows with the added columns.")
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(
        cls, left: Any, right: Any, left_key: str, right_key: str, bring: str, prefix: str
    ) -> io.NodeOutput:
        lt, rt = check_table(left, "Left"), check_table(right, "Right")
        cols = columns_of(bring)
        need_columns(lt, [left_key])
        need_columns(rt, [right_key, *cols])
        index = {}
        for row in rt["rows"]:
            index.setdefault(str(row.get(right_key) or ""), row)
        rows, hits = [], 0
        for row in lt["rows"]:
            match = index.get(str(row.get(left_key) or "")) if row.get(left_key) else None
            hits += match is not None
            rows.append({**row, **{f"{prefix}{c}": (match or {}).get(c, "") for c in cols}})
        out = make(lt["name"], rows)
        return io.NodeOutput(
            out,
            ui=report(
                "Join tables",
                lt,
                out,
                {"matched rows": f"{hits:,}", "unmatched": f"{len(rows) - hits:,}"},
                [f"{prefix}{c}" for c in cols],
            ),
        )


# --- cleanse ----------------------------------------------------------------------------------
SMALL = str.maketrans("ァィゥェォッャュョヮ", "アイウエオツヤユヨワ")


def normalize(
    value: str, width: bool, katakana: bool, small: bool, spaces: bool, upper: bool
) -> str:
    text = str(value or "")
    if width:
        text = unicodedata.normalize(
            "NFKC", text
        )  # full-width ASCII → ASCII, half-width kana → full-width
    if katakana:
        text = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)
    if small:
        text = text.translate(SMALL)
    if spaces:
        text = re.sub(r"\s+", "", text)
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return text.upper() if upper else text


class NormalizeText(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNormalizeText",
            display_name="Normalize text",
            category=CATEGORY,
            search_aliases=[
                "clean text",
                "kana",
                "nfkc",
                "full-width",
                "half-width",
                "standardize",
            ],
            description="Normalizes text columns with the steps you tick: widths (NFKC: full-width ASCII and half-width kana), hiragana → katakana, small kana → large, spaces, upper case.",
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows to clean."),
                io.String.Input(
                    "columns",
                    display_name="Columns",
                    default="",
                    tooltip="Comma-separated columns to normalize.",
                ),
                io.Boolean.Input(
                    "widths",
                    display_name="Unify widths (NFKC)",
                    default=True,
                    tooltip="ＡＢＣ１２３ → ABC123, ｶﾀｶﾅ → カタカナ.",
                ),
                io.Boolean.Input(
                    "katakana",
                    display_name="Hiragana → katakana",
                    default=True,
                    tooltip="やまだ → ヤマダ.",
                ),
                io.Boolean.Input(
                    "small_kana",
                    display_name="Small kana → large",
                    default=True,
                    tooltip="キョウコ → キヨウコ (as legacy systems store it).",
                ),
                io.Boolean.Input(
                    "spaces",
                    display_name="Remove spaces",
                    default=True,
                    tooltip="山田　太郎 → 山田太郎 (off: collapse to one space).",
                ),
                io.Boolean.Input(
                    "upper",
                    display_name="Upper case",
                    default=False,
                    tooltip="Latin letters to upper case.",
                ),
            ],
            outputs=[
                Table.Output(display_name="table", tooltip="Rows with the columns normalized.")
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(
        cls,
        table: Any,
        columns: str,
        widths: bool,
        katakana: bool,
        small_kana: bool,
        spaces: bool,
        upper: bool,
    ) -> io.NodeOutput:
        t = check_table(table)
        cols = columns_of(columns)
        need_columns(t, cols)
        rows, changed = [], Counter()
        for row in t["rows"]:
            new = dict(row)
            for c in cols:
                new[c] = normalize(row.get(c), widths, katakana, small_kana, spaces, upper)
                changed[c] += new[c] != (row.get(c) or "")
            rows.append(new)
        out = make(t["name"], rows, t["columns"])
        return io.NodeOutput(
            out,
            ui=report(
                "Normalize text",
                t,
                out,
                {f"{c} changed": f"{n:,}" for c, n in changed.items()},
                cols,
            ),
        )


ITAIJI = "髙:高 﨑:崎 邊:辺 邉:辺 齋:斎 齊:斉 𠮷:吉 德:徳 澤:沢 濱:浜 嶋:島 櫻:桜 廣:広 國:国 龍:竜 惠:恵 冨:富"


class MapValues(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowMapValues",
            display_name="Map values",
            category=CATEGORY,
            search_aliases=["replace", "dictionary", "itaiji", "variant kanji", "lookup map"],
            description="Replaces characters, parts of values or whole values using a visible map (from:to pairs), e.g. variant kanji 髙 → 高, OCR look-alikes in readings 力 → カ, or a company marker ｶ) → nothing (ｶ):).",
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows to map."),
                io.String.Input(
                    "columns",
                    display_name="Columns",
                    default="",
                    tooltip="Comma-separated columns.",
                ),
                io.String.Input(
                    "map",
                    display_name="Map",
                    multiline=True,
                    default=ITAIJI,
                    tooltip="from:to pairs separated by spaces or new lines.",
                ),
                io.Combo.Input(
                    "mode",
                    display_name="Replace",
                    options=["characters", "whole values", "parts of values"],
                    tooltip="Each character, the whole value, or each place a from-text appears (longest first; an empty to removes it).",
                ),
            ],
            outputs=[Table.Output(display_name="table", tooltip="Rows with values mapped.")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, columns: str, map: str, mode: str) -> io.NodeOutput:  # noqa: A002
        t = check_table(table)
        cols = columns_of(columns)
        need_columns(t, cols)
        pairs = dict(item.split(":", 1) for item in map.split() if ":" in item)
        if not pairs:
            raise ValueError("Map needs from:to pairs.")
        used, rows = Counter(), []
        for row in t["rows"]:
            new = dict(row)
            for c in cols:
                value = str(row.get(c) or "")
                if mode == "characters":
                    mapped = "".join(pairs.get(ch, ch) for ch in value)
                    used.update(ch for ch in value if ch in pairs)
                elif mode == "parts of values":
                    mapped = value
                    for part in sorted(pairs, key=len, reverse=True):
                        if part and part in mapped:
                            used[part] += 1
                            mapped = mapped.replace(part, pairs[part])
                else:
                    mapped = pairs.get(value, value)
                    used[value] += value in pairs
                new[c] = mapped
            rows.append(new)
        out = make(t["name"], rows, t["columns"])
        notes = {
            "replacements used": ", ".join(
                f"{k}→{pairs[k]} ×{n}" for k, n in used.most_common(8) if n
            )
            or "none"
        }
        return io.NodeOutput(out, ui=report("Map values", t, out, notes, cols))


ERAS = {
    "M": 1868,
    "T": 1912,
    "S": 1926,
    "H": 1989,
    "R": 2019,
    "明治": 1868,
    "大正": 1912,
    "昭和": 1926,
    "平成": 1989,
    "令和": 2019,
}
ERA_CODES = {"1": 1868, "2": 1912, "3": 1926, "4": 1989, "5": 2019}


def parse_date(text: str, pivot: int = 30) -> tuple[str, str]:
    """(ISO date or '', the format recognized). Japanese eras (letters, kanji, 元年, numeric codes), ISO-like,
    compact and two-digit years."""
    s = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not s:
        return "", "empty"

    def iso(y: int, m: int, d: int, kind: str) -> tuple[str, str]:
        try:
            return date(y, m, d).isoformat(), kind
        except ValueError:
            return "", f"{kind} (impossible date)"

    if m := re.fullmatch(r"([MTSHR])\s*(\d{1,2})[./-](\d{1,2})[./-](\d{1,2})", s):
        return iso(ERAS[m[1]] + int(m[2]) - 1, int(m[3]), int(m[4]), "era letter (S45.03.12)")
    if m := re.fullmatch(r"(明治|大正|昭和|平成|令和)(元|\d{1,2})年(\d{1,2})月(\d{1,2})日", s):
        return iso(
            ERAS[m[1]] + (1 if m[2] == "元" else int(m[2])) - 1,
            int(m[3]),
            int(m[4]),
            "era kanji (昭和45年3月12日)",
        )
    if m := re.fullmatch(r"([1-5])(\d{2})(\d{2})(\d{2})", s):
        return iso(ERA_CODES[m[1]] + int(m[2]) - 1, int(m[3]), int(m[4]), "era code (3450312)")
    if m := re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s) or re.fullmatch(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日", s
    ):
        return iso(int(m[1]), int(m[2]), int(m[3]), "year first (1970/03/12)")
    if m := re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s):
        return iso(int(m[1]), int(m[2]), int(m[3]), "compact (19700312)")
    if m := re.fullmatch(r"(\d{2})[/-](\d{1,2})[/-](\d{1,2})", s):
        year = int(m[1])
        return iso(
            year + (2000 if year <= pivot else 1900),
            int(m[2]),
            int(m[3]),
            "two-digit year (70/03/12)",
        )
    return "", "not a date"


class ParseDates(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowParseDates",
            display_name="Parse dates",
            category=CATEGORY,
            search_aliases=["date", "era", "wareki", "和暦", "iso date", "birth date"],
            description="Turns dates in any of these forms into YYYY-MM-DD: Japanese era letters (S45.03.12), era kanji (昭和45年3月12日, 元年), era codes (3450312), 1970/03/12, 19700312, 70/03/12. Shows how many values had each format.",
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows with date columns."),
                io.String.Input(
                    "columns",
                    display_name="Columns",
                    default="",
                    tooltip="Comma-separated date columns.",
                ),
                io.Int.Input(
                    "pivot",
                    display_name="Two-digit year pivot",
                    default=30,
                    min=0,
                    max=99,
                    tooltip="00–pivot → 2000s, above → 1900s.",
                ),
            ],
            outputs=[
                Table.Output(
                    display_name="table",
                    tooltip="Rows with ISO dates (unparseable values left empty).",
                )
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, columns: str, pivot: int) -> io.NodeOutput:
        t = check_table(table)
        cols = columns_of(columns)
        need_columns(t, cols)
        formats, rows = Counter(), []
        for row in t["rows"]:
            new = dict(row)
            for c in cols:
                new[c], kind = parse_date(row.get(c), pivot)
                formats[kind] += 1
            rows.append(new)
        out = make(t["name"], rows, t["columns"])
        return io.NodeOutput(
            out,
            ui=report(
                "Parse dates",
                t,
                out,
                {f"format: {k}": f"{n:,}" for k, n in formats.most_common()},
                cols,
            ),
        )


class ExtractDigits(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowExtractDigits",
            display_name="Extract digits",
            category=CATEGORY,
            search_aliases=["phone", "postal code", "zip", "digits", "numbers only"],
            description="Keeps only the digits of each value (after unifying widths): 03(1234)5678 → 0312345678, 〒１６０-０００１ → 1600001. Values with fewer digits than the minimum become empty.",
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows to clean."),
                io.String.Input(
                    "columns",
                    display_name="Columns",
                    default="",
                    tooltip="Comma-separated columns.",
                ),
                io.Int.Input(
                    "min_digits",
                    display_name="Minimum digits",
                    default=0,
                    min=0,
                    max=30,
                    tooltip="Shorter results become empty (placeholders, fragments).",
                ),
            ],
            outputs=[Table.Output(display_name="table", tooltip="Rows with digits only.")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, columns: str, min_digits: int) -> io.NodeOutput:
        t = check_table(table)
        cols = columns_of(columns)
        need_columns(t, cols)
        rows, emptied = [], 0
        for row in t["rows"]:
            new = dict(row)
            for c in cols:
                digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", str(row.get(c) or "")))
                if digits and len(digits) < min_digits:
                    digits, emptied = "", emptied + 1
                new[c] = digits
            rows.append(new)
        out = make(t["name"], rows, t["columns"])
        return io.NodeOutput(
            out, ui=report("Extract digits", t, out, {"too short, emptied": f"{emptied:,}"}, cols)
        )


# --- names ------------------------------------------------------------------------------------
SEPARATORS = re.compile(r"[\s\u3000・･]+")


def kana_key(value: str) -> str:
    return normalize(value, True, True, True, True, False)


class SplitName(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowSplitName",
            display_name="Split name",
            category=CATEGORY,
            search_aliases=["surname", "given name", "family name", "姓名", "split"],
            description=(
                "Splits a name (reading) into surname and given name, normalized. Values written with a space "
                "teach it the surnames; values written without one are split at the longest known surname. "
                "Shows how many were split each way. Comparing given names separately catches maiden names "
                "(same given name and birth, different surname) and family members (same surname, different given name)."
            ),
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows with a name column."),
                io.String.Input(
                    "column",
                    display_name="Column",
                    default="kana",
                    tooltip="The name or reading column to split.",
                ),
                io.Int.Input(
                    "min_seen",
                    display_name="Surname seen at least",
                    default=2,
                    min=1,
                    max=100,
                    tooltip="A surname must appear this often with a space to be trusted.",
                ),
            ],
            outputs=[
                Table.Output(
                    display_name="table",
                    tooltip="Rows with <column>_surname and <column>_given added.",
                )
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, column: str, min_seen: int) -> io.NodeOutput:
        t = check_table(table)
        need_columns(t, [column])
        seen = Counter()
        for row in t["rows"]:
            parts = SEPARATORS.split(str(row.get(column) or "").strip())
            if len(parts) >= 2 and parts[0]:
                seen[kana_key(parts[0])] += 1
        known = sorted((s for s, n in seen.items() if n >= min_seen and s), key=len, reverse=True)
        how, rows = Counter(), []
        for row in t["rows"]:
            value = str(row.get(column) or "").strip()
            parts = SEPARATORS.split(value)
            surname = given = ""
            if len(parts) >= 2 and parts[0]:
                surname, given = kana_key(parts[0]), kana_key("".join(parts[1:]))
                how["split at the space"] += 1
            elif value:
                key = kana_key(value)
                match = next((s for s in known if key.startswith(s) and len(key) > len(s)), None)
                if match:
                    surname, given = match, key[len(match) :]
                    how["split at a known surname"] += 1
                else:
                    how["could not split"] += 1
            rows.append({**row, f"{column}_surname": surname, f"{column}_given": given})
        out = make(t["name"], rows)
        notes = {**{k: f"{n:,}" for k, n in how.items()}, "known surnames": f"{len(known):,}"}
        return io.NodeOutput(
            out, ui=report("Split name", t, out, notes, [f"{column}_surname", f"{column}_given"])
        )


# --- matching: candidates and comparison ------------------------------------------------------
COMMON_PIECE = 1000  # a 2-character piece more records share than this says nothing (skipped)


def similar_pairs(
    values: list[tuple[str, str]], least: float, top: int = 10
) -> set[tuple[str, str]]:
    """Sparse retrieval: records whose values share most of their 2-character pieces (cosine of the
    piece sets at least ``least``), the ``top`` closest per record. Catches a typo or a cut-off
    reading that an exact key misses."""
    pieces = [{v[i : i + 2] for i in range(len(v) - 1)} or {v} for _, v in values]
    index = defaultdict(list)
    for i, ps in enumerate(pieces):
        for piece in ps:
            index[piece].append(i)
    found = set()
    for i, ps in enumerate(pieces):
        shared = Counter(
            j for piece in ps if len(index[piece]) <= COMMON_PIECE for j in index[piece] if j != i
        )
        close = [
            (n / (len(ps) * len(pieces[j])) ** 0.5, j)
            for j, n in shared.items()
            if n / (len(ps) * len(pieces[j])) ** 0.5 >= least
        ]
        for _, j in sorted(close, reverse=True)[:top]:
            if values[i][0] != values[j][0]:
                found.add(tuple(sorted((values[i][0], values[j][0]))))
    return found


class FindCandidates(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowFindCandidates",
            display_name="Find candidate pairs",
            category=CATEGORY,
            search_aliases=["blocking", "candidates", "pairs", "record linkage"],
            description="Finds the pairs worth comparing. Exact keys (blocking): rows sharing a value in a key become pairs; groups larger than the limit are skipped (a common surname would pair everyone). Similar values (sparse retrieval): `kana ~ 0.5` pairs rows whose kana share most of their 2-character pieces, so a typo or a cut-off reading still meets. Each pass is listed with its count.",
            inputs=[
                Table.Input(
                    "table", display_name="Records", tooltip="One row per record (mention)."
                ),
                io.String.Input(
                    "id",
                    display_name="Id columns",
                    default="system, record_id, role",
                    tooltip="Columns that together identify a record.",
                ),
                io.String.Input(
                    "keys",
                    display_name="Blocking keys",
                    multiline=True,
                    default="kana\nbirth\nphone",
                    tooltip="One pass per line: a column, columns joined with + (kana+birth), or column ~ similarity (kana ~ 0.5: one typo in five letters still meets).",
                ),
                io.Int.Input(
                    "max_group",
                    display_name="Largest group",
                    default=50,
                    min=2,
                    max=10000,
                    tooltip="Skip key values shared by more records than this.",
                ),
            ],
            outputs=[
                Table.Output(
                    display_name="pairs", tooltip="left, right, keys (which passes found the pair)."
                )
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, id: str, keys: str, max_group: int) -> io.NodeOutput:  # noqa: A002
        t = check_table(table)
        ids = columns_of(id)
        lines = [line.strip() for line in keys.splitlines() if line.strip()]
        similar = [line.split("~") for line in lines if "~" in line]
        passes = [[c.strip() for c in line.split("+")] for line in lines if "~" not in line]
        need_columns(t, ids + [c for p in passes for c in p] + [c.strip() for c, _ in similar])
        mention = lambda r: "/".join(str(r.get(c) or "") for c in ids)  # noqa: E731
        found, notes = defaultdict(set), {}
        for column, least in similar:
            column, least = column.strip(), float(least)
            values = [(mention(r), str(r[column])) for r in t["rows"] if r.get(column)]
            close = similar_pairs(values, least)
            for pair in close:
                found[pair].add(f"{column}~")
            notes[f"pass {column} ~ {least:g}"] = (
                f"{len(close):,} pairs (similar, up to 10 per record)"
            )
        for p in passes:
            groups = defaultdict(list)
            for r in t["rows"]:
                value = "|".join(str(r.get(c) or "") for c in p)
                if all(r.get(c) for c in p):
                    groups[value].append(mention(r))
            pairs = skipped = 0
            for members in groups.values():
                if len(members) > max_group:
                    skipped += 1
                    continue
                for i, a in enumerate(members):
                    for b in members[i + 1 :]:
                        if a != b:
                            found[tuple(sorted((a, b)))].add("+".join(p))
                            pairs += 1
            notes[f"pass {'+'.join(p)}"] = f"{pairs:,} pairs, {skipped} groups too large"
        rows = [
            {"left": a, "right": b, "keys": ", ".join(sorted(k))}
            for (a, b), k in sorted(found.items())
        ]
        out = make("candidate pairs", rows, ["left", "right", "keys"])
        return io.NodeOutput(
            out,
            ui=report(
                "Find candidate pairs", t, out, {**notes, "distinct pairs": f"{len(rows):,}"}
            ),
        )


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0 if a else 0.0
    if not a or not b:
        return 0.0
    reach = max(len(a), len(b)) // 2 - 1
    used_b = [False] * len(b)
    matches_a = []
    for i, ch in enumerate(a):
        for j in range(max(0, i - reach), min(len(b), i + reach + 1)):
            if not used_b[j] and b[j] == ch:
                used_b[j] = True
                matches_a.append(ch)
                break
    if not matches_a:
        return 0.0
    matches_b = [b[j] for j, used in enumerate(used_b) if used]
    transpositions = sum(x != y for x, y in zip(matches_a, matches_b, strict=True)) / 2
    m = len(matches_a)
    jaro = (m / len(a) + m / len(b) + (m - transpositions) / m) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4], strict=False):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def compare_dates(a: str, b: str) -> str:
    if not a or not b:
        return "missing"
    if a == b:
        return "exact"
    ya, ma, da = a.split("-")
    yb, mb, db = b.split("-")
    if ya == yb and ma == db and da == mb:
        return "close"  # day and month swapped
    # One digit typed wrong, or an era mistake (Shōwa read as Heisei: 63 years; Heisei as Reiwa: 30).
    if sum(x != y for x, y in zip(a, b, strict=False)) == 1 or (
        ma == mb and da == db and abs(int(ya) - int(yb)) in (63, 30)
    ):
        return "close"
    return "conflict"


def compare_value(a: Any, b: Any, method: str, similar: float) -> str:
    a, b = str(a or "").strip(), str(b or "").strip()
    if method == "date":
        return compare_dates(a, b)
    if not a or not b:
        return "missing"
    if a == b:
        return "exact" if method != "same" else "same"
    if method == "same":
        return "different"
    return "similar" if jaro_winkler(a, b) >= similar else "different"


class CompareFields(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowCompareFields",
            display_name="Compare fields",
            category=CATEGORY,
            search_aliases=[
                "compare",
                "similarity",
                "jaro winkler",
                "comparison levels",
                "record linkage",
            ],
            description=(
                "Compares each candidate pair field by field into plain levels — text: exact / similar (Jaro-Winkler "
                "at least the threshold) / different / missing; date: exact / close (day-month swapped, one digit, "
                "era mistake) / conflict / missing; same: same / different / missing — and writes pairs.jsonl for "
                "Learn weights and Decision rules. Shows how often each level occurs."
            ),
            inputs=[
                Table.Input("records", display_name="Records", tooltip="The cleaned records."),
                Table.Input("pairs", display_name="Pairs", tooltip="From Find candidate pairs."),
                io.String.Input(
                    "id",
                    display_name="Id columns",
                    default="system, record_id, role",
                    tooltip="Same as in Find candidate pairs.",
                ),
                io.String.Input(
                    "fields",
                    display_name="Fields",
                    multiline=True,
                    default=(
                        "kana: {method: text, similar: 0.88, common: 20}\nname: {method: text, similar: 0.85}\nbirth: {method: date}\n"
                        "phone: {method: same}\naddress: {method: text, similar: 0.8}\n"
                    ),
                    tooltip="YAML: field: {method: text | date | same, similar: threshold for text, common: with an exact match, adds <field>_freq = common (value shared by at least this many records) or rare}.",
                ),
                io.String.Input(
                    "show",
                    display_name="Show in Review",
                    default="name, kana, birth, address, phone",
                    tooltip="Columns shown for each record of a pair.",
                ),
            ],
            outputs=[
                Table.Output(display_name="compared", tooltip="Pairs with one level per field."),
                RecordPairs.Output(
                    display_name="data",
                    tooltip="Run folders with pairs.jsonl, for Learn weights and Decision rules.",
                ),
            ],
            hidden=[io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, records: Any, pairs: Any, id: str, fields: str, show: str) -> io.NodeOutput:  # noqa: A002
        from .matching_nodes import own_folder, workflow_repo

        rt, pt = check_table(records, "Records"), check_table(pairs, "Pairs")
        spec = config(fields, "Fields")
        if not isinstance(spec, dict) or not spec:
            raise ValueError("Fields needs field: {method: ...} entries.")
        ids, shown = columns_of(id), columns_of(show)
        need_columns(rt, ids + list(spec) + shown)
        by_id = {"/".join(str(r.get(c) or "") for c in ids): r for r in rt["rows"]}
        # A shared common value (佐藤) is weak evidence; a shared rare one (勅使河原) is strong.
        common = {
            field: {
                v
                for v, n in Counter(str(r.get(field) or "") for r in rt["rows"]).items()
                if v and n >= how["common"]
            }
            for field, how in spec.items()
            if isinstance(how, dict) and how.get("common")
        }
        rows, levels = [], defaultdict(Counter)
        for p in pt["rows"]:
            a, b = by_id.get(p["left"]), by_id.get(p["right"])
            if a is None or b is None:
                continue
            # Stable across runs (not the row number), so your review decisions stay on their pair.
            digest = hashlib.sha1(f"{p['left']}|{p['right']}".encode()).hexdigest()[:12]
            row = {"id": f"p{digest}", "left": p["left"], "right": p["right"]}
            # A rule can send pairs only an embedding model found to the reranker.
            row["found_by"] = "embedding" if p.get("keys") == "embedding" else "keys"
            for field, how in spec.items():
                how = how if isinstance(how, dict) else {}
                row[field] = compare_value(
                    a.get(field),
                    b.get(field),
                    how.get("method", "text"),
                    float(how.get("similar", 0.88)),
                )
                if field in common:  # its own field, so rules on `exact` keep working
                    row[f"{field}_freq"] = (
                        ("common" if str(a.get(field) or "") in common[field] else "rare")
                        if row[field] == "exact"
                        else "n/a"
                    )
                    levels[f"{field}_freq"][row[f"{field}_freq"]] += 1
                levels[field][row[field]] += 1
            row["a"], row["b"] = {c: a.get(c) for c in shown}, {c: b.get(c) for c in shown}
            rows.append(row)
        folder = own_folder(workflow_repo(cls), "compare_fields")
        with open(folder / "pairs.jsonl", "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        out = make(
            "compared pairs", [{k: v for k, v in r.items() if k not in ("a", "b")} for r in rows]
        )
        notes = {
            field: ", ".join(f"{lv} {n:,}" for lv, n in c.most_common())
            for field, c in levels.items()
        }
        ui = report("Compare fields", pt, out, notes)
        ui["aws_wires"] *= 2  # the table and the pairs file carry the same pairs
        pairs = ref(
            "record_pairs",
            "pairs",
            folder / "pairs.jsonl",
            len(rows),
            run_folders=[str(folder.resolve())],
        )
        ui["aws_datasets"] = ui_datasets((1, pairs))
        return io.NodeOutput(out, pairs, ui=ui)


class AttachGroups(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowAttachGroups",
            display_name="Attach group ids",
            category=CATEGORY,
            search_aliases=["customer id", "cluster id", "assign id", "result"],
            description="Gives every record its group id from Cluster guard (clusters.csv); records in no group get their own new id.",
            inputs=[
                Table.Input("records", display_name="Records", tooltip="All records."),
                io.MultiType.Input(
                    "groups",
                    types=[RecordClusters, io.Dict],
                    display_name="Groups",
                    tooltip="Connect Cluster guard's customers.",
                ),
                io.String.Input(
                    "id",
                    display_name="Id columns",
                    default="system, record_id, role",
                    tooltip="Same as in Find candidate pairs.",
                ),
                io.String.Input(
                    "column",
                    display_name="Id column",
                    default="customer_id",
                    tooltip="Name of the new column.",
                ),
            ],
            outputs=[Table.Output(display_name="table", tooltip="Records with their group id.")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, records: Any, groups: Any, id: str, column: str) -> io.NodeOutput:  # noqa: A002
        from .datasets import dataset_file

        rt = check_table(records, "Records")
        ids = columns_of(id)
        need_columns(rt, ids)
        # record_clusters rows are (record, cluster); files from before the standard say (mention, customer).
        clusters = {
            r.get("record", r.get("mention")): r.get("cluster", r.get("customer"))
            for r in csv.DictReader(
                dataset_file(groups, "clusters.csv").read_text(encoding="utf-8").splitlines()
            )
        }
        n, rows, alone = len(set(clusters.values())), [], 0
        for r in rt["rows"]:
            key = "/".join(str(r.get(c) or "") for c in ids)
            group = clusters.get(key)
            if group is None:
                n, alone = n + 1, alone + 1
                group = f"S{n:06d}"
            rows.append({**r, column: group})
        out = make(rt["name"], rows)
        return io.NodeOutput(
            out,
            ui=report(
                "Attach group ids",
                rt,
                out,
                {"in a group": f"{len(rows) - alone:,}", "on their own": f"{alone:,}"},
                [column],
            ),
        )


class Stub(io.ComfyNode):
    """A planned stage's placeholder: passes its input on, typed as its input, so the pipeline runs
    end to end before the stage is built."""

    # A stand-in for any table or dataset the standard registers (memory datasets included).
    T = io.MatchType.Template("data", [Table, *(custom(s) for s in dataset_sockets(storage=None))])

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowStub",
            display_name="Stub (not built yet)",
            category=CATEGORY,
            search_aliases=["placeholder", "todo", "passthrough", "skeleton"],
            description="Stands in for a stage that is not built yet: passes its data on unchanged.",
            inputs=[
                io.MatchType.Input(
                    "data",
                    template=cls.T,
                    display_name="Data",
                    tooltip="What the stage will take; passed on as it is.",
                )
            ],
            outputs=[io.MatchType.Output(template=cls.T, display_name="data")],
        )

    @classmethod
    def execute(cls, data: Any) -> io.NodeOutput:
        rows = data.get("rows") if isinstance(data, dict) else None
        line = (
            f"stub · passes {len(rows):,} rows on"
            if isinstance(rows, list)
            else "stub · passes its data on"
        )
        return io.NodeOutput(data, ui={"text": (line,), "aws_headline": (line,)})


NODES = [
    Stub,
    SplitName,
    ReadTable,
    WriteTable,
    StackRoles,
    TableFromResult,
    Concatenate,
    FilterRows,
    JoinTables,
    NormalizeText,
    MapValues,
    ParseDates,
    ExtractDigits,
    FindCandidates,
    CompareFields,
    AttachGroups,
]
