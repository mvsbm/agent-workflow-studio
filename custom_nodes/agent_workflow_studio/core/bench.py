"""The benchmark around contact matching: synthetic CRM contacts with an answer key, and how well a
result matches it. Uses the pack's nayose_bench generator (an export copies it beside core); real
names, towns and companies need its public sources downloaded once (nayose_bench.sources)."""

from __future__ import annotations

import json
from pathlib import Path

from .context import Context
from .steps import read_csv, write_csv


def synthetic_contacts(
    ctx: Context, profile: str, contacts: int, seed: int, key_dir: str = ""
) -> tuple:
    """Contacts, phones and addresses (CSV, on CONT_ID) in the data folder, and the answer key
    (beside them, or in `key_dir`). The same profile, size and seed give the same data."""
    from ..nayose_bench.contacts import generate_contacts

    name = f"{profile}_{contacts}_{seed}"
    data = Path(ctx.input_dir) / "contacts_benchmark" / name
    key = Path(key_dir) / name if key_dir else data.parent / f"{name}_key"
    if not (key / "stats.json").exists() or not (data / "contacts.csv").exists():
        generate_contacts(data, key, contacts, seed, profile=profile)
    stats = json.loads((key / "stats.json").read_text(encoding="utf-8"))
    ctx.facts.update({k: v for k, v in stats.items() if isinstance(v, int | float | str)})
    ctx.report(
        f"SYNTHETIC CONTACTS · {profile} · "
        + " · ".join(f"{k} {v:,}" for k, v in stats.items() if isinstance(v, int))[:200]
    )
    return f"contacts_benchmark/{name}", str(key / "answer_key_contacts.csv")


def score_contacts(ctx: Context, entities, answer_key: str) -> tuple:
    """Pairwise and B-cubed precision, recall and F1 of the entities against the answer key."""
    from ..nayose_bench.score import score

    result = Path(ctx.run_dir) / "result_for_score.csv"
    write_csv(
        result,
        (
            {
                "system": "contacts",
                "record_id": r["record"],
                "role": "contact",
                "customer_id": r["cluster"],
            }
            for r in read_csv(entities)
        ),
        ["system", "record_id", "role", "customer_id"],
    )
    report = score(Path(answer_key).parent, result)
    ctx.extra["report"] = report
    ctx.facts.update(
        {
            f"{k} {m}": report[k][m]
            for k in ("pairwise", "bcubed")
            for m in ("precision", "recall", "f1")
        }
    )
    pairwise = report["pairwise"]
    ctx.report(
        f"SCORE · F1 {pairwise['f1']} · precision {pairwise['precision']} · recall {pairwise['recall']}"
    )
    return ()
