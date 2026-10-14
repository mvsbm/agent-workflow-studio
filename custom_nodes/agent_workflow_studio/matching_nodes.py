"""The matching cascade's visible stages around Decision rules.

    compare tool → Learn weights → Decision rules → LLM suggestions → (Review tab) → Cluster guard

* **Learn weights** estimates each field level's points from the compared pairs themselves
  (Fellegi–Sunter with EM; the user's review decisions are used as fixed answers) and writes
  them into the Decision rules YAML, with thresholds from target probabilities.
* **LLM suggestions** asks a *local* model about the pairs in the review band only, and puts its
  suggestion and reason next to each pair in the Review tab. It never decides.
* **Cluster guard** builds customers from the match decisions, strongest first, and refuses a
  merge that would put together two records already decided to be different people (or exceed
  a size cap). It shows the whole decision tree: how every pair flowed through the stages.

All three read and write files in this workflow's run folders, like Agent Tools, so they chain
with them through ``data`` (``_run_folders``).
"""

from __future__ import annotations

import csv
import json
import math
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from comfy_api.latest import io

from .data_preview import table
from .decision_rules import load_spec, pair_id, read_pairs

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


# --- shared ---------------------------------------------------------------------------------
def workflow_repo(cls):
    from .project_repo import ProjectRepo, projects_root

    workflow = (getattr(getattr(cls, "hidden", None), "extra_pnginfo", None) or {}).get("workflow")
    if not isinstance(workflow, dict) or not isinstance(workflow.get("id"), str):
        raise ValueError("This node runs only inside a saved workflow with a project history.")
    return ProjectRepo(projects_root(), workflow["id"])


def own_folder(repo, name: str) -> Path:
    import folder_paths

    folder = (
        Path(folder_paths.get_output_directory())
        / "agent_tools"
        / repo.id
        / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def upstream_file(name: str, data: Any) -> Path:
    from .nayose_nodes import find_file

    return find_file(name, data)


def chain(data: Any, folder: Path) -> list[str]:
    return [*((data or {}).get("_run_folders") or []), str(folder.resolve())]


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# --- Learn weights --------------------------------------------------------------------------
# Points are capped: a level almost never seen among matches would otherwise become a near-infinite
# veto, and in dirty data (maiden names, typos) no single field is proof of a different person.
MAX_POINTS = 6.0
# No value to compare is no evidence either way: 0 points, never learned (Fellegi–Sunter null level).
NO_EVIDENCE = {"missing", "n/a", "None", ""}


def learn_weights(
    rows: list[dict], fields: list[str], labels: dict, start: dict, iterations: int = 500
) -> dict:
    """Fellegi–Sunter EM over comparison patterns. Returns m, u, λ and points (log2 m/u).

    Pairs the user reviewed keep their known answer (1 or 0) throughout. Starting probabilities
    come from the current points, so the first guess is the existing scorecard.
    """
    patterns, items = Counter(), []
    for index, row in enumerate(rows):
        key = tuple(str(row.get(f)) for f in fields)
        label = labels.get(pair_id(row, index))
        if label in ("same", "different"):
            items.append((key, 1, 1.0 if label == "same" else 0.0))
        else:
            patterns[key] += 1
    items += [(key, n, None) for key, n in patterns.items()]
    if not items:
        raise ValueError("No pairs to learn from.")

    def start_p(key: tuple) -> float:
        total = sum(start.get(f, {}).get(level, 0) for f, level in zip(fields, key, strict=True))
        return 1 / (1 + math.exp(-total / 2))

    p = {key: start_p(key) for key in patterns}
    levels = {
        f: sorted({key[i] for key, _, _ in items} - NO_EVIDENCE) for i, f in enumerate(fields)
    }
    m = u = {}
    lam, converged, rounds, smooth = 0.0, False, 0, 0.5  # a half-pair prior count per level
    while rounds < iterations:
        rounds += 1
        # M-step: how common each level is among matches (m) and among non-matches (u).
        weight_m, weight_u, total_m, total_u = defaultdict(float), defaultdict(float), 0.0, 0.0
        for key, n, fixed in items:
            pm = p[key] if fixed is None else fixed
            for f, level in zip(fields, key, strict=True):
                if level not in NO_EVIDENCE:
                    weight_m[f, level] += n * pm
                    weight_u[f, level] += n * (1 - pm)
                    weight_m[f] += n * pm
                    weight_u[f] += n * (1 - pm)
            total_m += n * pm
            total_u += n * (1 - pm)
        m = {
            f: {
                lv: (weight_m[f, lv] + smooth) / (weight_m[f] + smooth * len(levels[f]))
                for lv in levels[f]
            }
            for f in fields
        }
        u = {
            f: {
                lv: (weight_u[f, lv] + smooth) / (weight_u[f] + smooth * len(levels[f]))
                for lv in levels[f]
            }
            for f in fields
        }
        new_lam = min(max(total_m / max(total_m + total_u, 1e-9), 1e-6), 1 - 1e-6)
        # E-step: each unreviewed pattern's match probability under the new estimates.
        prior, change = math.log2(new_lam / (1 - new_lam)), 0.0
        for key in patterns:
            score = prior + sum(
                math.log2(m[f][lv] / u[f][lv])
                for f, lv in zip(fields, key, strict=True)
                if lv not in NO_EVIDENCE
            )
            value = 1 / (1 + 2 ** (-score))
            change, p[key] = max(change, abs(value - p[key])), value
        done = abs(new_lam - lam) < 1e-7 and change < 1e-5
        lam = new_lam
        if done:
            converged = True
            break
    raw = {f: {lv: math.log2(m[f][lv] / u[f][lv]) for lv in levels[f]} for f in fields}
    present = {f: {key[i] for key, _, _ in items} & NO_EVIDENCE for i, f in enumerate(fields)}
    points = {
        f: {
            **{lv: round(max(-MAX_POINTS, min(MAX_POINTS, w)), 1) for lv, w in raw[f].items()},
            **dict.fromkeys(sorted(present[f]), 0.0),
        }
        for f in fields
    }
    return {
        "m": m,
        "u": u,
        "lambda": lam,
        "points": points,
        "rounds": rounds,
        "converged": converged,
        "patterns": len(patterns),
        "reviewed": sum(1 for _, _, fixed in items if fixed is not None),
        "capped": [(f, lv) for f in fields for lv, w in raw[f].items() if abs(w) > MAX_POINTS],
    }


def thresholds(lam: float, match_probability: float, review_probability: float) -> dict:
    """Score thresholds (sum of points) at which the posterior reaches the target probabilities."""
    logit = lambda q: math.log2(q / (1 - q))  # noqa: E731
    prior = logit(lam)
    return {
        "match": round(logit(match_probability) - prior, 1),
        "review": round(logit(review_probability) - prior, 1),
    }


class LearnWeights(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowLearnWeights",
            display_name="Learn weights",
            category="agent_workflow_studio/matching",
            search_aliases=[
                "fellegi sunter",
                "em",
                "splink",
                "weights",
                "scorecard",
                "probabilistic",
            ],
            description=(
                "Learns each field level's points from the compared pairs (Fellegi–Sunter with EM; your Review "
                "decisions count as known answers) and writes them into the Decision rules YAML, with match and "
                "review thresholds set from target probabilities. Shows the learned weights next to the old ones."
            ),
            inputs=[
                io.Dict.Input(
                    "data",
                    display_name="Pairs",
                    optional=True,
                    tooltip="Connect the tool that wrote the compared pairs.",
                ),
                io.String.Input(
                    "pairs_file",
                    display_name="Pairs file",
                    default="pairs.jsonl",
                    tooltip="One line per pair with a level per field.",
                ),
                io.String.Input(
                    "rules",
                    display_name="Starting rules",
                    multiline=True,
                    default="",
                    tooltip="Decision rules YAML: hard rules are kept, its score fields are re-learned and its points are the starting guess.",
                ),
                io.Float.Input(
                    "match_probability",
                    display_name="Match at",
                    default=0.95,
                    min=0.5,
                    max=0.9999,
                    step=0.01,
                    tooltip="Pairs at least this likely to be the same go to match.",
                ),
                io.Float.Input(
                    "review_probability",
                    display_name="Review from",
                    default=0.3,
                    min=0.01,
                    max=0.99,
                    step=0.01,
                    tooltip="Pairs between this and Match at go to review.",
                ),
            ],
            outputs=[
                io.String.Output(
                    display_name="rules",
                    tooltip="The YAML with learned points; connect to Decision rules' Rules.",
                ),
                io.Dict.Output(
                    display_name="data",
                    tooltip="Passes the pairs on (run folders) to Decision rules.",
                ),
            ],
            hidden=[io.Hidden.extra_pnginfo],
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")  # review decisions change between runs

    @classmethod
    def execute(
        cls,
        pairs_file: str,
        rules: str,
        match_probability: float,
        review_probability: float,
        data: Any = None,
    ) -> io.NodeOutput:
        if review_probability >= match_probability:
            raise ValueError("Review from must be lower than Match at.")
        spec = load_spec(rules)
        if not spec["score"]:
            raise ValueError(
                "Starting rules need a `score` section naming the fields to learn (any starting points)."
            )
        repo = workflow_repo(cls)
        rows = read_pairs(upstream_file(pairs_file.strip(), data))
        fields = list(spec["score"])
        result = learn_weights(rows, fields, repo.load_labels(), spec["score"])
        learned = {
            "rules": spec["rules"],
            "score": result["points"],
            "decide": thresholds(result["lambda"], match_probability, review_probability),
        }
        text = (
            f"# Learned by Learn weights from {len(rows):,} pairs ({result['reviewed']} reviewed): "
            f"{result['lambda']:.1%} of candidate pairs look like matches.\n"
            + yaml.safe_dump(learned, allow_unicode=True, sort_keys=False, width=120)
        )
        weights = [
            {
                "field": f,
                "level": lv,
                "points": result["points"][f][lv],
                "note": "no value: no evidence"
                if lv not in result["m"][f]
                else f"capped at ±{MAX_POINTS:g}"
                if (f, lv) in result["capped"]
                else "",
                "was": spec["score"][f].get(lv, "—"),
                "share among matches (m)": f"{result['m'][f][lv]:.3f}"
                if lv in result["m"][f]
                else "",
                "share among non-matches (u)": f"{result['u'][f][lv]:.3f}"
                if lv in result["u"][f]
                else "",
            }
            for f in fields
            for lv in sorted(result["points"][f], key=lambda lv: -result["points"][f][lv])
        ]
        facts = [
            {"": k, "value": v}
            for k, v in {
                "pairs": f"{len(rows):,}",
                "distinct patterns": result["patterns"],
                "your reviewed pairs used": result["reviewed"],
                "share that match (λ)": f"{result['lambda']:.2%}",
                "rounds": result["rounds"],
                "converged": result["converged"],
                "match threshold": learned["decide"]["match"],
                "review threshold": learned["decide"]["review"],
            }.items()
        ]
        ui = {
            "aws_tables": [
                table("Learned", facts),
                table("Points per field level (learned vs before)", weights, 80),
            ],
            "text": (text,),
            "aws_wires": ["learned scorecard", f"{len(rows):,} pairs"],
            "aws_headline": (
                f"{result['lambda']:.0%} look like matches · match ≥ {learned['decide']['match']:g} "
                f"· review ≥ {learned['decide']['review']:g}",
            ),
        }
        return io.NodeOutput(text, data, ui=ui)


# --- LLM suggestions ------------------------------------------------------------------------
SUGGEST = """You review customer records from a Japanese insurer's legacy systems for nayose (deciding whether two
records are the same person). The data is dirty: OCR errors, typos, wrong kanji for the same reading, missing
fields, half-width or bank-format kana, Japanese era dates, swapped day and month, old addresses, maiden names,
and family members who share an address, phone or bank account but are different people.
Answer with JSON: {"same": "yes" | "no" | "unsure", "reason": "<one short sentence>"}."""
SCHEMA = {
    "type": "object",
    "properties": {"same": {"enum": ["yes", "no", "unsure"]}, "reason": {"type": "string"}},
    "required": ["same", "reason"],
}


def ask_local(
    endpoint: str, model: str, instructions: str, pair: dict, timeout: float = 120
) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": "Record A: "
                + json.dumps(pair.get("a") or {}, ensure_ascii=False)
                + "\nRecord B: "
                + json.dumps(pair.get("b") or {}, ensure_ascii=False)
                + "\nEvidence by field: "
                + json.dumps(
                    {k: v for k, v in pair.items() if k not in ("a", "b", "id", "left", "right")},
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 300,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "verdict", "schema": SCHEMA},
        },
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (loopback only, checked by the caller)
        content = json.loads(response.read())["choices"][0]["message"]["content"]
    try:
        verdict = json.loads(content)
    except ValueError:
        verdict = {"same": "unsure", "reason": "unreadable answer"}
    same = verdict.get("same") if verdict.get("same") in ("yes", "no", "unsure") else "unsure"
    return {
        "suggestion": {"yes": "same", "no": "different", "unsure": "unsure"}[same],
        "reason": str(verdict.get("reason", ""))[:300],
    }


def check_local(endpoint: str) -> None:
    parts = urlsplit(endpoint)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS:
        raise ValueError(
            "LLM suggestions use a model server on this machine only (http://127.0.0.1:…): customer data must not leave it."
        )


class LLMSuggestions(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowLLMSuggestions",
            display_name="LLM suggestions",
            category="agent_workflow_studio/matching",
            search_aliases=["llm judge", "gemma", "suggest", "review", "local model"],
            description=(
                "Asks a local model (an OpenAI-compatible server on this machine, e.g. llama-server with Gemma) about "
                "the pairs Decision rules sent to review, and shows its suggestion and reason next to each pair in the "
                "Review tab. It never decides: you do. Shows how often it agrees with your decisions so far."
            ),
            inputs=[
                io.Dict.Input(
                    "data", display_name="Decisions", tooltip="Connect Decision rules' decisions."
                ),
                io.String.Input(
                    "pairs_file",
                    display_name="Pairs file",
                    default="pairs.jsonl",
                    tooltip="The compared pairs, for the records' details.",
                ),
                io.String.Input(
                    "endpoint",
                    display_name="Model server",
                    default="http://127.0.0.1:8091/v1",
                    tooltip="A local OpenAI-compatible server, e.g. llama-server -hf ggml-org/gemma-4-E4B-it-GGUF:Q4_0 --port 8091.",
                ),
                io.String.Input(
                    "model",
                    display_name="Model",
                    default="gemma-4-E4B-it",
                    tooltip="Model name the server expects.",
                ),
                io.String.Input(
                    "instructions",
                    display_name="Instructions",
                    multiline=True,
                    default=SUGGEST,
                    tooltip="What the model is told; edit it to change how it judges.",
                ),
                io.Int.Input(
                    "limit",
                    display_name="Pairs",
                    default=200,
                    min=1,
                    max=5000,
                    tooltip="At most this many review pairs, most uncertain first.",
                ),
            ],
            outputs=[
                io.Dict.Output(
                    display_name="suggestions",
                    tooltip="Counts and run folders (suggestions.csv) for Cluster guard.",
                )
            ],
            hidden=[io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")

    @classmethod
    def execute(
        cls, data: Any, pairs_file: str, endpoint: str, model: str, instructions: str, limit: int
    ) -> io.NodeOutput:
        check_local(endpoint)
        repo = workflow_repo(cls)
        # An unsettled branch (its own file), or all decisions of the stage before.
        decisions = read_csv(
            upstream_file((data or {}).get("pairs_subset") or "decisions.csv", data)
        )
        pairs = {
            pair_id(row, i): row
            for i, row in enumerate(read_pairs(upstream_file(pairs_file.strip(), data)))
        }
        review = {
            d["id"]: d
            for d in decisions
            if d["decision"] in ("review", "undecided") and d["id"] in pairs
        }
        try:  # most uncertain first, as Decision rules ranked them for the Review tab
            ranked = [
                p["id"]
                for p in json.loads(upstream_file("review.json", data).read_text(encoding="utf-8"))[
                    "pairs"
                ]
            ]
        except (ValueError, KeyError):
            ranked = []
        order = [i for i in ranked if i in review] + [i for i in review if i not in set(ranked)]
        chosen = [review[i] for i in order[:limit]]
        started = time.time()
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                answers = list(
                    pool.map(
                        lambda d: ask_local(endpoint, model, instructions, pairs[d["id"]]), chosen
                    )
                )
        except OSError as error:
            raise ValueError(
                f"Cannot reach the model server at {endpoint} ({error}). Start one, e.g. llama-server -hf ggml-org/gemma-4-E4B-it-GGUF:Q4_0 --port 8091"
            ) from None
        rows = [
            {"id": d["id"], "rules_score": d["score"], **a}
            for d, a in zip(chosen, answers, strict=True)
        ]
        folder = own_folder(repo, "llm_suggestions")
        write_csv(folder / "suggestions.csv", rows, ["id", "rules_score", "suggestion", "reason"])
        (folder / "review.json").write_text(
            json.dumps(
                {
                    "title": f"LLM suggestions · {len(rows):,} review pairs · {model}",
                    "pairs": [
                        {
                            "id": r["id"],
                            "a": pairs[r["id"]].get("a") or {"mention": pairs[r["id"]].get("left")},
                            "b": pairs[r["id"]].get("b")
                            or {"mention": pairs[r["id"]].get("right")},
                            "score": r["rules_score"],
                            "suggestion": r["suggestion"],
                            "reason": f"{model}: {r['reason']}",
                        }
                        for r in rows
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        counts = Counter(r["suggestion"] for r in rows)
        labels = repo.load_labels()
        agree = Counter()
        for r in rows:
            label = labels.get(r["id"])
            if label in ("same", "different") and r["suggestion"] != "unsure":
                agree["agree" if r["suggestion"] == label else "disagree"] += 1
        facts = [
            {"": k, "value": v}
            for k, v in {
                "review pairs": f"{len(review):,}",
                "asked": len(rows),
                "suggests same": counts["same"],
                "suggests different": counts["different"],
                "unsure": counts["unsure"],
                "agrees with your reviews": f"{agree['agree']} of {agree['agree'] + agree['disagree']}"
                if agree
                else "no reviews of these pairs yet",
                "seconds": round(time.time() - started, 1),
                "model": model,
            }.items()
        ]
        ui = {
            "text": (
                f"LLM SUGGESTIONS · {len(rows)} pairs asked · same {counts['same']} · different {counts['different']} · unsure {counts['unsure']}",
            ),
            "aws_tables": [
                table("LLM suggestions", facts),
                table("Suggestions (first 20)", rows, 20),
            ],
            "aws_wires": [f"{len(rows):,} suggestions"],
        }
        result = {
            "same": counts["same"],
            "different": counts["different"],
            "unsure": counts["unsure"],
            "_run_folders": chain(data, folder),
        }
        return io.NodeOutput(result, ui=ui)


# --- Cluster guard and the decision tree -----------------------------------------------------
def guard_clusters(decisions: list[dict], max_size: int) -> dict:
    """Merge matches strongest first; refuse merges joining records decided different (or too big)."""
    parent, members, cannot = {}, {}, defaultdict(set)

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d in decisions:
        for side in (d["left"], d["right"]):
            if side and side not in parent:
                parent[side], members[side] = side, {side}
    # Cannot-link: pairs a rule or the user decided are different people (score bands are not proof).
    for d in decisions:
        if (
            d["decision"] == "different"
            and not d["branch"].startswith("Score")
            and d["left"]
            and d["right"]
        ):
            cannot[d["left"]].add(d["right"])
            cannot[d["right"]].add(d["left"])

    def strength(d: dict) -> tuple:  # your decisions, then rules (no score), then highest score
        return (d["branch"] == "Your review", d["score"] in ("", None), float(d["score"] or 0))

    merged, blocked, joins = [], [], []
    for d in sorted(
        (d for d in decisions if d["decision"] == "match" and d["left"] and d["right"]),
        key=strength,
        reverse=True,
    ):
        a, b = find(d["left"]), find(d["right"])
        if a == b:
            merged.append(d)
            continue
        small, large = sorted((a, b), key=lambda r: len(members[r]))
        conflict = next(
            ((x, y) for x in members[small] for y in cannot[x] if find(y) == large), None
        )
        if conflict:
            blocked.append(
                {**d, "reason": f"would join {conflict[0]} and {conflict[1]}, decided different"}
            )
            continue
        if max_size and len(members[a]) + len(members[b]) > max_size:
            blocked.append({**d, "reason": f"customer would exceed {max_size} records"})
            continue
        parent[small] = large
        members[large] |= members.pop(small)
        merged.append(d)
        joins.append(d)  # this merge joined two groups: the chain that explains a customer
    clusters = {}
    for root, group in members.items():
        for mention in group:
            clusters[mention] = root
    return {"clusters": clusters, "merged": merged, "blocked": blocked, "joins": joins}


def explain_customers(guarded: dict, ids: dict, values: dict) -> dict:
    """Per customer: its records, the merges that joined them (strongest first) and the merges
    refused into it, so anyone can see why these records are one customer."""
    clusters = guarded["clusters"]
    out = defaultdict(lambda: {"records": [], "joined_by": [], "kept_apart": []})
    for mention, root in sorted(clusters.items()):
        out[ids[root]]["records"].append({"id": mention, **values.get(mention, {})})
    step = lambda d: {  # noqa: E731
        "a": d["left"],
        "b": d["right"],
        "stage": d.get("stage", ""),
        "branch": d["branch"],
        "why": d["why"],
    }
    for d in guarded["joins"]:
        out[ids[clusters[d["left"]]]]["joined_by"].append(step(d))
    for d in guarded["blocked"]:
        for side in {ids[clusters[d["left"]]], ids[clusters[d["right"]]]}:
            out[side]["kept_apart"].append({**step(d), "reason": d["reason"]})
    return dict(out)


def decision_tree(decisions: list[dict], suggestions: dict, labels: dict, guarded: dict) -> dict:
    """How every candidate pair flowed: the branch that decided it, then LLM, you, and the guard."""
    by_branch = defaultdict(Counter)
    for d in decisions:
        by_branch[d.get("stage") or d["branch"]][d["decision"]] += 1
    review_ids = [d["id"] for d in decisions if d["decision"] == "review"]
    llm = Counter(suggestions.get(i, "not asked") for i in review_ids)
    you = Counter(
        labels.get(i) if labels.get(i) in ("same", "different") else "not yet" for i in review_ids
    )
    match_count = sum(1 for d in decisions if d["decision"] == "match")
    children = []
    for branch, counts in by_branch.items():
        node = {
            "label": branch,
            "count": sum(counts.values()),
            "decision": max(counts, key=counts.get) if len(counts) == 1 else None,
            # A stage that decided several ways shows each outcome under it.
            "children": [{"label": d, "count": n, "decision": d} for d, n in counts.most_common()]
            if len(counts) > 1
            else [],
        }
        if branch.startswith("Score") and counts.get("review"):
            node["children"] = [
                {
                    "label": "LLM suggests",
                    "count": counts["review"],
                    "children": [
                        {
                            "label": f"{k}",
                            "count": v,
                            "decision": {"same": "match", "different": "different"}.get(
                                k, "review"
                            ),
                        }
                        for k, v in llm.most_common()
                    ],
                },
                {
                    "label": "You decided",
                    "count": counts["review"],
                    "children": [
                        {
                            "label": f"{k}",
                            "count": v,
                            "decision": {"same": "match", "different": "different"}.get(
                                k, "review"
                            ),
                        }
                        for k, v in you.most_common()
                    ],
                },
            ]
        children.append(node)
    clusters = guarded["clusters"]
    sizes = Counter(Counter(clusters.values()).values())
    return {
        "label": "Candidate pairs",
        "count": len(decisions),
        "children": [
            {"label": "Decided by", "count": len(decisions), "children": children},
            {
                "label": "Merges",
                "count": match_count,
                "children": [
                    {"label": "accepted", "count": len(guarded["merged"]), "decision": "match"},
                    {
                        "label": "blocked by the guard",
                        "count": len(guarded["blocked"]),
                        "decision": "different",
                    },
                ],
            },
            {
                "label": f"Customers: {len(set(clusters.values())):,} (largest {max(Counter(clusters.values()).values(), default=0)} records)",
                "count": len(clusters),
                "children": [
                    {"label": f"{size} record{'s' * (size > 1)}", "count": n * size}
                    for size, n in sorted(sizes.items())[:8]
                ],
            },
        ],
    }


class ClusterGuard(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowClusterGuard",
            display_name="Cluster guard",
            category="agent_workflow_studio/matching",
            search_aliases=["cluster", "customers", "union find", "chaining", "nayose tree"],
            description=(
                "Builds customers from the match decisions, strongest first, and refuses any merge that would join "
                "records already decided to be different people (by a rule or by you) or exceed a size cap — so one "
                "wrong link cannot chain hundreds of records. Shows the whole decision tree with counts and every "
                "blocked merge with its reason. Writes clusters.csv (mention, customer)."
            ),
            inputs=[
                io.Dict.Input(
                    "data",
                    display_name="All decisions",
                    optional=True,
                    tooltip="Decision rules' decisions (all pairs), or connect the branches below instead.",
                ),
                *(
                    io.Dict.Input(
                        f"{kind}_{i}",
                        display_name=f"{kind.capitalize()} {i}",
                        optional=True,
                        tooltip=f"A stage's {kind} branch (Decision rules, Your review decisions).",
                    )
                    for kind in ("matched", "different")
                    for i in (1, 2, 3)
                ),
                io.Int.Input(
                    "max_size",
                    display_name="Max records per customer",
                    default=60,
                    min=0,
                    max=100000,
                    tooltip="0 = no cap.",
                ),
            ],
            outputs=[
                io.Dict.Output(
                    display_name="customers",
                    tooltip="Counts and run folders (clusters.csv) for the tool that writes the result.",
                )
            ],
            hidden=[io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")

    @classmethod
    def execute(cls, max_size: int, data: Any = None, **branches: Any) -> io.NodeOutput:
        repo = workflow_repo(cls)
        decisions = read_csv(upstream_file("decisions.csv", data)) if data else []
        # Branches from several stages converge here; each row remembers the stage that decided it.
        for name, branch in sorted(branches.items()):
            if isinstance(branch, dict):
                decisions += [
                    {**row, "stage": branch.get("stage") or name}
                    for row in read_csv(upstream_file(branch.get("file", ""), branch))
                ]
        if not decisions:
            raise ValueError(
                "Connect Decision rules' decisions, or the matched / different branches of each stage."
            )
        data = data or next((b for _, b in sorted(branches.items()) if isinstance(b, dict)), None)
        try:
            suggestions = {
                r["id"]: r["suggestion"] for r in read_csv(upstream_file("suggestions.csv", data))
            }
        except ValueError:
            suggestions = {}
        guarded = guard_clusters(decisions, max_size)
        folder = own_folder(repo, "cluster_guard")
        clusters = guarded["clusters"]
        ids = {root: f"C{i:06d}" for i, root in enumerate(sorted(set(clusters.values())), 1)}
        # The records' values for the Customers tab, from the compared pairs (when upstream).
        values = {}
        try:
            for row in read_pairs(upstream_file("pairs.jsonl", data)):
                for side, record in (("left", "a"), ("right", "b")):
                    if isinstance(row.get(record), dict):
                        values.setdefault(row.get(side), row[record])
        except ValueError:
            pass
        (folder / "customers.json").write_text(
            json.dumps(explain_customers(guarded, ids, values), ensure_ascii=False),
            encoding="utf-8",
        )
        write_csv(
            folder / "clusters.csv",
            [{"mention": m, "customer": ids[r]} for m, r in sorted(clusters.items())],
            ["mention", "customer"],
        )
        write_csv(
            folder / "blocked.csv",
            guarded["blocked"],
            ["id", "left", "right", "branch", "score", "reason"],
        )
        tree = decision_tree(decisions, suggestions, repo.load_labels(), guarded)
        sizes = Counter(clusters.values())
        biggest = [{"customer": ids[r], "records": n} for r, n in sizes.most_common(10)]
        # A short text summary too, so a contract gate can check this node directly.
        largest = sizes.most_common(1)[0][1] if sizes else 0
        summary = (
            f"CLUSTER GUARD · {len(sizes):,} customers · largest {largest} records · "
            f"{len(guarded['merged']):,} merges accepted · {len(guarded['blocked']):,} blocked"
        )
        ui = {
            "text": (summary,),
            "aws_wires": [f"{len(sizes):,} customers"],
            "aws_headline": (
                f"{len(sizes):,} customers · {len(guarded['merged']):,} merges · "
                f"{len(guarded['blocked']):,} blocked",
            ),
            "aws_tree": [tree],
            "aws_tables": [
                table("Blocked merges (why)", guarded["blocked"], 20),
                table("Largest customers", biggest, 10),
            ],
        }
        result = {
            "customers": len(sizes),
            "blocked": len(guarded["blocked"]),
            "file": "clusters.csv",
            "_run_folders": chain(data, folder),
        }
        return io.NodeOutput(result, ui=ui)


def own_title(cls, fallback: str) -> str:
    hidden = getattr(cls, "hidden", None)
    node = (getattr(hidden, "prompt", None) or {}).get(str(getattr(hidden, "unique_id", ""))) or {}
    return node.get("_meta", {}).get("title") or fallback


class ReviewDecisions(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowReviewDecisions",
            display_name="Your review decisions",
            category="agent_workflow_studio/matching",
            search_aliases=["human in the loop", "review", "manual decisions", "clerical review"],
            description=(
                "You, as a step of the cascade: the pairs a stage left unsettled, split by the decisions you made "
                "in the Review tab into matched and different (for Cluster guard) and still open."
            ),
            inputs=[
                io.Dict.Input(
                    "data",
                    display_name="Unsettled",
                    tooltip="A stage's unsettled branch (Decision rules or LLM suggestions).",
                )
            ],
            outputs=[
                io.Dict.Output(
                    display_name="matched", tooltip="Pairs you decided are the same person."
                ),
                io.Dict.Output(
                    display_name="different", tooltip="Pairs you decided are different people."
                ),
                io.Dict.Output(display_name="open", tooltip="Pairs you have not decided yet."),
            ],
            hidden=[io.Hidden.extra_pnginfo, io.Hidden.prompt, io.Hidden.unique_id],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")  # your decisions change between runs

    @classmethod
    def execute(cls, data: Any) -> io.NodeOutput:
        repo = workflow_repo(cls)
        rows = read_csv(upstream_file((data or {}).get("pairs_subset") or "decisions.csv", data))
        rows = [r for r in rows if r["decision"] in ("review", "undecided")]
        labels = repo.load_labels()
        split = {"matched": [], "different": [], "open": []}
        for row in rows:
            label = labels.get(row["id"])
            if label == "same":
                split["matched"].append(
                    {
                        **row,
                        "decision": "match",
                        "branch": "Your review",
                        "why": "you decided: same",
                    }
                )
            elif label == "different":
                split["different"].append(
                    {
                        **row,
                        "decision": "different",
                        "branch": "Your review",
                        "why": "you decided: different",
                    }
                )
            else:
                split["open"].append(row)
        folder = own_folder(repo, "your_review")
        columns = ["id", "left", "right", "decision", "branch", "score", "why"]
        for name, part in split.items():
            write_csv(folder / f"{name}.csv", part, columns)
        stage, chain_ = own_title(cls, "Your review decisions"), chain(data, folder)
        outs = {
            name: {
                "_run_folders": chain_,
                "file": f"{name}.csv",
                "stage": stage,
                "count": len(part),
                **({"pairs_subset": "open.csv"} if name == "open" else {}),
            }
            for name, part in split.items()
        }
        facts = [
            {"": k, "value": v}
            for k, v in {
                "pairs to decide": f"{len(rows):,}",
                "you decided: same": len(split["matched"]),
                "you decided: different": len(split["different"]),
                "still open": f"{len(split['open']):,} (decide them in the Review tab, then run again)",
            }.items()
        ]
        text = f"YOUR REVIEW · {len(split['matched'])} same · {len(split['different'])} different · {len(split['open']):,} open"
        ui = {
            "text": (text,),
            "aws_wires": [
                f"{len(split['matched']):,} same",
                f"{len(split['different']):,} different",
                f"{len(split['open']):,} open",
            ],
            "aws_tables": [
                table("Your review decisions", facts),
                table("Still open", split["open"], 10),
            ],
        }
        return io.NodeOutput(outs["matched"], outs["different"], outs["open"], ui=ui)


NODES = [LearnWeights, LLMSuggestions, ReviewDecisions, ClusterGuard]
