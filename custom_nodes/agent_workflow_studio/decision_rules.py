"""Decision rules: the visible, editable decision of a matching (or any triage) job.

An upstream tool compares candidate pairs and writes one row per pair with a plain *level* per
field (``reading: exact | similar | different``). This node decides each pair from a short YAML
spec the user can read and edit on the canvas:

    rules:                      # top to bottom; the first rule that applies decides
      - name: Same bank account
        when: {account: same}   # field: level, a list of levels, or "!level" (anything else)
        then: match
        skip: true              # optional: keep the rule but do not apply it
    score:                      # points per field level for pairs no rule decided
      reading: {exact: 4, similar: 2, different: -3}
    decide: {match: 9, review: 4}   # total >= match: match; >= review: review; else different

The user's decisions from the Review tab override everything. The node shows the decision flow
with counts per branch (and the change since its last run), the scorecard with counts per
level, and examples; it writes
decisions.csv (with each pair's reason) and review.json (the pairs to review, uncertain first).
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from comfy_api.latest import io

from .datasets import dataset_file, ref, subset_file, subset_ids, ui_datasets
from .table_nodes import PairDecisions, RecordPairs

DECISIONS = ("match", "review", "different")
# Outcomes in decisions.csv: a stage with rules only leaves the rest undecided for the next stage.
OUTCOMES = (*DECISIONS, "undecided")
EARLIER = "Decided by an earlier stage"
EXAMPLE = """# Checked top to bottom: the first rule that applies decides the pair.
rules:
  - name: Same bank account
    when: {account: same}
    then: match
# Points per field level for pairs no rule decided; the total decides.
score:
  reading: {exact: 4, similar: 2, different: -3}
  birth: {exact: 5, close: 3, missing: 0, conflict: -6}
decide: {match: 9, review: 4}
"""


def load_spec(text: str) -> dict:
    """Parse and check the rules; errors say what to fix in plain words."""
    try:
        spec = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"The rules are not valid YAML: {error}") from None
    if not isinstance(spec, dict) or set(spec) - {"rules", "score", "decide"}:
        raise ValueError("The rules have three parts only: rules, score, decide.")
    rules = spec.get("rules") or []
    if not isinstance(rules, list):
        raise ValueError("`rules` is a list of {name, when, then}.")
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("name"), str)
            or not isinstance(rule.get("when"), dict)
            or not rule["when"]
            or rule.get("then") not in DECISIONS
            or not isinstance(rule.get("skip", False), bool)
        ):
            raise ValueError(
                f"Each rule needs a name, a non-empty `when` and `then` (one of {', '.join(DECISIONS)}),"
                f" and optionally `skip: true`: {rule!r}"
            )
    score = spec.get("score") or {}
    if not isinstance(score, dict) or not all(
        isinstance(levels, dict) and all(isinstance(p, int | float) for p in levels.values())
        for levels in score.values()
    ):
        raise ValueError("`score` maps each field to {level: points}.")
    decide = spec.get("decide") or {}
    if score and not (
        isinstance(decide, dict)
        and all(isinstance(decide.get(k), int | float) for k in ("match", "review"))
        and decide["review"] <= decide["match"]
    ):
        raise ValueError("`decide` needs numbers `match` and `review`, with review <= match.")
    return {"rules": rules, "score": score, "decide": decide}


def _holds(value: Any, wanted: Any) -> bool:
    if isinstance(wanted, list):
        return any(_holds(value, w) for w in wanted)
    wanted = str(wanted)
    if wanted.startswith("!"):
        return str(value) != wanted[1:]
    return str(value) == wanted


def decide(row: dict, spec: dict, label: str | None = None) -> dict:
    """{decision, branch, score, why} for one pair."""
    rule = next(
        (
            r
            for r in spec["rules"]
            if not r.get("skip")
            and all(_holds(row.get(field), wanted) for field, wanted in r["when"].items())
        ),
        None,
    )
    # The user's decision overrides, except in a rules-only stage no rule of which settles the
    # pair: it goes on, so the stage that does decide it shows how its branch agreed with you.
    if label in ("same", "different") and (rule or spec["score"]):
        return {
            "decision": "match" if label == "same" else "different",
            "branch": "Your review",
            "score": None,
            "why": f"you decided: {label}",
        }
    if rule:
        why = ", ".join(f"{f}={row.get(f)}" for f in rule["when"])
        return {"decision": rule["then"], "branch": rule["name"], "score": None, "why": why}
    if not spec["score"]:
        # Rules only (e.g. deterministic matching): what no rule settles goes on to the next stage.
        return {
            "decision": "undecided",
            "branch": "No rule applied: passed on",
            "score": None,
            "why": "",
        }
    parts = [
        (field, row.get(field), levels.get(str(row.get(field)), 0))
        for field, levels in spec["score"].items()
    ]
    total = sum(points for *_, points in parts)
    match, review = spec["decide"]["match"], spec["decide"]["review"]
    decision = "match" if total >= match else "review" if total >= review else "different"
    branch = {
        "match": f"Score ≥ {match:g}",
        "review": f"Score {review:g} to {match:g}",
        "different": f"Score < {review:g}",
    }[decision]
    why = (
        " ".join(f"{points:+g} {field} {level}" for field, level, points in parts if points)
        + f" = {total:g}"
    )
    return {"decision": decision, "branch": branch, "score": total, "why": why}


def read_pairs(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return list(csv.DictReader(text.splitlines()))


def pair_id(row: dict, index: int) -> str:
    return str(
        row.get("id")
        or (f"{row['left']}|{row['right']}" if "left" in row and "right" in row else index)
    )


def stage_card(pnginfo: Any, uid: str) -> dict | None:
    """The stage card a node runs inside: its step id and title, from the workflow the run carries
    (the node's id is "<stage>:<node>"); None for a node at the top level."""
    workflow = (pnginfo or {}).get("workflow") if isinstance(pnginfo, dict) else None
    top = uid.split(":")[0] if ":" in uid else None
    for node in (workflow or {}).get("nodes") or [] if top else []:
        if str(node.get("id")) == top and (node.get("properties") or {}).get("aws_stage"):
            # A stage card's title is its subgraph's name unless renamed (then it is saved on the node).
            name = next(
                (
                    d.get("name")
                    for d in (workflow.get("definitions") or {}).get("subgraphs", [])
                    if d.get("id") == node.get("type")
                ),
                "",
            )
            return {"id": node["properties"]["aws_stage"], "title": node.get("title") or name}
    return None


def run_rules(
    rows: list[dict], spec: dict, labels: dict, earlier: dict | None = None
) -> tuple[list[dict], dict]:
    """Decide every pair. Pairs an earlier stage already decided (``earlier``: id -> decisions.csv
    row) keep that decision; only the ones it left undecided are judged here."""
    earlier = earlier or {}
    decided, branches, levels, examples = [], Counter(), defaultdict(Counter), defaultdict(list)
    # For pairs the user decided: what the rules alone said, so each branch's mistakes show.
    agreement = defaultdict(Counter)
    for index, row in enumerate(rows):
        pid = pair_id(row, index)
        before = earlier.get(pid)
        if (
            before
            and before["decision"] != "undecided"
            and labels.get(pid) not in ("same", "different")
        ):
            carried = {
                "decision": before["decision"],
                "branch": before["branch"],
                "score": before["score"] or None,
                "why": before["why"],
            }
            decided.append(
                {"id": pid, "left": row.get("left", ""), "right": row.get("right", ""), **carried}
            )
            branches[(EARLIER, before["decision"])] += 1
            continue
        result = decide(row, spec, labels.get(pid))
        if labels.get(pid) in ("same", "different"):
            ruled = decide(row, spec)
            wanted = "match" if labels[pid] == "same" else "different"
            outcome = (
                "agree"
                if ruled["decision"] == wanted
                else "undecided"
                if ruled["decision"] in ("review", "undecided")
                else "disagree"
            )
            agreement[ruled["branch"]][outcome] += 1
        decided.append(
            {"id": pid, "left": row.get("left", ""), "right": row.get("right", ""), **result}
        )
        branches[(result["branch"], result["decision"])] += 1
        if len(examples[result["branch"]]) < 4:
            examples[result["branch"]].append(
                {
                    "branch": result["branch"],
                    "pair": pid,
                    "why": result["why"],
                    "a": row.get("a"),
                    "b": row.get("b"),
                }
            )
        for field in spec["score"]:
            levels[field][str(row.get(field))] += 1
    order = [EARLIER, "Your review", *(r["name"] for r in spec["rules"])]
    flow = sorted(
        branches.items(),
        key=lambda kv: (
            order.index(kv[0][0]) if kv[0][0] in order else len(order),
            OUTCOMES.index(kv[0][1]),
        ),
    )
    return decided, {"flow": flow, "levels": levels, "examples": examples, "agreement": agreement}


SPOT_CHECK = 3  # automatic decisions per branch sent to review, so each branch's precision shows


def spot_check_sample(decided: list[dict], per_branch: int = SPOT_CHECK) -> list[dict]:
    """A fixed sample of each branch's automatic decisions (the same pairs every run)."""
    by_branch = defaultdict(list)
    for d in decided:
        if d["decision"] in ("match", "different") and d["branch"] != "Your review":
            by_branch[d["branch"]].append(d)
    # Spread over the branch by a hash of the id: reproducible, and not just the first rows.
    return [
        d
        for items in by_branch.values()
        for d in sorted(items, key=lambda d: hashlib.sha256(d["id"].encode()).hexdigest())[
            :per_branch
        ]
    ]


def at_least(agree: int, reviewed: int) -> float:
    """Wilson 95% lower bound of agree / reviewed: what the reviews show, at least."""
    if not reviewed:
        return 0.0
    p, z = agree / reviewed, 1.96
    centre = p + z * z / (2 * reviewed)
    spread = z * ((p * (1 - p) + z * z / (4 * reviewed)) / reviewed) ** 0.5
    return (centre - spread) / (1 + z * z / reviewed)


class DecisionRules(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowDecisionRules",
            display_name="Decision rules",
            category="agent_workflow_studio",
            search_aliases=["decide", "rules", "scorecard", "match", "triage", "decision tree"],
            description=(
                "Decides each compared pair with rules and a points scorecard you can read and edit (YAML). "
                "Shows the decision flow with counts, writes decisions.csv with every pair's reason, and sends "
                "uncertain pairs to the Review tab; your Review decisions override the rules."
            ),
            inputs=[
                io.MultiType.Input(
                    "data",
                    types=[RecordPairs, io.Dict],
                    display_name="Pairs",
                    optional=True,
                    tooltip="Connect the Agent Tool that wrote the compared pairs (or give a path under output/).",
                ),
                io.String.Input(
                    "pairs_file",
                    display_name="Pairs file",
                    default="pairs.jsonl",
                    tooltip="JSONL or CSV in that tool's run folder: id (or left/right) plus one level per field.",
                ),
                io.String.Input(
                    "rules",
                    display_name="Rules",
                    multiline=True,
                    default=EXAMPLE,
                    tooltip="rules (first that applies decides), score (points per level), decide (thresholds).",
                ),
                io.Int.Input(
                    "review_limit",
                    display_name="Review limit",
                    default=2000,
                    min=0,
                    max=20000,
                    tooltip="At most this many uncertain pairs go to the Review tab.",
                ),
            ],
            outputs=[
                PairDecisions.Output(
                    display_name="decisions",
                    tooltip="Counts, plus run folders so the next tool can read decisions.csv.",
                ),
                io.String.Output(display_name="summary", tooltip="The decision flow as text."),
                # The branches, so the graph shows where pairs go: decided ones leave the cascade.
                PairDecisions.Output(
                    display_name="matched",
                    tooltip="Pairs decided to be the same: connect to Cluster guard.",
                ),
                PairDecisions.Output(
                    display_name="different",
                    tooltip="Pairs decided to be different: connect to Cluster guard.",
                ),
                RecordPairs.Output(
                    display_name="unsettled",
                    tooltip="Pairs not decided here (undecided or to review): connect to the next stage.",
                ),
            ],
            hidden=[io.Hidden.extra_pnginfo, io.Hidden.prompt, io.Hidden.unique_id],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")  # review decisions change between runs

    @classmethod
    def execute(
        cls, pairs_file: str, rules: str, review_limit: int, data: Any = None
    ) -> io.NodeOutput:
        import folder_paths

        from .nayose_nodes import find_file, table
        from .project_repo import ProjectRepo, projects_root

        spec = load_spec(rules)
        workflow = (getattr(getattr(cls, "hidden", None), "extra_pnginfo", None) or {}).get(
            "workflow"
        )
        if not isinstance(workflow, dict) or not isinstance(workflow.get("id"), str):
            raise ValueError(
                "Decision rules run only inside a saved workflow with a project history."
            )
        repo = ProjectRepo(projects_root(), workflow["id"])
        # The pairs it is given (a record_pairs dataset: exactly that file, and its subset if a
        # branch); a value from before dataset references names its file in its run folders.
        path = dataset_file(data, pairs_file.strip())
        rows = read_pairs(path)
        subset = subset_file(data)
        if subset:  # fed an earlier stage's unsettled branch: judge only those pairs
            wanted = subset_ids(data)
            rows = [row for i, row in enumerate(rows) if pair_id(row, i) in wanted]
        try:  # an earlier Decision rules stage upstream: keep what it decided, judge the rest
            earlier = (
                {}
                if subset
                else (
                    {
                        r["id"]: r
                        for r in csv.DictReader(
                            find_file("decisions.csv", data)
                            .read_text(encoding="utf-8")
                            .splitlines()
                        )
                    }
                    if data
                    else {}
                )
            )
        except ValueError:
            earlier = {}
        decided, seen = run_rules(rows, spec, repo.load_labels(), earlier)

        hidden = getattr(cls, "hidden", None)
        uid = str(getattr(hidden, "unique_id", ""))
        title = ((getattr(hidden, "prompt", None) or {}).get(uid) or {}).get("_meta", {}).get(
            "title"
        ) or "Decision rules"
        card = stage_card(getattr(hidden, "extra_pnginfo", None), uid)
        # Decisions are named by the stage card they come from (what the user sees on the canvas).
        stage = card["title"] if card else title
        runs = Path(folder_paths.get_output_directory()) / "agent_tools" / repo.id
        # The node id keeps two stages finishing in the same second apart.
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}{int(time.time() * 1000) % 1000:03d}"
        folder = runs / f"decision_rules-{stamp}-{uid.replace(':', '_')}"
        folder.mkdir(parents=True, exist_ok=True)
        # Monitoring: pairs per branch now and on this stage's previous run.
        per_branch = Counter()
        for (branch, _), n in seen["flow"]:
            per_branch[branch] += n
        previous = {}
        for old in sorted(runs.glob("decision_rules-*/rule_counts.json"), reverse=True):
            record = json.loads(old.read_text(encoding="utf-8"))
            if old.parent != folder and record.get("stage") == stage:
                previous = record["counts"]
                break
        (folder / "rule_counts.json").write_text(
            json.dumps(
                {"stage": stage, "total": len(rows), "counts": per_branch}, ensure_ascii=False
            ),
            encoding="utf-8",
        )

        def change(branch: str) -> str:
            if not previous:
                return ""
            now, before = per_branch.get(branch, 0), previous.get(branch, 0)
            shift = abs(now - before) > max(20, 0.2 * before)  # worth a look: >20% and >20 pairs
            return f"{before:,} → {now:,}" + (" ⚠" if shift else "")

        def reviews(branch: str) -> str:
            c = seen["agreement"].get(branch)
            return (
                f"{c['agree']} agree, {c['disagree']} disagree"
                + (f", {c['undecided']} sent to review" if c["undecided"] else "")
                if c
                else ""
            )

        with open(folder / "decisions.csv", "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file, ["id", "left", "right", "decision", "branch", "score", "why"]
            )
            writer.writeheader()
            writer.writerows(decided)
        branches = {
            "matched": ("match",),
            "different": ("different",),
            "unsettled": ("undecided", "review"),
        }
        for name, outcomes in branches.items():
            with open(folder / f"{name}.csv", "w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(
                    file, ["id", "left", "right", "decision", "branch", "score", "why"]
                )
                writer.writeheader()
                writer.writerows(d for d in decided if d["decision"] in outcomes)
        by_id = {pair_id(row, i): row for i, row in enumerate(rows)}
        reranked = any(by_id[d["id"]].get("rerank_score") is not None for d in decided)
        uncertain = sorted(
            (d for d in decided if d["decision"] == "review"),
            # Reranked pairs: likeliest matches first (most of your yes-clicks early); else the closest calls.
            key=lambda d: (
                -(by_id[d["id"]].get("rerank_score") or 0)
                if reranked
                else abs(
                    (d["score"] or 0)
                    - (spec["decide"].get("match", 0) + spec["decide"].get("review", 0)) / 2
                )
            ),
        )
        # Only this stage's own decisions: an earlier stage spot-checks its own.
        sample = spot_check_sample([d for d in decided if d["id"] not in earlier])
        review = {
            "stage": {**(card or {"title": stage}), "node": title},
            "title": f"Decision rules · {len(uncertain):,} pairs to review"
            + f" · {len(sample)} automatic decisions to spot-check",
            "pairs": [
                {
                    "id": d["id"],
                    "a": by_id[d["id"]].get("a") or {"mention": d["left"]},
                    "b": by_id[d["id"]].get("b") or {"mention": d["right"]},
                    "score": round(float(d["score"]), 2) if d["score"] not in (None, "") else None,
                    "branch": d["branch"],  # the rule (or score band) that left it to you
                    "reason": d["why"]
                    + (
                        f" · reranker {by_id[d['id']]['rerank_score']:.2f}"
                        if by_id[d["id"]].get("rerank_score") is not None
                        else ""
                    )
                    if d["decision"] == "review"
                    else f"Spot check · {d['branch']} decided {d['decision']}: {d['why']}",
                }
                for d in [
                    *sample,
                    *uncertain[:review_limit],
                ]  # the few spot checks first, or nobody reaches them
            ],
        }
        (folder / "review.json").write_text(
            json.dumps(review, ensure_ascii=False), encoding="utf-8"
        )

        counts = Counter(d["decision"] for d in decided)
        # What each branch means, shown under it: a rule's condition, or the scorecard.
        when = {
            r["name"]: "when " + ", ".join(f"{f} = {v}" for f, v in r["when"].items())
            for r in spec["rules"]
        }
        flow = [
            {
                "label": branch,
                "decision": decision,
                "count": n,
                "detail": when.get(branch)
                or (
                    "the sum of the points in the scorecard below"
                    if branch.startswith("Score")
                    else "your decisions in the Review tab"
                    if branch == "Your review"
                    else "kept as the previous stage decided them"
                    if branch == EARLIER
                    else ""
                ),
            }
            for (branch, decision), n in seen["flow"]
        ]
        scorecard = [
            {
                "field": field,
                "level": level,
                "points": spec["score"][field].get(level, 0),
                "pairs": n,
                "note": ""
                if level in spec["score"][field]
                else "level not in the scorecard: 0 points",
            }
            for field, levels in seen["levels"].items()
            for level, n in levels.most_common()
        ]
        examples = [e for branch in seen["examples"].values() for e in branch]
        agreement = [
            {
                "branch": branch,
                "reviewed": sum(c.values()),
                "agree": c["agree"],
                "disagree": c["disagree"],
                "rules undecided": c["undecided"],
                "right at least": f"{at_least(c['agree'], c['agree'] + c['disagree']):.0%}"
                if c["agree"] + c["disagree"]
                else "—",
            }
            for branch, c in seen["agreement"].items()
        ]

        # Full example pairs per branch, so the agent (and the user) can judge each branch.
        def compact(record: dict | None) -> str:
            values = (str(v) for v in (record or {}).values() if v not in (None, ""))
            return " · ".join(values)[:160]

        spot_check = [
            f"[{e['branch']}] {e['pair']}: A {compact(e['a'])} | B {compact(e['b'])} | {e['why']}"
            for e in examples
        ]
        summary = "\n".join(
            [f"DECISION RULES · {len(rows):,} pairs"]
            + [f"{f['label']}: {f['decision']} {f['count']:,}" for f in flow]
            + [", ".join(f"{d} {counts[d]:,}" for d in OUTCOMES if counts[d])]
            + (
                ["SINCE THE LAST RUN (pairs per branch):"]
                + [
                    f"{b}: {change(b)}"
                    for b in dict.fromkeys([*previous, *per_branch])
                    if change(b)
                ]
                if previous
                else []
            )
            + (
                ["YOUR REVIEWS VS THE RULES (per branch):"]
                + [
                    f"{a['branch']}: {a['agree']} agree, {a['disagree']} disagree, {a['rules undecided']} undecided, right at least {a['right at least']} (95%)"
                    for a in agreement
                ]
                if agreement
                else ["No review decisions yet."]
            )
            + [
                "SPOT CHECK (judge these yourself; a branch with wrong examples is the rule to fix):",
                *spot_check,
            ]
        )
        ui = {
            "aws_flow": [{"total": len(rows), "branches": flow}],
            # One per output: decisions, summary, matched, different, unsettled.
            "aws_wires": [
                f"{len(rows):,} pairs",
                "",
                f"{counts['match']:,} matched",
                f"{counts['different']:,} different",
                f"{counts['undecided'] + counts['review']:,} unsettled",
            ],
            # Zoomed out the node reads as its decision table: totals, then each rule with its count.
            "aws_headline": (
                "\n".join(
                    [
                        " · ".join(
                            f"{n:,} {label}"
                            for label, n in (
                                ("matched", counts["match"]),
                                ("different", counts["different"]),
                                ("to review", counts["review"]),
                                ("passed on", counts["undecided"]),
                            )
                            if n
                        )
                    ]
                    + [
                        f"{f['label']} → {f['decision']} {f['count']:,}"
                        for f in flow
                        if f["label"] not in (EARLIER,)
                    ][:8]
                    # What your spot checks show about each automatic branch.
                    + [
                        f"{a['branch']}: right at least {a['right at least']} ({a['agree']} of {a['agree'] + a['disagree']} you checked)"
                        for a in agreement
                        if a["agree"] + a["disagree"]
                    ]
                ),
            ),
            "aws_tables": [
                # The rules actually used: with Learn weights connected, the Rules box shows stale text.
                table(
                    "Rules in effect (first that applies decides)",
                    [
                        *(
                            {
                                "#": i + 1,
                                "rule": r["name"],
                                "when": ", ".join(f"{f} = {v}" for f, v in r["when"].items()),
                                "then": "skipped" if r.get("skip") else r["then"],
                                "pairs": ""
                                if r.get("skip")
                                else f"{per_branch.get(r['name'], 0):,}",
                                "last run → now": "" if r.get("skip") else change(r["name"]),
                                "your reviews": reviews(r["name"]),
                            }
                            for i, r in enumerate(spec["rules"])
                        ),
                        *(
                            [
                                {
                                    "#": "",
                                    "rule": "Scorecard",
                                    "when": f"total ≥ {spec['decide']['match']:g}",
                                    "then": "match",
                                    "pairs": f"{per_branch.get(f'Score ≥ {spec["decide"]["match"]:g}', 0):,}",
                                    "last run → now": change(
                                        f"Score ≥ {spec['decide']['match']:g}"
                                    ),
                                    "your reviews": reviews(f"Score ≥ {spec['decide']['match']:g}"),
                                },
                                {
                                    "#": "",
                                    "rule": "Scorecard",
                                    "when": f"total ≥ {spec['decide']['review']:g}",
                                    "then": "review",
                                    "pairs": f"{per_branch.get(f'Score {spec["decide"]["review"]:g} to {spec["decide"]["match"]:g}', 0):,}",
                                    "last run → now": change(
                                        f"Score {spec['decide']['review']:g} to {spec['decide']['match']:g}"
                                    ),
                                    "your reviews": reviews(
                                        f"Score {spec['decide']['review']:g} to {spec['decide']['match']:g}"
                                    ),
                                },
                                {
                                    "#": "",
                                    "rule": "Scorecard",
                                    "when": "otherwise",
                                    "then": "different",
                                    "pairs": f"{per_branch.get(f'Score < {spec["decide"]["review"]:g}', 0):,}",
                                    "last run → now": change(
                                        f"Score < {spec['decide']['review']:g}"
                                    ),
                                    "your reviews": reviews(
                                        f"Score < {spec['decide']['review']:g}"
                                    ),
                                },
                            ]
                            if spec["score"]
                            else []
                        ),
                    ],
                    40,
                ),
                *(
                    [table("Your reviews vs the rules, per branch", agreement, 40)]
                    if agreement
                    else []
                ),
                table("Scorecard: pairs per field level", scorecard, 60),
                table("Examples per branch", examples, 40),
            ],
            "text": (summary,),
            # Shown in the Rules box when the rules come in over a link (e.g. from Learn weights).
            "aws_rules": (rules,),
        }
        # Datasets by reference: every decision (pair_decisions), and each branch. A decided branch
        # is its own file; the unsettled branch is the pairs it was given, limited to those left.
        chain = [*((data or {}).get("_run_folders") or []), str(folder.resolve())]
        result = ref(
            "pair_decisions",
            "decisions",
            folder / "decisions.csv",
            len(decided),
            upstream=data,
            run_folders=chain,
            **{d: counts[d] for d in OUTCOMES},
        )
        outs = {
            name: ref(
                "record_pairs" if name == "unsettled" else "pair_decisions",
                name,
                path if name == "unsettled" else folder / f"{name}.csv",
                sum(d["decision"] in o for d in decided),
                subset=folder / "unsettled.csv" if name == "unsettled" else None,
                upstream=data,
                run_folders=chain,
                stage=stage,
            )
            for name, o in branches.items()
        }
        ui["aws_datasets"] = ui_datasets(
            (0, result), (2, outs["matched"]), (3, outs["different"]), (4, outs["unsettled"])
        )
        return io.NodeOutput(
            result, summary, outs["matched"], outs["different"], outs["unsettled"], ui=ui
        )


__all__ = ["DecisionRules", "decide", "load_spec", "run_rules"]
