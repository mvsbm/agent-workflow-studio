"""Nayose benchmark as visible nodes: people → insurance activity → one dirty export per system → score.

Plus **Preview table**, which shows rows of any CSV/JSON file a stage or agent tool wrote. Every
node shows its data as tables inside the node (``ui.aws_tables``), so each stage can be read on
the canvas. The answer key goes under the output folder, where agent tools cannot read it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from comfy_api.latest import io

from .data_preview import counts_table, read_rows, table
from .nayose_bench import generate as bench
from .nayose_bench.score import score as score_result
from .standards import custom

# The synthetic-data domain's dataset classes (standards/datasets/synthetic.json), passed in memory.
SyntheticPeople = custom("synthetic_people")
SyntheticActivity = custom("synthetic_activity")


def benchmark_dirs(folder: str) -> tuple[Path, Path]:
    import folder_paths

    name = Path(folder.strip() or "nayose").name  # one plain folder name, never a path
    return (
        Path(folder_paths.get_input_directory()) / name,
        Path(folder_paths.get_output_directory()) / "nayose_benchmark" / name / "key",
    )


def name_of(entity: dict, when=bench.END) -> str:
    if entity["kind"] == "company":
        return entity["legal"] + entity["core"][0]
    return bench.at(entity["names"], when)[0] + " " + entity["given"][0]


class NayosePeople(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNayosePeople",
            display_name="Nayose · 1 People",
            category="agent_workflow_studio/nayose benchmark",
            description="Synthetic Japanese households with life histories: moves, marriages (surname changes), "
            "children, phones, bank accounts; plus look-alike strangers and small companies.",
            inputs=[
                io.Int.Input(
                    "households",
                    display_name="Households",
                    default=1200,
                    min=10,
                    max=20000,
                    tooltip="How many households to simulate (about 2.3 people each).",
                ),
                io.Int.Input(
                    "seed",
                    display_name="Seed",
                    default=7,
                    min=0,
                    max=2**31 - 1,
                    control_after_generate=False,
                    tooltip="Same seed, same data: change it for a different sample.",
                ),
            ],
            outputs=[
                SyntheticPeople.Output(
                    display_name="people",
                    tooltip="Simulated people and companies with their life histories.",
                )
            ],
        )

    @classmethod
    def execute(cls, households: int, seed: int) -> io.NodeOutput:
        world = bench.population(households, seed)
        people = [
            {
                "id": p["id"],
                "name now": name_of(p),
                "reading": p["names"][-1][1][1] + " " + p["given"][1],
                "born": p["dob"].isoformat(),
                "household": p["hh"],
                "surnames": " → ".join(n[0] for _, n in p["names"]),
                "addresses": len(p["addr"]),
                "accounts": len(p["accounts"]),
                "notes": ", ".join(p["tags"]),
            }
            for p in world.people
        ]
        changed = sum(len(p["names"]) > 1 for p in world.people)
        moved = sum(len(p["addr"]) > 1 for p in world.people)
        summary = {
            "people": len(world.people),
            "households": households,
            "companies": len(world.companies),
            "surname changed (marriage)": changed,
            "moved at least once": moved,
            "look-alike strangers": sum("lookalike" in p["tags"] for p in world.people) // 2,
        }
        tables = [counts_table("Population", summary), table("People", people)]
        return io.NodeOutput(world, ui={"aws_tables": tables})


class NayoseActivity(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNayoseActivity",
            display_name="Nayose · 2 Insurance activity",
            category="agent_workflow_studio/nayose benchmark",
            description="Clean facts: policies (auto, fire, medical, injury, liability) with holder, insured "
            "and beneficiary; claims by holders and family; premium and payout bank registrations. "
            "Update rate: how often a customer's change reaches a policy at all.",
            inputs=[
                SyntheticPeople.Input(
                    "people", display_name="People", tooltip="From Nayose · 1 People."
                ),
                io.Float.Input(
                    "update_rate",
                    display_name="Update rate",
                    default=0.5,
                    min=0.0,
                    max=1.0,
                    step=0.05,
                    tooltip="How often a move, marriage or new phone is reported to a policy at all (lower = more stale records).",
                ),
                io.Int.Input(
                    "seed",
                    display_name="Seed",
                    default=7,
                    min=0,
                    max=2**31 - 1,
                    control_after_generate=False,
                    tooltip="Same seed, same data: change it for a different sample.",
                ),
            ],
            outputs=[
                SyntheticActivity.Output(
                    display_name="facts", tooltip="Clean policies, claims and bank registrations."
                )
            ],
        )

    @classmethod
    def execute(cls, people: Any, update_rate: float, seed: int) -> io.NodeOutput:
        facts = bench.activity(people, seed, update_rate)
        by_id = people.by_id()
        name = lambda pid: name_of(by_id[pid]) if pid else ""  # noqa: E731
        policies = [
            {
                "policy": p["number"],
                "product": bench.PRODUCTS[p["code"]][0],
                "holder": name(p["holder"]),
                "signed": p["signed"].isoformat(),
                "channel": p["channel"],
                "insured": name(p["insured"]),
                "beneficiary": name(p["beneficiary"]) or p["placeholder"],
                "stale fields": ", ".join(
                    g for g, d in p["as_of"].items() if d < bench.END and d == p["signed"]
                ),
            }
            for p in facts["policies"]
        ]
        claims = [
            {
                "claim": c["uketsuke"],
                "received": c["received"].isoformat(),
                "channel": c["channel"],
                "claimant": name(c["claimant"]),
                "holder": name(c["holder"]),
            }
            for c in facts["claims"]
        ]
        holder_of = {p["number"]: p["holder"] for p in facts["policies"]}
        summary = {
            "policies": len(facts["policies"]),
            "claims": len(facts["claims"]),
            "bank registrations": len(facts["payments"]),
            "claims by a family member": sum(c["claimant"] != c["holder"] for c in facts["claims"]),
            "premiums paid from someone else's account": sum(
                p["kind"] == "1" and p["entity"] != holder_of[p["kanri"]] for p in facts["payments"]
            ),
        }
        tables = [
            counts_table("Activity", summary),
            table("Policies (clean)", policies),
            table("Claims (clean)", claims),
        ]
        return io.NodeOutput(facts, ui={"aws_tables": tables})


class NayoseExport(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNayoseExport",
            display_name="Nayose · 3 Legacy export",
            category="agent_workflow_studio/nayose benchmark",
            description="One legacy system's export of the same facts, damaged the way that system damages data "
            "(mainframe kana and 〓, OCR on paper forms, call-centre homophones, bank-format kana...). "
            "Writes the dirty file to ComfyUI's input folder and its answer key under output/nayose_benchmark.",
            inputs=[
                SyntheticActivity.Input(
                    "facts", display_name="Facts", tooltip="From Nayose · 2 Insurance activity."
                ),
                io.Combo.Input(
                    "system",
                    display_name="System",
                    options=list(bench.SYSTEMS),
                    tooltip="Which legacy system exports these facts, in its own format and with its own kinds of damage.",
                ),
                io.Float.Input(
                    "damage",
                    display_name="Damage",
                    default=1.0,
                    min=0.0,
                    max=3.0,
                    step=0.1,
                    tooltip="0 = clean, 1 = realistic, 3 = terrible",
                ),
                io.String.Input(
                    "folder",
                    display_name="Folder",
                    default="nayose",
                    tooltip="Folder inside ComfyUI's input directory.",
                ),
                io.Int.Input(
                    "seed",
                    display_name="Seed",
                    default=7,
                    min=0,
                    max=2**31 - 1,
                    control_after_generate=False,
                    tooltip="Same seed, same data: change it for a different sample.",
                ),
                io.String.Input(
                    "after",
                    display_name="After",
                    optional=True,
                    default="",
                    tooltip="Connect another export's folder to run after it.",
                ),
            ],
            outputs=[
                io.String.Output(
                    display_name="folder", tooltip="The input folder with the exported files."
                )
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(
        cls, facts: Any, system: str, damage: float, folder: str, seed: int, after: str = ""
    ) -> io.NodeOutput:
        data_dir, key_dir = benchmark_dirs(folder)
        rows, key = bench.export(facts, system, damage, seed)
        damage_counts = bench.write_export(system, rows, key, facts["world"], data_dir, key_dir)
        summary = {
            "rows": len(rows),
            "person mentions": len(key),
            "file": f"input/{data_dir.name}/{bench.SYSTEMS[system]}",
        }
        tables = [
            counts_table(f"{system} export", summary),
            table(bench.SYSTEMS[system], rows),
            counts_table("Damage in this export (mentions)", damage_counts),
        ]
        return io.NodeOutput(data_dir.name, ui={"aws_tables": tables})


class NayoseScore(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNayoseScore",
            display_name="Nayose · Score",
            category="agent_workflow_studio/nayose benchmark",
            description="Scores nayose_result.csv (system, record_id, role, customer_id) against the hidden answer "
            "key: pairwise and B-cubed precision/recall/F1, recall per kind of damage and per system pair, and "
            "what the wrong merges were. Shows totals only, never per-record answers, and only to you: "
            "the agent cannot see these results, so it cannot tune itself to the answer key.",
            inputs=[
                io.String.Input(
                    "folder",
                    display_name="Folder",
                    default="nayose",
                    tooltip="The benchmark folder whose answer key to use (connect an export's folder).",
                ),
                io.Dict.Input(
                    "result",
                    display_name="Result",
                    optional=True,
                    tooltip="Connect the Agent Tool that wrote nayose_result.csv.",
                ),
                io.String.Input(
                    "result_file",
                    display_name="Result file",
                    default="nayose_result.csv",
                    tooltip="File name of the result in the connected tool's run folders.",
                ),
            ],
            # No outputs: the score is for the user and never flows into an agent's tools.
            outputs=[],
            hidden=[io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")  # the result file changes between runs

    @classmethod
    def execute(cls, folder: str, result_file: str, result: Any = None) -> io.NodeOutput:
        try:  # a connected tool's run folders, or a result file path (e.g. from Write table)
            path = find_file(result_file, result)
        except ValueError:
            # Generating data comes first; scoring waits for the matcher's output.
            note = {
                "waiting": "Connect the step that writes the result (or its file path), then run again."
            }
            return io.NodeOutput(ui={"aws_tables": [counts_table("Score", note)]})
        _, key_dir = benchmark_dirs(folder)
        if not any(key_dir.glob("answer_key*.csv")):
            raise ValueError(f"No answer key for {folder!r}: run the Legacy export nodes first.")
        report = score_result(key_dir, path)
        totals = {
            f"{k} {m}": report[k][m]
            for k in ("pairwise", "bcubed")
            for m in ("precision", "recall", "f1")
        }
        totals |= {
            "customers found": report["customers"]["predicted"],
            "real customers": report["customers"]["true"],
            "mentions covered": report["covered"],
        }
        tables = [
            counts_table(f"Score · {path.parent.name}/{path.name}", totals),
            counts_table("Recall by damage (pairs that should match)", report["recall_by_damage"]),
            counts_table("Recall by systems", report["recall_by_systems"]),
            counts_table("Wrong merges", report["false_merges"] or {"none": 0}),
        ]
        line = f"F1 {report['pairwise']['f1']} · precision {report['pairwise']['precision']} · recall {report['pairwise']['recall']}"
        if lost := cls.lost(key_dir, path):
            tables.insert(1, counts_table("Where the true matches are lost", lost))
            line += "\n" + " · ".join(f"{k} {v}" for k, v in list(lost.items())[1:4])
        return io.NodeOutput(ui={"aws_tables": tables, "aws_headline": (line,)})

    @classmethod
    def lost(cls, key_dir: Path, path: Path) -> dict:
        """The funnel, from this workflow's latest compared pairs (none: no matching steps yet)."""
        import folder_paths

        from .matching_nodes import workflow_repo
        from .nayose_bench.score import funnel

        try:
            runs = Path(folder_paths.get_output_directory()) / "agent_tools" / workflow_repo(cls).id
        except ValueError:
            return {}
        found = sorted(runs.glob("compare_fields-*/pairs.jsonl"))
        if not found:
            return {}
        with open(found[-1], encoding="utf-8") as f:
            pairs = [(row["left"], row["right"]) for row in map(json.loads, f)]
        out = funnel(key_dir, path, pairs)
        c, total = out["counts"], out["counts"].get("true pairs", 0) or 1
        share = lambda n: f"{n:,} ({n / total:.0%})"  # noqa: E731
        return {
            "true same-person pairs": f"{total:,}",
            "same customer": share(c.get("same customer", 0)),
            "lost: never a candidate": share(c.get("never a candidate", 0)),
            "lost: candidate, not merged": share(c.get("candidate, not merged", 0)),
            **{
                f"never a candidate · {t}": n
                for t, n in out["never a candidate, by damage"].items()
            },
        }


def find_file(name: str, result: Any) -> Path:
    """A file named ``name`` in the newest upstream tool run folder, or a path under input/ or output/."""
    import folder_paths

    roots = [
        Path(folder_paths.get_output_directory()).resolve(),
        Path(folder_paths.get_input_directory()).resolve(),
    ]
    folders = reversed((result or {}).get("_run_folders") or []) if isinstance(result, dict) else []
    candidates = [Path(f) / name for f in folders] + [root / name for root in roots]
    hidden = (
        roots[0] / "nayose_benchmark"
    )  # answer keys: the agent must never be able to preview them
    for path in candidates:
        path = path.resolve()
        if hidden in path.parents:
            raise ValueError("Answer keys cannot be previewed; use Nayose · Score.")
        if not path.is_file() and path.parent.is_dir():
            # Saved like ComfyUI's save nodes (name_00007_.csv): the newest run's file of that name.
            numbered = sorted(path.parent.glob(f"{path.stem}_[0-9]*_{path.suffix}"))
            path = numbered[-1] if numbered else path
        if any(root in path.parents for root in roots) and path.is_file():
            return path
    raise ValueError(
        f"No {name!r} found in the connected tool's run folders or under input/ or output/."
    )


class PreviewTable(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowPreviewTable",
            display_name="Preview table",
            category="agent_workflow_studio",
            search_aliases=["table", "csv", "rows", "preview data"],
            description="Shows the first rows of a CSV, JSONL or JSON file: one an Agent Tool wrote (connect its "
            "result and name the file) or one under ComfyUI's input/output folders.",
            inputs=[
                io.String.Input(
                    "file",
                    display_name="File",
                    default="",
                    tooltip="File name in the tool's run folder, or a path under input/ or output/.",
                ),
                io.Dict.Input(
                    "data",
                    display_name="Data",
                    optional=True,
                    tooltip="Connect an Agent Tool's result.",
                ),
                io.Int.Input(
                    "rows",
                    display_name="Rows",
                    default=20,
                    min=1,
                    max=200,
                    tooltip="How many rows to show.",
                ),
            ],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")

    @classmethod
    def execute(cls, file: str, rows: int, data: Any = None) -> io.NodeOutput:
        from .datasets import dataset_file, is_ref

        # A dataset given with no file named: the dataset itself; else that file in its lineage.
        path = (
            dataset_file(data)
            if is_ref(data) and not file.strip()
            else find_file(file.strip(), data)
        )
        records = read_rows(path)
        return io.NodeOutput(
            ui={"aws_tables": [table(f"{path.name} · {len(records):,} rows", records, rows)]}
        )


NODES = [NayosePeople, NayoseActivity, NayoseExport, NayoseScore, PreviewTable]
