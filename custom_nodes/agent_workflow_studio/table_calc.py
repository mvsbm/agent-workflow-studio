"""Calculations on tables as visible nodes: parse numbers, remove duplicates, compute a column,
summarize by group. Together with the other table nodes they cover everyday reporting, so such work
is readable settings on the canvas rather than agent-written code.
"""

from __future__ import annotations

import ast
import unicodedata
from collections import defaultdict
from typing import Any

from comfy_api.latest import io

from .table_nodes import CATEGORY, Table, check_table, columns_of, make, need_columns, report


def to_number(value: Any) -> float | int | None:
    """'¥1,200' -> 1200, '３．５' -> 3.5, '' -> None. Not a number -> None."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    for mark in ("¥", "￥", "円", "$", "€", ",", " "):
        text = text.replace(mark, "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number == int(number) else number


class ParseNumbers(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowParseNumbers",
            display_name="Parse numbers",
            category=CATEGORY,
            search_aliases=["to number", "price", "amount", "currency", "numeric", "cast"],
            description="Turns text like ¥1,200, 3,500円 or ３．５ into numbers; a blank can mean a default (e.g. quantity 1).",
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows to convert."),
                io.String.Input(
                    "columns",
                    display_name="Columns",
                    default="",
                    tooltip="Columns to convert, comma-separated.",
                ),
                io.String.Input(
                    "blank_means",
                    display_name="Blank means",
                    default="",
                    tooltip="A number to use for blank cells (empty: leave them blank).",
                ),
            ],
            outputs=[Table.Output(display_name="table")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, columns: str, blank_means: str) -> io.NodeOutput:
        t, cols = check_table(table), columns_of(columns)
        need_columns(t, cols)
        default = to_number(blank_means)
        filled = failed = 0
        rows = []
        for row in t["rows"]:
            row = dict(row)
            for c in cols:
                if not str(row.get(c) or "").strip():
                    row[c] = default if default is not None else ""
                    filled += default is not None
                    continue
                number = to_number(row[c])
                failed += number is None
                row[c] = number if number is not None else ""
            rows.append(row)
        out = make(t["name"], rows, t["columns"])
        notes = {"blanks filled": f"{filled:,}", "not a number (emptied)": f"{failed:,}"}
        return io.NodeOutput(out, ui=report("Parse numbers", t, out, notes, cols))


class RemoveDuplicates(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowRemoveDuplicates",
            display_name="Remove duplicates",
            category=CATEGORY,
            search_aliases=["dedupe", "distinct", "unique", "drop duplicates"],
            description="Keeps the first row for each key (e.g. order_id); later rows with the same key are dropped.",
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows to deduplicate."),
                io.String.Input(
                    "key",
                    display_name="Key columns",
                    default="",
                    tooltip="Columns that identify a row, comma-separated (empty: all).",
                ),
            ],
            outputs=[Table.Output(display_name="table")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, key: str) -> io.NodeOutput:
        t = check_table(table)
        cols = columns_of(key) or t["columns"]
        need_columns(t, cols)
        seen, kept = set(), []
        for row in t["rows"]:
            k = tuple(str(row.get(c) or "").strip() for c in cols)
            if k not in seen:
                seen.add(k)
                kept.append(row)
        out = make(t["name"], kept, t["columns"])
        return io.NodeOutput(
            out,
            ui=report(
                "Remove duplicates",
                t,
                out,
                {"key": ", ".join(cols), "dropped": f"{len(t['rows']) - len(kept):,}"},
            ),
        )


# The expression language of Compute column: arithmetic and a few functions over the row's columns.
FUNCTIONS = {
    "round": lambda x, n=0: round(x, int(n)),
    "abs": abs,
    "min": min,
    "max": max,
    "left": lambda s, n: str(s)[: int(n)],
    "right": lambda s, n: str(s)[-int(n) :] if int(n) else "",
    "lower": lambda s: str(s).lower(),
    "upper": lambda s: str(s).upper(),
    "text": lambda x: "" if x is None else str(x),
    "number": to_number,
    "length": lambda s: len(str(s)),
}


def _operator(op):
    """Arithmetic on numbers only (no text repetition); + also joins text to text."""
    compute = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
    }[op]

    def apply(a, b):
        numbers = all(isinstance(x, int | float) and not isinstance(x, bool) for x in (a, b))
        texts = op is ast.Add and isinstance(a, str) and isinstance(b, str)
        if not (numbers or texts):
            raise TypeError("arithmetic needs numbers; + joins text to text")
        return compute(a, b)

    return apply


OPERATORS = {op: _operator(op) for op in (ast.Add, ast.Sub, ast.Mult, ast.Div)}


def compile_expression(text: str) -> ast.Expression:
    """Parse and check an expression: only numbers, text, column names, + - * /, and FUNCTIONS."""
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as error:
        raise ValueError(f"The expression is not valid: {error.msg}.") from None
    for node in ast.walk(tree):
        allowed = (
            isinstance(
                node,
                ast.Expression
                | ast.BinOp
                | ast.UnaryOp
                | ast.Constant
                | ast.Name
                | ast.Load
                | ast.USub
                | ast.UAdd
                | ast.Call,
            )
            or type(node) in OPERATORS
        )
        if not allowed:
            raise ValueError(
                f"Not allowed in an expression: {type(node).__name__}. Use + - * /, columns and {', '.join(FUNCTIONS)}."
            )
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords
        ):
            raise ValueError(f"Unknown function. Functions: {', '.join(FUNCTIONS)}.")
        if isinstance(node, ast.Constant) and not isinstance(node.value, int | float | str):
            raise ValueError("Only numbers and text may appear as values.")
    return tree


def evaluate(tree: ast.AST, row: dict) -> Any:
    if isinstance(tree, ast.Expression):
        return evaluate(tree.body, row)
    if isinstance(tree, ast.Constant):
        return tree.value
    if isinstance(tree, ast.Name):
        if tree.id not in row:
            raise ValueError(f"No column {tree.id}.")
        value = row[tree.id]
        number = to_number(value) if isinstance(value, str) else value
        return number if number is not None else value
    if isinstance(tree, ast.UnaryOp):
        value = evaluate(tree.operand, row)
        return -value if isinstance(tree.op, ast.USub) else value
    if isinstance(tree, ast.BinOp):
        return OPERATORS[type(tree.op)](evaluate(tree.left, row), evaluate(tree.right, row))
    return FUNCTIONS[tree.func.id](*(evaluate(a, row) for a in tree.args))


class ComputeColumn(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowComputeColumn",
            display_name="Compute column",
            category=CATEGORY,
            search_aliases=[
                "formula",
                "calculate",
                "derive",
                "expression",
                "new column",
                "multiply",
            ],
            description=(
                "Adds (or replaces) a column computed per row, e.g. revenue = quantity * unit_price or "
                "month = left(date, 7). Allowed: + - * /, columns, numbers, text and "
                + ", ".join(FUNCTIONS)
                + "."
            ),
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows to compute on."),
                io.String.Input(
                    "column", display_name="New column", default="", tooltip="The column to write."
                ),
                io.String.Input(
                    "expression",
                    display_name="Expression",
                    default="",
                    tooltip="e.g. quantity * unit_price",
                ),
            ],
            outputs=[Table.Output(display_name="table")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, column: str, expression: str) -> io.NodeOutput:
        t, name = check_table(table), column.strip()
        if not name:
            raise ValueError("Name the new column.")
        tree = compile_expression(expression)
        rows, failed = [], 0
        for row in t["rows"]:
            try:
                value = evaluate(tree, row)
            except (TypeError, ZeroDivisionError, ValueError) as error:
                if "No column" in str(error):
                    raise
                value, failed = "", failed + 1
            if isinstance(value, float) and value == int(value):
                value = int(value)
            rows.append({**row, name: value})
        out = make(t["name"], rows, [*t["columns"], *([name] if name not in t["columns"] else [])])
        notes = {"formula": f"{name} = {expression}", "could not compute (blank)": f"{failed:,}"}
        return io.NodeOutput(out, ui=report("Compute column", t, out, notes, [name]))


AGGREGATES = {
    "sum": lambda xs: sum(x for x in xs if x is not None),
    "mean": lambda xs: (lambda v: sum(v) / len(v) if v else "")([x for x in xs if x is not None]),
    "min": lambda xs: min((x for x in xs if x is not None), default=""),
    "max": lambda xs: max((x for x in xs if x is not None), default=""),
    "count": len,
    "count_distinct": lambda xs: len({x for x in xs if x not in (None, "")}),
}


def parse_aggregations(text: str) -> list[tuple[str, str, str]]:
    """'revenue: sum(revenue)' lines -> (name, function, column); count() needs no column."""
    out = []
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        name, _, spec = line.partition(":")
        func, _, rest = spec.strip().partition("(")
        func, column = func.strip(), rest.rstrip(")").strip()
        if not name.strip() or func not in AGGREGATES or (func != "count" and not column):
            raise ValueError(
                f"Write each aggregation as 'name: function(column)', function one of {', '.join(AGGREGATES)}: {line!r}"
            )
        out.append((name.strip(), func, column))
    if not out:
        raise ValueError("Give at least one aggregation, e.g. revenue: sum(revenue).")
    return out


class Summarize(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowSummarize",
            display_name="Summarize (group by)",
            category=CATEGORY,
            search_aliases=["group by", "aggregate", "pivot", "total", "sum", "count", "report"],
            description="One row per group with totals, counts, means, min/max or distinct counts, e.g. revenue per region and month.",
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows to summarize."),
                io.String.Input(
                    "group_by",
                    display_name="Group by",
                    default="",
                    tooltip="Columns, comma-separated (empty: one row for all).",
                ),
                io.String.Input(
                    "aggregations",
                    display_name="Aggregations",
                    multiline=True,
                    default="rows: count()",
                    tooltip="One per line: name: function(column); functions "
                    + ", ".join(AGGREGATES)
                    + ".",
                ),
            ],
            outputs=[Table.Output(display_name="summary")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, group_by: str, aggregations: str) -> io.NodeOutput:
        t, keys, aggs = check_table(table), columns_of(group_by), parse_aggregations(aggregations)
        need_columns(t, keys + [c for _, _, c in aggs if c])
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for row in t["rows"]:
            groups[tuple(str(row.get(k) or "").strip() for k in keys)].append(row)
        rows = []
        for key in sorted(groups):
            members = groups[key]
            row = dict(zip(keys, key, strict=True))
            for name, func, column in aggs:
                values = (
                    [m for m in members]
                    if func == "count" and not column
                    else [
                        (
                            to_number(m.get(column))
                            if func not in ("count", "count_distinct")
                            else m.get(column)
                        )
                        for m in members
                    ]
                )
                value = AGGREGATES[func](values)
                row[name] = (
                    int(value) if isinstance(value, float) and value == int(value) else value
                )
            rows.append(row)
        out = make("summary", rows, [*keys, *(n for n, _, _ in aggs)])
        notes = {"groups": f"{len(rows):,}", "by": ", ".join(keys) or "(all rows)"}
        return io.NodeOutput(out, ui=report("Summarize", t, out, notes))


TESTS = ("is empty", "is filled", "equals", "is one of", "matches", "not matches", "is duplicate")


def parse_rules(text: str) -> list[tuple[str, str, str, str]]:
    """'name: column test value' lines -> (name, column, test, value)."""
    rules = []
    for line in [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]:
        name, _, spec = line.partition(":")
        column, _, rest = spec.strip().partition(" ")
        test = next(
            (t for t in sorted(TESTS, key=len, reverse=True) if rest.strip().startswith(t)), None
        )
        if not name.strip() or not column or not test:
            raise ValueError(
                f"Write each check as 'name: column test [value]', test one of {', '.join(TESTS)}: {line!r}"
            )
        rules.append((name.strip(), column, test, rest.strip()[len(test) :].strip()))
    if not rules:
        raise ValueError("Give at least one check, e.g. missing_phone: phone is empty.")
    return rules


class DataChecks(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowDataChecks",
            display_name="Data checks",
            category=CATEGORY,
            search_aliases=[
                "data quality",
                "validate",
                "rules",
                "count problems",
                "audit",
                "duplicates",
            ],
            description=(
                "Counts the rows (or, for is duplicate, the values) that break each readable rule, with examples: "
                "e.g. missing_phone: phone is empty · invalid_email: email not matches ^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$ · "
                "duplicate_id: customer_id is duplicate."
            ),
            inputs=[
                Table.Input("table", display_name="Table", tooltip="Rows to check."),
                io.String.Input(
                    "rules",
                    display_name="Checks",
                    multiline=True,
                    default="",
                    tooltip="One per line: name: column test [value]; tests "
                    + ", ".join(TESTS)
                    + ".",
                ),
            ],
            outputs=[
                Table.Output(
                    display_name="counts", tooltip="One row per check: check, count, examples."
                )
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, table: Any, rules: str) -> io.NodeOutput:
        import re
        from collections import Counter

        t, parsed = check_table(table), parse_rules(rules)
        need_columns(t, [c for _, c, _, _ in parsed])
        rows = []
        for name, column, test, value in parsed:
            values = [str(r.get(column) or "").strip() for r in t["rows"]]
            if test == "is duplicate":
                counts = Counter(v for v in values if v)
                hits = sorted(v for v, n in counts.items() if n > 1)
            else:
                pattern = re.compile(value) if "matches" in test else None
                check = {
                    "is empty": lambda v: not v,
                    "is filled": bool,
                    "equals": lambda v, value=value: v == value,
                    "is one of": lambda v, value=value: v in set(columns_of(value)),
                    "matches": lambda v, pattern=pattern: bool(pattern.search(v)),
                    "not matches": lambda v, pattern=pattern: not pattern.search(v),
                }[test]
                hits = [v for v in values if check(v)]
            rows.append(
                {
                    "check": name,
                    "count": len(hits),
                    "rule": f"{column} {test} {value}".strip(),
                    "examples": " | ".join(hits[:3]),
                }
            )
        out = make("checks", rows, ["check", "count", "rule", "examples"])
        notes = {f"{r['check']}": f"{r['count']:,}" for r in rows}
        return io.NodeOutput(out, ui=report("Data checks", t, out, notes))


NODES = [ParseNumbers, RemoveDuplicates, ComputeColumn, Summarize, DataChecks]
