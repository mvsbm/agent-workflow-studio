"""Calculation nodes: numbers from messy text, duplicates by key, safe formulas, group-by totals."""

import unittest

from .table_calc import (
    ComputeColumn,
    DataChecks,
    ParseNumbers,
    RemoveDuplicates,
    Summarize,
    compile_expression,
    evaluate,
    to_number,
)
from .table_nodes import make

ORDERS = make(
    "orders",
    [
        {"id": "A1", "region": "East", "date": "2026-01-05", "qty": "", "price": "¥1,200"},
        {"id": "A2", "region": "East", "date": "2026-01-20", "qty": "2", "price": "3,500円"},
        {"id": "A2", "region": "East", "date": "2026-01-20", "qty": "2", "price": "3,500円"},
        {"id": "A3", "region": "West", "date": "2026-02-01", "qty": "3", "price": "１００"},
    ],
)


class TableCalcTests(unittest.TestCase):
    def test_numbers_from_messy_text(self) -> None:
        self.assertEqual(
            [to_number(v) for v in ("¥1,200", "3,500円", "３．５", "", "abc")],
            [1200, 3500, 3.5, None, None],
        )
        out = ParseNumbers.execute(ORDERS, "qty, price", "1").args[0]
        self.assertEqual(
            [(r["qty"], r["price"]) for r in out["rows"]],
            [(1, 1200), (2, 3500), (2, 3500), (3, 100)],
        )

    def test_duplicates_are_dropped_by_key(self) -> None:
        out = RemoveDuplicates.execute(ORDERS, "id").args[0]
        self.assertEqual([r["id"] for r in out["rows"]], ["A1", "A2", "A3"])

    def test_formulas_are_safe_and_per_row(self) -> None:
        priced = ParseNumbers.execute(ORDERS, "qty, price", "1").args[0]
        out = ComputeColumn.execute(priced, "revenue", "qty * price").args[0]
        self.assertEqual([r["revenue"] for r in out["rows"]], [1200, 7000, 7000, 300])
        self.assertEqual(
            evaluate(compile_expression("left(date, 7)"), ORDERS["rows"][0]), "2026-01"
        )
        for bad in ("__import__('os')", "price.real", "[1, 2]", "'x' * 1000000000", "lambda: 1"):
            with self.subTest(bad), self.assertRaises((ValueError, TypeError)):
                evaluate(compile_expression(bad), {"price": 3})

    def test_group_by_totals(self) -> None:
        priced = ParseNumbers.execute(
            RemoveDuplicates.execute(ORDERS, "id").args[0], "qty, price", "1"
        ).args[0]
        dated = ComputeColumn.execute(
            ComputeColumn.execute(priced, "revenue", "qty * price").args[0],
            "month",
            "left(date, 7)",
        ).args[0]
        out = Summarize.execute(
            dated, "region, month", "revenue: sum(revenue)\norders: count()"
        ).args[0]
        self.assertEqual(
            out["rows"],
            [
                {"region": "East", "month": "2026-01", "revenue": 8200, "orders": 2},
                {"region": "West", "month": "2026-02", "revenue": 300, "orders": 1},
            ],
        )

    def test_data_checks_count_readable_rules(self) -> None:
        people = make(
            "p",
            [
                {"id": "C1", "email": "a@x.com", "phone": "1"},
                {"id": "C2", "email": "bad", "phone": ""},
                {"id": "C2", "email": "c @x.com", "phone": "3"},
                {"id": "C3", "email": "d@x.com", "phone": ""},
            ],
        )
        rules = "invalid_email: email not matches ^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$\nmissing_phone: phone is empty\nduplicate_id: id is duplicate"
        out = DataChecks.execute(people, rules).args[0]
        self.assertEqual(
            [(r["check"], r["count"]) for r in out["rows"]],
            [("invalid_email", 2), ("missing_phone", 2), ("duplicate_id", 1)],
        )
        with self.assertRaisesRegex(ValueError, "Write each check"):
            DataChecks.execute(people, "x: phone is weird")


if __name__ == "__main__":
    unittest.main()
