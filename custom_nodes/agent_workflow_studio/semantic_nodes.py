"""Semantic matching with local models: a bi-encoder finds similar records (dense retrieval), a
cross-encoder reranker scores candidate pairs. Both run on this machine; only the public model
weights are downloaded (once, into ComfyUI's models folder), never any record.

The reranker's score becomes one more comparison field (`rerank`: high / medium / low), so Learn
weights weighs it with the other evidence and Decision rules can name it in a rule.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from comfy_api.latest import io

from .data_preview import table
from .table_nodes import Pairs, Table, check_table, columns_of, make, need_columns, report

CATEGORY = "agent_workflow_studio/matching"
EMBEDDER = "cl-nagoya/ruri-v3-30m"  # Japanese bi-encoder, Apache-2.0, 37M parameters
RERANKER = "cl-nagoya/ruri-v3-reranker-310m"  # Japanese cross-encoder, Apache-2.0
_loaded: dict[str, Any] = {}


def local_model(name: str, kind: str):
    """(tokenizer, model, device), loaded once; the weights download on first use."""
    if name in _loaded:
        return _loaded[name]
    import folder_paths
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

    if not name.count("/") == 1 or ".." in name:
        raise ValueError("Model is a Hugging Face name like cl-nagoya/ruri-v3-30m.")
    folder = Path(folder_paths.models_dir) / "agent_workflow_studio" / name.replace("/", "--")
    if not (folder / "config.json").is_file():
        snapshot_download(name, local_dir=folder)
    device = (
        "mps"
        if torch.backends.mps.is_available()
        else "cuda"
        if torch.cuda.is_available()
        else "cpu"
    )
    loader = AutoModelForSequenceClassification if kind == "reranker" else AutoModel
    _loaded[name] = (
        AutoTokenizer.from_pretrained(folder),
        loader.from_pretrained(folder).to(device).eval(),
        device,
    )
    return _loaded[name]


def text_of(record: dict, columns: list[str]) -> str:
    return " ".join(str(record.get(c) or "") for c in columns if record.get(c))


def embed(texts: list[str], name: str = EMBEDDER):
    import torch

    tokenizer, model, device = local_model(name, "embedder")
    out = []
    with torch.no_grad():
        for i in range(0, len(texts), 256):
            batch = tokenizer(
                texts[i : i + 256],
                padding=True,
                truncation=True,
                max_length=64,
                return_tensors="pt",
            ).to(device)
            hidden, mask = model(**batch).last_hidden_state, batch["attention_mask"].unsqueeze(-1)
            mean = (hidden * mask).sum(1) / mask.sum(1)  # mean pooling, as the model was trained
            out.append(torch.nn.functional.normalize(mean, dim=-1).cpu())
    return torch.cat(out) if out else torch.zeros(0)


def rerank(pairs: list[tuple[str, str]], name: str = RERANKER) -> list[float]:
    import torch

    tokenizer, model, device = local_model(name, "reranker")
    scores = []
    with torch.no_grad():
        for i in range(0, len(pairs), 128):
            a, b = zip(*pairs[i : i + 128], strict=True)
            batch = tokenizer(
                list(a), list(b), padding=True, truncation=True, max_length=128, return_tensors="pt"
            ).to(device)
            scores += torch.sigmoid(model(**batch).logits.squeeze(-1)).cpu().tolist()
    return scores


def nearest(vectors, top: int, least: float) -> list[tuple[int, int, float]]:
    """(i, j, cosine) for each record's ``top`` nearest others at least ``least`` similar."""
    found = []
    for start in range(0, len(vectors), 1024):
        sims = vectors[start : start + 1024] @ vectors.T
        values, indices = sims.topk(min(top + 1, len(vectors)), dim=1)
        for row, (vs, js) in enumerate(zip(values.tolist(), indices.tolist(), strict=True)):
            i = start + row
            found += [(i, j, v) for v, j in zip(vs, js, strict=True) if j != i and v >= least]
    return found


def level(score: float, high: float, low: float) -> str:
    return "high" if score >= high else "low" if score < low else "medium"


class FindSimilar(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowFindSimilar",
            display_name="Find similar records (bi-encoder)",
            category=CATEGORY,
            search_aliases=[
                "embeddings",
                "dense retrieval",
                "bi-encoder",
                "vector search",
                "semantic",
            ],
            description=(
                "Dense retrieval: turns each record into a vector with a local Japanese embedding model and pairs "
                "every record with its nearest others, adding them to the candidate pairs (keys: embedding). "
                "Catches records written differently that share no exact key. Runs on this machine."
            ),
            inputs=[
                Table.Input("records", display_name="Records", tooltip="One row per record."),
                Table.Input(
                    "pairs",
                    display_name="Pairs",
                    optional=True,
                    tooltip="Candidate pairs found so far (Find candidate pairs); the similar ones are added.",
                ),
                io.String.Input(
                    "id",
                    display_name="Id columns",
                    default="system, record_id, role",
                    tooltip="Same as in Find candidate pairs.",
                ),
                io.String.Input(
                    "columns",
                    display_name="Text from",
                    default="name, kana, birth, address",
                    tooltip="Columns joined into each record's text.",
                ),
                io.Int.Input(
                    "top",
                    display_name="Nearest per record",
                    default=5,
                    min=1,
                    max=50,
                    tooltip="How many of its most similar records each record is paired with.",
                ),
                io.Float.Input(
                    "least",
                    display_name="Similarity at least",
                    default=0.9,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Cosine similarity of the two records' vectors.",
                ),
                io.String.Input(
                    "model",
                    display_name="Model",
                    default=EMBEDDER,
                    tooltip="A Hugging Face embedding model; its weights download once, records never leave.",
                ),
            ],
            outputs=[Table.Output(display_name="pairs", tooltip="left, right, keys.")],
            is_output_node=True,
        )

    @classmethod
    def execute(
        cls,
        records: Any,
        id: str,  # noqa: A002
        columns: str,
        top: int,
        least: float,
        model: str,
        pairs: Any = None,
    ) -> io.NodeOutput:
        rt = check_table(records, "Records")
        ids, cols = columns_of(id), columns_of(columns)
        need_columns(rt, ids + cols)
        mention = ["/".join(str(r.get(c) or "") for c in ids) for r in rt["rows"]]
        started = time.time()
        vectors = embed([text_of(r, cols) for r in rt["rows"]], model)
        found = {}
        for row in check_table(pairs, "Pairs")["rows"] if pairs is not None else []:
            found[(row["left"], row["right"])] = set(
                filter(None, str(row.get("keys", "")).split(", "))
            )
        added = 0
        for i, j, _ in nearest(vectors, top, least):
            pair = tuple(sorted((mention[i], mention[j])))
            if pair[0] != pair[1]:
                added += pair not in found
                found.setdefault(pair, set()).add("embedding")
        rows = [
            {"left": a, "right": b, "keys": ", ".join(sorted(k))}
            for (a, b), k in sorted(found.items())
        ]
        out = make("candidate pairs", rows, ["left", "right", "keys"])
        notes = {
            "model": model,
            "new pairs from embeddings": f"{added:,}",
            "seconds": f"{time.time() - started:.0f}",
        }
        return io.NodeOutput(out, ui=report("Find similar records", None, out, notes))


class RerankPairs(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowRerankPairs",
            display_name="Rerank pairs (cross-encoder)",
            category=CATEGORY,
            search_aliases=["reranker", "cross-encoder", "semantic similarity", "neural matcher"],
            description=(
                "Reads both records of each pair together with a local Japanese reranker and adds a comparison "
                "field `rerank`: high / medium / low (and its score). By default only the pairs an earlier stage "
                "left unsettled are scored (about 40 pairs a second); the others get n/a. Learn weights then "
                "weighs it like any field. Runs on this machine."
            ),
            inputs=[
                io.MultiType.Input(
                    "data",
                    types=[Pairs, io.Dict],
                    display_name="Pairs",
                    tooltip="Compare fields' data, or a stage's unsettled branch (only those are scored).",
                ),
                io.String.Input(
                    "columns",
                    display_name="Text from",
                    default="name, kana, birth, address, phone",
                    tooltip="Each record's columns (as shown in Review) joined into its text.",
                ),
                io.Float.Input(
                    "high",
                    display_name="High from",
                    default=0.5,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Score from which a pair is high (in our test, half of the true pairs scored above 0.5).",
                ),
                io.Float.Input(
                    "low",
                    display_name="Low below",
                    default=0.05,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Score below which a pair is low.",
                ),
                io.String.Input(
                    "model",
                    display_name="Model",
                    default=RERANKER,
                    tooltip="A Hugging Face cross-encoder; its weights download once, records never leave.",
                ),
            ],
            outputs=[
                Pairs.Output(
                    display_name="data",
                    tooltip="The pairs with `rerank`, for Learn weights and Decision rules.",
                )
            ],
            hidden=[io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, data: Any, columns: str, high: float, low: float, model: str) -> io.NodeOutput:
        from .decision_rules import read_pairs
        from .matching_nodes import chain, own_folder, read_csv, upstream_file, workflow_repo

        if low > high:
            raise ValueError("Low below must not be above High from.")
        rows = read_pairs(upstream_file("pairs.jsonl", data))
        subset = (data or {}).get("pairs_subset") if isinstance(data, dict) else None
        wanted = {r["id"] for r in read_csv(upstream_file(subset, data))} if subset else None
        cols = columns_of(columns)
        todo = [r for r in rows if wanted is None or r["id"] in wanted]
        started = time.time()
        scores = rerank(
            [(text_of(r.get("a") or {}, cols), text_of(r.get("b") or {}, cols)) for r in todo],
            model,
        )
        scored = {r["id"]: s for r, s in zip(todo, scores, strict=True)}
        levels = Counter()
        for r in rows:
            s = scored.get(r["id"])
            r["rerank"] = "n/a" if s is None else level(s, high, low)
            r["rerank_score"] = None if s is None else round(s, 3)
            levels[r["rerank"]] += 1
        folder = own_folder(workflow_repo(cls), "rerank")
        with open(folder / "pairs.jsonl", "w", encoding="utf-8") as handle:
            for r in rows:
                handle.write(json.dumps(r, ensure_ascii=False) + "\n")
        examples = [
            {
                "level": r["rerank"],
                "score": r["rerank_score"],
                "A": text_of(r.get("a") or {}, cols)[:60],
                "B": text_of(r.get("b") or {}, cols)[:60],
            }
            for lv in ("high", "medium", "low")
            for r in [r for r in todo if r["rerank"] == lv][:3]
        ]
        line = f"{len(todo):,} pairs reranked: " + " · ".join(
            f"{lv} {levels[lv]:,}" for lv in ("high", "medium", "low")
        )
        out = {"_run_folders": chain(data, folder), "file": "pairs.jsonl", "count": len(rows)}
        if subset:
            out["pairs_subset"] = subset
        ui = {
            "text": (line,),
            "aws_headline": (line,),
            "aws_wires": [f"{len(rows):,} pairs"],
            "aws_tables": [
                table(
                    "Rerank pairs",
                    [
                        {"": k, "value": v}
                        for k, v in {
                            "model": model,
                            "pairs scored": f"{len(todo):,} of {len(rows):,}",
                            **{
                                f"rerank {lv}": f"{levels[lv]:,}"
                                for lv in ("high", "medium", "low", "n/a")
                            },
                            "seconds": f"{time.time() - started:.0f}",
                        }.items()
                    ],
                ),
                table("Examples per level", examples, 9),
            ],
        }
        return io.NodeOutput(out, ui=ui)


NODES = [FindSimilar, RerankPairs]
