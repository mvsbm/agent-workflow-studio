"""Contact matching as studio nodes: each is a thin adapter over one step in core/steps.py (the same
function an exported CLI calls), plus what the node shows on the canvas.

    Data folder → Read contacts → Standardize → Find candidates → Compare → Learn from your reviews
    → Decide by score → Cluster guard → Entity per contact

The benchmark nodes (Synthetic contacts, Score contacts) feed generated data with an answer key in
front and score the result at the end; they are not part of an exported pipeline.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from comfy_api.latest import io

from .core import contacts as C
from .core import steps
from .core.context import Context
from .data_preview import table
from .datasets import dataset_file, ref, ui_datasets
from .decision_rules import stage_card
from .matching_nodes import chain, own_folder, workflow_repo
from .standards import custom

Contacts = custom("contacts")
ContactRecords = custom("contact_records")
CandidatePairs = custom("candidate_pairs")
RecordPairs = custom("record_pairs")
PairDecisions = custom("pair_decisions")
RecordClusters = custom("record_clusters")
CATEGORY = "agent_workflow_studio/contacts"
HIDDEN = [io.Hidden.extra_pnginfo, io.Hidden.prompt, io.Hidden.unique_id]
TOWNS = "reference/towns.tsv"


def studio_ctx(cls, name: str) -> Context:
    """A step's context in the studio: ComfyUI's input folder, its own run folder in this
    workflow's history, your Review decisions, and the stage card it runs in."""
    import folder_paths

    repo = workflow_repo(cls)
    hidden = getattr(cls, "hidden", None)
    uid = str(getattr(hidden, "unique_id", "") or "")
    return Context(
        input_dir=Path(folder_paths.get_input_directory()),
        run_dir=own_folder(repo, name),
        labels=repo.load_labels(),
        stage=stage_card(getattr(hidden, "extra_pnginfo", None), uid) if uid else None,
    )


def shown(ctx: Context, title: str, wires: list[str], *given: tuple[int, dict]) -> dict:
    """What a node shows: its facts, one line, the amount on each output wire, its datasets."""
    line = ctx.lines[-1] if ctx.lines else title
    facts = [{"": k, "value": f"{v:,}" if isinstance(v, int) else v} for k, v in ctx.facts.items()]
    return {
        "aws_tables": [table(title, facts, 40, 140)],
        "text": (line,),
        "aws_headline": (line.split(" · ", 1)[-1][:160],),
        "aws_wires": wires,
        "aws_datasets": ui_datasets(*given),
    }


def ensure_towns(input_dir: Path, name: str) -> None:
    """The town registry the standardization parses addresses with, made once from the curated
    municipal mergers and (when downloaded) every town in Japan from Japan Post."""
    path = (input_dir / name).resolve()
    if not name or path.exists() or input_dir.resolve() not in path.parents:
        return
    from .nayose_bench.generate import PLACES
    from .nayose_bench.sources import CACHE

    towns = {loc: history[-1][1] for _, _, history, _ in PLACES for _, loc in history}
    if (CACHE / "towns.tsv").exists():
        with open(CACHE / "towns.tsv", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                towns.setdefault(r["city"] + r["town"], r["city"] + r["town"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("name\tcurrent\n")
        f.writelines(f"{k}\t{v}\n" for k, v in towns.items())


class ReadContacts(io.ComfyNode):
    EXPORT = "steps:read_contacts"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowReadContacts",
            display_name="Read contacts",
            category=CATEGORY,
            search_aliases=["contacts", "crm", "column map", "cont_id", "read exports"],
            description=(
                "Reads your contact exports through a column map: which file and column hold the key, the names "
                "(kanji, kana, romaji), birth date, gender, the person/company flag, phones and addresses. Phones "
                "and addresses may sit in their own files (joined on the key) or in columns of the contacts file. "
                "Stops with the column to map when one is missing; counts duplicate keys and unreadable dates."
            ),
            inputs=[
                io.String.Input(
                    "folder",
                    display_name="Folder",
                    default="",
                    tooltip="The data folder (connect Data folder).",
                ),
                io.String.Input(
                    "columns",
                    display_name="Column map",
                    multiline=True,
                    default=C.COLUMNS,
                    tooltip="YAML: which column holds what. Only key, birth_date and one name column are required.",
                ),
            ],
            outputs=[Contacts.Output(display_name="contacts", tooltip="One contact per key.")],
            hidden=HIDDEN,
        )

    @classmethod
    def execute(cls, folder: str, columns: str) -> io.NodeOutput:
        ctx = studio_ctx(cls, "read_contacts")
        (path,) = steps.read_contacts(ctx, folder, columns)
        out = ref(
            "contacts", "contacts", path, ctx.facts["contacts"], run_folders=[str(ctx.run_dir)]
        )
        ui = shown(ctx, "Read contacts", [f"{ctx.facts['contacts']:,} contacts"], (0, out))
        return io.NodeOutput(out, ui=ui)


class StandardizeContacts(io.ComfyNode):
    EXPORT = "steps:standardize_contacts"
    FILES = ("towns",)  # input files an export bundles

    @classmethod
    def define_schema(cls) -> io.Schema:
        fix = lambda name, label, tip: io.Boolean.Input(  # noqa: E731
            name, display_name=label, default=True, tooltip=tip
        )
        return io.Schema(
            node_id="AgentWorkflowStandardizeContacts",
            display_name="Standardize contacts",
            category=CATEGORY,
            search_aliases=[
                "standardize",
                "normalize",
                "kana",
                "itaiji",
                "address parse",
                "company name",
            ],
            description=(
                "Makes contacts comparable: kanji variants folded, kana keys (small kana, ヅ/ヂ, long vowels), "
                "phones canonical, addresses parsed to town, house and room against a town registry (renamed "
                "municipalities mapped to today's name). Each fix below earned its place in the measurements."
            ),
            inputs=[
                Contacts.Input("contacts", display_name="Contacts", tooltip="From Read contacts."),
                io.String.Input(
                    "towns",
                    display_name="Town registry",
                    default=TOWNS,
                    tooltip="TSV (name, current) in the input folder; made once if missing. Empty: no registry.",
                ),
                fix("kana_ocr", "Repair kana OCR", "八→ハ, 卜→ト, 口→ロ … in kana fields."),
                fix(
                    "readings",
                    "Readings from the data",
                    "Fill a missing kana from the reading the data uses for that kanji.",
                ),
                fix(
                    "split_one_field",
                    "Split whole names",
                    "A whole name in the surname field, split after a known surname.",
                ),
                fix(
                    "shop_alias",
                    "Shop name as surname",
                    "藤江不動産 also meets 藤江 (a sole proprietor's shop).",
                ),
                fix(
                    "company_clean",
                    "Clean company names",
                    "御中, departments and contact persons removed; legal form, branch and loanword spelling folded.",
                ),
            ],
            outputs=[
                ContactRecords.Output(display_name="records", tooltip="Standardized records.")
            ],
            hidden=HIDDEN,
        )

    @classmethod
    def execute(cls, contacts: Any, towns: str, **fixes: bool) -> io.NodeOutput:
        ctx = studio_ctx(cls, "standardize_contacts")
        ensure_towns(Path(ctx.input_dir), towns.strip())
        (path,) = steps.standardize_contacts(ctx, dataset_file(contacts), towns.strip(), **fixes)
        out = ref(
            "contact_records", "records", path, ctx.facts["records"], upstream=contacts,
            run_folders=chain(contacts, ctx.run_dir),
        )  # fmt: skip
        return io.NodeOutput(out, ui=shown(ctx, "Standardize contacts", [""], (0, out)))


class FindContactCandidates(io.ComfyNode):
    EXPORT = "steps:find_contact_candidates"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowFindContactCandidates",
            display_name="Find contact candidates",
            category=CATEGORY,
            search_aliases=["blocking", "candidates", "pairs", "record linkage"],
            description=(
                "Finds the pairs worth comparing: exact keys (birth date with each name part, the full name), a "
                "shared phone or house, birth date + town, and names alike in their 2-character pieces. A key "
                "shared by more contacts than its cap pairs no one (a family number, a common name)."
            ),
            inputs=[
                ContactRecords.Input("records", display_name="Records", tooltip="From Standardize contacts."),
                io.Boolean.Input("exact", display_name="Exact keys", default=True, tooltip="Birth date + name parts, full name alone."),
                io.Boolean.Input("shared", display_name="Shared phone or house", default=True, tooltip="Never decides alone: only makes a pair to compare."),
                io.Boolean.Input("locality", display_name="Birth date + town", default=True, tooltip="Finds contacts with no usable name."),
                io.Boolean.Input("similar", display_name="Similar names", default=True, tooltip="Typos and other readings still meet."),
                io.Int.Input("exact_cap", display_name="Largest exact group", default=50, min=2, max=10000, tooltip="Skip exact keys shared by more contacts than this."),
                io.Int.Input("shared_cap", display_name="Largest shared group", default=20, min=2, max=10000, tooltip="Skip phones, houses and towns shared by more contacts than this."),
                io.Float.Input("similarity", display_name="Name similarity", default=0.5, min=0.1, max=1.0, step=0.05, tooltip="How alike names must be (cosine of their 2-character pieces)."),
            ],
            outputs=[CandidatePairs.Output(display_name="candidates", tooltip="Pairs to compare.")],
            hidden=HIDDEN,
        )  # fmt: skip

    @classmethod
    def execute(cls, records: Any, **options: Any) -> io.NodeOutput:
        ctx = studio_ctx(cls, "find_contact_candidates")
        (path,) = steps.find_contact_candidates(ctx, dataset_file(records), **options)
        out = ref(
            "candidate_pairs", "candidates", path, ctx.facts["pairs"], upstream=records,
            run_folders=chain(records, ctx.run_dir),
        )  # fmt: skip
        ui = shown(ctx, "Find contact candidates", [f"{ctx.facts['pairs']:,} pairs"], (0, out))
        return io.NodeOutput(out, ui=ui)


class CompareContacts(io.ComfyNode):
    EXPORT = "steps:compare_contacts"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowCompareContacts",
            display_name="Compare contacts",
            category=CATEGORY,
            search_aliases=["compare", "features", "similarity", "jaro winkler"],
            description=(
                "Measures how alike the two contacts of each pair are: names in both scripts, birth date (and how "
                "many contacts share it), phones and houses (and how common they are), addresses, the company "
                "name core, the person/company flag. Writes pairs.jsonl for Learn from your reviews."
            ),
            inputs=[
                ContactRecords.Input(
                    "records", display_name="Records", tooltip="From Standardize contacts."
                ),
                CandidatePairs.Input(
                    "candidates", display_name="Candidates", tooltip="From Find contact candidates."
                ),
            ],
            outputs=[RecordPairs.Output(display_name="pairs", tooltip="Compared pairs.")],
            hidden=HIDDEN,
        )

    @classmethod
    def execute(cls, records: Any, candidates: Any) -> io.NodeOutput:
        ctx = studio_ctx(cls, "compare_contacts")
        (path,) = steps.compare_contacts(ctx, dataset_file(records), dataset_file(candidates))
        out = ref(
            "record_pairs", "pairs", path, ctx.facts["pairs compared"], upstream=candidates,
            run_folders=chain(candidates, ctx.run_dir),
        )  # fmt: skip
        return io.NodeOutput(out, ui=shown(ctx, "Compare contacts", [""], (0, out)))


class LearnFromReviews(io.ComfyNode):
    EXPORT = "steps:learn_from_reviews"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowLearnFromReviews",
            display_name="Learn from your reviews",
            category=CATEGORY,
            search_aliases=["active learning", "logistic regression", "train", "review", "model"],
            description=(
                "Scores every pair from your Review decisions: a model (logistic regression) learns from the pairs "
                "you decided, then asks you about the pairs it is least sure of, a batch at a time (active "
                "learning). Reviews a model trained without them strongly disputes come back for a second look. "
                "Before any review, pairs are ordered by their agreement patterns. About 1,000 reviews is enough."
            ),
            inputs=[
                RecordPairs.Input("pairs", display_name="Pairs", tooltip="From Compare contacts."),
                io.Int.Input("batch", display_name="Reviews per round", default=50, min=5, max=2000, tooltip="How many pairs to ask you about each run."),
                io.Boolean.Input("audit", display_name="Check my reviews", default=True, tooltip="Ask again about reviews the model strongly disputes."),
                io.String.Input("answer_key_file", display_name="Answer key (benchmark)", default="", optional=True, tooltip="Benchmark only: a reviewer who knows the answers reviews instead of you.", advanced=True),
                io.Int.Input("budget", display_name="Simulated reviews", default=1000, min=10, max=100000, optional=True, tooltip="Benchmark only: how many reviews the simulated reviewer makes.", advanced=True),
                io.Int.Input("seed", display_name="Seed", default=0, min=0, max=2**31 - 1, optional=True, control_after_generate=False, tooltip="Which random pairs the first round adds.", advanced=True),
            ],
            outputs=[
                RecordPairs.Output(display_name="scored", tooltip="Each pair with how likely it is the same."),
                io.String.Output(display_name="summary", tooltip="What the model learned from, and what to review next."),
            ],
            hidden=HIDDEN,
        )  # fmt: skip

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")  # your review decisions change between runs

    @classmethod
    def execute(
        cls,
        pairs: Any,
        batch: int,
        audit: bool,
        answer_key_file: str = "",
        budget: int = 1000,
        seed: int = 0,
    ) -> io.NodeOutput:
        ctx = studio_ctx(cls, "learn_from_reviews")
        path, summary = steps.learn_from_reviews(
            ctx, dataset_file(pairs), batch, audit, (answer_key_file or "").strip(), budget, seed
        )
        out = ref(
            "record_pairs", "scored", path, ctx.facts["pairs scored"], upstream=pairs,
            run_folders=chain(pairs, ctx.run_dir),
        )  # fmt: skip
        ui = shown(ctx, "Learn from your reviews", ["", ""], (0, out))
        ui["aws_human"] = [
            {
                "open": ctx.facts["next to review"] + ctx.facts["reviews to check again"],
                "decided": ctx.facts["reviewed: same"] + ctx.facts["reviewed: different"],
            }
        ]
        return io.NodeOutput(out, summary, ui=ui)


class DecideByScore(io.ComfyNode):
    EXPORT = "steps:decide_pairs"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowDecideByScore",
            display_name="Decide by score",
            category="agent_workflow_studio/matching",
            search_aliases=["threshold", "decide", "precision", "recall", "review queue"],
            description=(
                "Decides each scored pair: your review first; then at least Match at → match, at least Review "
                "from → to review (the queue for people), below → different. Raise Match at for fewer wrong "
                "merges, lower it for fewer missed ones. Precision first: a match also needs a shared phone, "
                "house, town or a stated alias, else it goes to review."
            ),
            inputs=[
                RecordPairs.Input("pairs", display_name="Scored pairs", tooltip="From Learn from your reviews."),
                io.Float.Input("match_at", display_name="Match at", default=0.5, min=0.01, max=1.0, step=0.01, tooltip="Pairs at least this likely to be the same are merged."),
                io.Float.Input("review_from", display_name="Review from", default=0.1, min=0.0, max=1.0, step=0.01, tooltip="Pairs from here up to Match at go to the review queue; below are different."),
                io.Boolean.Input("corroboration", display_name="Precision first", default=False, tooltip="A match also needs a second identifier beside name and birth date."),
            ],
            outputs=[
                PairDecisions.Output(display_name="decisions", tooltip="Every pair's decision: connect to Cluster guard."),
                PairDecisions.Output(display_name="queue", tooltip="The review queue: the pairs for people to decide."),
                io.String.Output(display_name="summary", tooltip="Counts per decision."),
            ],
            hidden=HIDDEN,
        )  # fmt: skip

    @classmethod
    def execute(
        cls, pairs: Any, match_at: float, review_from: float, corroboration: bool
    ) -> io.NodeOutput:
        ctx = studio_ctx(cls, "decide_by_score")
        path, queue, summary = steps.decide_pairs(
            ctx, dataset_file(pairs), match_at, review_from, corroboration
        )
        counts = ctx.extra["counts"]
        rows, waiting = sum(counts.values()), counts["review"]
        common = dict(upstream=pairs, run_folders=chain(pairs, ctx.run_dir))
        out = ref("pair_decisions", "decisions", path, rows, **common)
        out_queue = ref("pair_decisions", "review_queue", queue, waiting, **common)
        ui = shown(
            ctx,
            "Decide by score",
            [f"{rows:,} pairs", f"{waiting:,} to review", ""],
            (0, out),
            (1, out_queue),
        )
        return io.NodeOutput(out, out_queue, summary, ui=ui)


class EntityPerContact(io.ComfyNode):
    EXPORT = "steps:entity_per_contact"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowEntityPerContact",
            display_name="Entity per contact",
            category=CATEGORY,
            search_aliases=["entity id", "nayose result", "golden record", "output"],
            description=(
                "Every contact with its entity id: contacts Cluster guard grouped share their cluster's id; every "
                "other contact is its own entity. Writes entities.csv (record, cluster)."
            ),
            inputs=[
                Contacts.Input(
                    "records", display_name="Contacts", tooltip="From Read contacts (every key)."
                ),
                RecordClusters.Input(
                    "clusters", display_name="Clusters", tooltip="From Cluster guard."
                ),
            ],
            outputs=[
                RecordClusters.Output(display_name="entities", tooltip="One row per contact.")
            ],
            hidden=HIDDEN,
        )

    @classmethod
    def execute(cls, records: Any, clusters: Any) -> io.NodeOutput:
        ctx = studio_ctx(cls, "entity_per_contact")
        (path,) = steps.entity_per_contact(ctx, dataset_file(records), dataset_file(clusters))
        out = ref(
            "record_clusters", "entities", path, ctx.facts["contacts"], upstream=clusters,
            run_folders=chain(clusters, ctx.run_dir), clusters=ctx.facts["entities"],
        )  # fmt: skip
        ui = shown(ctx, "Entity per contact", [f"{ctx.facts['entities']:,} entities"], (0, out))
        return io.NodeOutput(out, ui=ui)


# --- benchmark ------------------------------------------------------------------------------------
class SyntheticContacts(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        from .nayose_bench.contacts import PROFILES

        return io.Schema(
            node_id="AgentWorkflowSyntheticContacts",
            display_name="Synthetic contacts (benchmark)",
            category=CATEGORY,
            search_aliases=["benchmark", "synthetic", "test data", "answer key"],
            description=(
                "Generates CRM-shaped contact exports (contacts, phones, addresses on CONT_ID) with realistic dirt "
                "and an answer key, so a matching workflow can be measured. Real profiles use real names, towns "
                "and companies once their sources are downloaded."
            ),
            inputs=[
                io.Combo.Input("profile", options=list(PROFILES), default="company_hard", display_name="Profile", tooltip="How dirty and how many companies."),
                io.Int.Input("contacts", display_name="Contacts", default=10000, min=100, max=2_000_000, tooltip="About how many contacts."),
                io.Int.Input("seed", display_name="Seed", default=7, min=0, max=2**31 - 1, control_after_generate=False, tooltip="The same seed gives the same data."),
            ],
            outputs=[
                io.String.Output(display_name="folder", tooltip="The data folder (for Read contacts)."),
                io.String.Output(display_name="answer key", tooltip="The answer key file (for the simulated reviewer and Score contacts)."),
            ],
        )  # fmt: skip

    @classmethod
    def execute(cls, profile: str, contacts: int, seed: int) -> io.NodeOutput:
        import folder_paths

        from .nayose_bench.contacts import generate_contacts

        name = f"{profile}_{contacts}_{seed}"
        data = Path(folder_paths.get_input_directory()) / "contacts_benchmark" / name
        key = Path(folder_paths.get_output_directory()) / "contacts_benchmark" / name
        if not (key / "stats.json").exists() or not (data / "contacts.csv").exists():
            generate_contacts(data, key, contacts, seed, profile=profile)
        stats = json.loads((key / "stats.json").read_text(encoding="utf-8"))
        line = " · ".join(f"{k} {v:,}" for k, v in stats.items() if isinstance(v, int))[:160]
        ui = {
            "aws_tables": [
                table("Synthetic contacts", [{"": k, "value": v} for k, v in stats.items()], 30)
            ],
            "text": (f"SYNTHETIC CONTACTS · {line}",),
            "aws_headline": (line,),
        }
        return io.NodeOutput(
            f"contacts_benchmark/{name}", str(key / "answer_key_contacts.csv"), ui=ui
        )


class ScoreContacts(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowScoreContacts",
            display_name="Score contacts (benchmark)",
            category=CATEGORY,
            search_aliases=["score", "f1", "precision", "recall", "benchmark"],
            description=(
                "Scores entities.csv against the answer key: pairwise and B-cubed precision, recall and F1, recall "
                "per kind of dirt, and the wrong merges. Totals only, to you: nothing flows on, so no step can "
                "tune itself to the answer key."
            ),
            inputs=[
                RecordClusters.Input(
                    "entities", display_name="Entities", tooltip="From Entity per contact."
                ),
                io.String.Input(
                    "answer_key",
                    display_name="Answer key",
                    default="",
                    tooltip="From Synthetic contacts.",
                ),
            ],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, entities: Any, answer_key: str) -> io.NodeOutput:
        from .nayose_bench.score import score

        rows = steps.read_csv(dataset_file(entities))
        truth = Path(dataset_file(entities)).parent / "result_for_score.csv"
        steps.write_csv(
            truth,
            ({"system": "contacts", "record_id": r["record"], "role": "contact", "customer_id": r["cluster"]} for r in rows),
            ["system", "record_id", "role", "customer_id"],
        )  # fmt: skip
        report = score(Path(answer_key).parent, truth)
        totals = {
            f"{k} {m}": report[k][m]
            for k in ("pairwise", "bcubed")
            for m in ("precision", "recall", "f1")
        }
        line = f"F1 {report['pairwise']['f1']} · precision {report['pairwise']['precision']} · recall {report['pairwise']['recall']}"
        tables = [
            table("Score", [{"": k, "value": v} for k, v in totals.items()]),
            table(
                "Recall by dirt",
                [{"": k, "value": v} for k, v in report["recall_by_damage"].items()],
                40,
            ),
            table(
                "Wrong merges",
                [{"": k, "value": v} for k, v in (report["false_merges"] or {"none": 0}).items()],
            ),
        ]
        return io.NodeOutput(
            ui={"aws_tables": tables, "text": (f"SCORE · {line}",), "aws_headline": (line,)}
        )


NODES = [
    ReadContacts,
    StandardizeContacts,
    FindContactCandidates,
    CompareContacts,
    LearnFromReviews,
    DecideByScore,
    EntityPerContact,
    SyntheticContacts,
    ScoreContacts,
]
