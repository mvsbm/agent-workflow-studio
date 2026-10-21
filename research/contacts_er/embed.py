"""Local ruri embeddings and cross-encoder scores (on this Mac; nothing leaves it)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

MODELS = Path.home() / ".local/share/agent-workflow-studio/ComfyUI/models/agent_workflow_studio"
_cache: dict = {}


def _device():
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def embed(texts: list[str], model: str = "cl-nagoya--ruri-v3-30m", max_length: int = 64) -> np.ndarray:
    import torch
    from transformers import AutoModel, AutoTokenizer

    if model not in _cache:
        _cache[model] = (
            AutoTokenizer.from_pretrained(MODELS / model),
            AutoModel.from_pretrained(MODELS / model).to(_device()).eval(),
        )
    tok, net = _cache[model]
    out = []
    with torch.no_grad():
        for i in range(0, len(texts), 512):
            batch = tok(
                [t or " " for t in texts[i : i + 512]],
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            ).to(_device())
            h, m = net(**batch).last_hidden_state, batch["attention_mask"].unsqueeze(-1)
            v = torch.nn.functional.normalize((h * m).sum(1) / m.sum(1), dim=-1)
            out.append(v.float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 256), np.float32)


def rerank(pairs: list[tuple[str, str]], model: str = "cl-nagoya--ruri-v3-reranker-310m") -> np.ndarray:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    if model not in _cache:
        _cache[model] = (
            AutoTokenizer.from_pretrained(MODELS / model),
            AutoModelForSequenceClassification.from_pretrained(MODELS / model).to(_device()).eval(),
        )
    tok, net = _cache[model]
    out = []
    with torch.no_grad():
        for i in range(0, len(pairs), 64):
            a, b = zip(*pairs[i : i + 64], strict=True)
            batch = tok(list(a), list(b), padding=True, truncation=True, max_length=160, return_tensors="pt").to(_device())
            out += torch.sigmoid(net(**batch).logits.squeeze(-1)).float().cpu().tolist()
    return np.array(out, dtype=np.float32)
