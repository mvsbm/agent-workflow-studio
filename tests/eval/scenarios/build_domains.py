"""Build domains.json: real-world jobs outside record matching, with dirty input data and answers
computed from the clean truth. Seeded, so the file is reproducible:

    python3 tests/eval/scenarios/build_domains.py
"""

from __future__ import annotations

import csv
import io
import json
import random
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def table(rows: list[list]) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def sales() -> dict:
    rng = random.Random(11)
    prices = {"Tea set": 3200, "Kettle": 5800, "Cups (6)": 1200}
    rows, revenue = (
        [["order_id", "date", "region", "product", "quantity", "unit_price"]],
        defaultdict(int),
    )
    for i in range(1, 61):
        month, day = rng.randint(1, 3), rng.randint(1, 28)
        region, product = rng.choice(["East", "West", "North"]), rng.choice(list(prices))
        qty = rng.randint(1, 5)
        revenue[(region, f"2026-{month:02d}")] += qty * prices[product]
        date = rng.choice([f"2026-{month:02d}-{day:02d}", f"2026/{month}/{day}"])
        price = rng.choice([str(prices[product]), f"¥{prices[product]:,}"])
        shown_qty = "" if qty == 1 and rng.random() < 0.6 else str(qty)  # blank means one
        row = [
            f"A{i:04d}",
            date,
            region if rng.random() > 0.15 else f" {region.lower()} ",
            product,
            shown_qty,
            price,
        ]
        rows.append(row)
        if rng.random() < 0.1:
            rows.append(list(row))  # an export error: the same order twice
    return {
        "id": "sales_report",
        "prompt": (
            "In the input folder sales/ there is orders.csv, an export from our shop system. It is a bit "
            "dirty: prices sometimes have a ¥ sign and thousands commas, dates come as 2026-01-05 or "
            "2026/1/5, region names sometimes have stray spaces or lowercase, a blank quantity means 1, "
            "and some orders appear twice (order_id is unique, so a repeated order is an export error). "
            "Build a workflow that cleans it and reports total revenue (quantity × unit price, in yen) per "
            "region and month. Write it as revenue_by_region_month.csv with exactly the columns region, "
            "month (YYYY-MM), revenue, and show it on the canvas. Run it."
        ),
        "input_files": {"sales/orders.csv": table(rows)},
        "expect_file": {
            "name": "revenue_by_region_month.csv",
            "rows": [
                {"region": r, "month": m, "revenue": v} for (r, m), v in sorted(revenue.items())
            ],
        },
        "timeout_minutes": 15,
    }


FEEDBACK = [
    ("F01", "The app crashes every time I open the camera screen.", "bug"),
    ("F02", "請求額が二重に引き落とされています。返金してください。", "billing"),
    ("F03", "Love the new design, it is so much faster now!", "praise"),
    ("F04", "Could you add a dark mode? My eyes hurt at night.", "feature_request"),
    ("F05", "ログインボタンを押しても何も起きません。", "bug"),
    ("F06", "I was charged for the premium plan after I cancelled it.", "billing"),
    ("F07", "サポートの方がとても親切でした。ありがとうございます！", "praise"),
    ("F08", "Please let us export reports as PDF.", "feature_request"),
    ("F09", "Sync stopped working since yesterday's update; my notes are gone.", "bug"),
    ("F10", "領収書の宛名を変更したいのですが、請求書はどこで見られますか？", "billing"),
    ("F11", "カレンダーと連携できるようにしてほしいです。", "feature_request"),
    ("F12", "Best budgeting app I have used, thank you team.", "praise"),
]


def feedback() -> dict:
    return {
        "id": "feedback_triage",
        "prompt": (
            "Our support inbox export is in the input folder feedback/messages.csv (columns id, message; "
            "Japanese and English). Build a workflow that sorts each message into exactly one category: "
            "bug, billing, praise or feature_request, so the right team gets it. Write triage.csv with "
            "the columns id, category and show the result on the canvas. Run it."
        ),
        "input_files": {
            "feedback/messages.csv": table([["id", "message"], *[[i, m] for i, m, _ in FEEDBACK]])
        },
        "expect_file": {
            "name": "triage.csv",
            "rows": [{"id": i, "category": c} for i, _, c in FEEDBACK],
            "min_match": 11,
        },
        "timeout_minutes": 15,
    }


INVOICES = {
    "inv_001.txt": (
        "INVOICE\nVendor: Sakura Office Supply Co.\nInvoice date: 2026-02-03\n\nItems\n  Paper A4 x10   4,500\n"
        "  Toner x2      18,000\nSubtotal 22,500\nTax (10%) 2,250\nTOTAL DUE: ¥24,750\n",
        {"vendor": "Sakura Office Supply Co.", "date": "2026-02-03", "total": "24750"},
    ),
    "inv_002.txt": (
        "請求書\nお客様: 当社 御中\n発行元: 株式会社みどり物流\n発行日 2026年2月14日\n"
        "配送料 一式 120,000円\n消費税 12,000円\nご請求金額合計 132,000円\n",
        {"vendor": "株式会社みどり物流", "date": "2026-02-14", "total": "132000"},
    ),
    "inv_003.txt": (
        "From: Blue Harbor Consulting LLC\nTo: our company\nDate: 03/01/2026 (MM/DD/YYYY)\n"
        "Workshop (2 days)  $0 -- billed in JPY: 300,000\nTravel 41,800\nTotal (JPY) 341,800\n",
        {"vendor": "Blue Harbor Consulting LLC", "date": "2026-03-01", "total": "341800"},
    ),
    "inv_004.txt": (
        "Rechnung / Invoice\nKitchen Parts Japan K.K.\n2026.03.20\n\nReplacement valve 3 pcs @ 6,600 = 19,800\n"
        "Shipping 1,100\nGrand total JPY 20,900 (tax included)\n",
        {"vendor": "Kitchen Parts Japan K.K.", "date": "2026-03-20", "total": "20900"},
    ),
}


def invoices() -> dict:
    return {
        "id": "invoice_extract",
        "prompt": (
            "The input folder invoices/ holds four supplier invoices as text files, each in a different "
            "layout (English and Japanese). Build a workflow that reads them and extracts, for each, the "
            "vendor (the company that issued it), the invoice date as YYYY-MM-DD and the total to pay in "
            "yen as a whole number. Write invoices.csv with the columns file, vendor, date, total and show "
            "it on the canvas. Run it."
        ),
        "input_files": {f"invoices/{name}": text for name, (text, _) in INVOICES.items()},
        "expect_file": {
            "name": "invoices.csv",
            "rows": [
                {"date": want["date"], "total": want["total"]} for _, want in INVOICES.values()
            ],
        },
        "timeout_minutes": 15,
    }


def quality() -> dict:
    rng = random.Random(5)
    rows = [["customer_id", "name", "email", "phone"]]
    for i in range(1, 31):
        rows.append(
            [
                f"C{i:03d}",
                f"Customer {i}",
                f"user{i}@example.com",
                f"03-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
            ]
        )
    for i in (3, 9, 14, 22, 27):  # invalid emails
        rows[i][2] = rng.choice(
            ["user{0}example.com", "user{0}@", "@example.com", "user {0}@example.com"]
        ).format(i)
    for i in (5, 11, 19, 25):  # missing phones
        rows[i][3] = ""
    for i in (7, 16, 28):  # three ids that appear twice
        rows.append(
            [rows[i][0], f"Duplicate of {rows[i][0]}", f"dup{i}@example.com", "03-0000-0000"]
        )
    return {
        "id": "data_quality",
        "prompt": (
            "Check the customer master in the input folder quality/customers.csv before we migrate it. "
            "Count three problems: invalid_email (not exactly one @ with text before it and a domain with "
            "a dot after it, or containing spaces), duplicate_id (customer_id values that appear more than "
            "once; count each such id once) and missing_phone (empty phone). Write quality_report.csv "
            "with exactly the columns check, count (one row per problem, named as above) and show it on "
            "the canvas. Run it."
        ),
        "input_files": {"quality/customers.csv": table(rows)},
        "expect_file": {
            "name": "quality_report.csv",
            "rows": [
                {"check": "invalid_email", "count": "5"},
                {"check": "duplicate_id", "count": "3"},
                {"check": "missing_phone", "count": "4"},
            ],
        },
        "timeout_minutes": 12,
    }


if __name__ == "__main__":
    tasks = [sales(), feedback(), invoices(), quality()]
    (HERE / "domains.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(tasks)} tasks: {', '.join(t['id'] for t in tasks)}")
