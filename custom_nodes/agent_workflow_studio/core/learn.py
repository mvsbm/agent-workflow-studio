"""Learn from your reviews (research/contacts_er: the decision engine that won).

Day 0, before any review: an unsupervised Fellegi-Sunter score only orders the first pairs to
review. After that: logistic regression on the reviewed pairs; the next pairs to review are the
ones the model is least sure of (active learning); reviews the model strongly disputes, judged by a
model that never saw them (out of fold), are asked again (the review audit).
"""

from __future__ import annotations

import math

import numpy as np

from .features import col

# --- day 0: Fellegi-Sunter over field levels (orders the first reviews) --------------------------
LEVELS = {
    "name_n": lambda X: np.select(
        [col(X, "eq_loose") > 0, col(X, "jw_full_n") >= 0.92, col(X, "jw_full_n") >= 0.8, col(X, "jw_full_n") == 0],
        [0, 1, 2, 4], 3),
    "name_k": lambda X: np.select(
        [col(X, "jw_full_k") >= 0.99, col(X, "jw_full_k") >= 0.85, col(X, "jw_full_k") == 0], [0, 1, 3], 2),
    "given": lambda X: np.where(col(X, "giv_match") > 0, 0, np.where(col(X, "jw_giv_n") >= 0.85, 1, 2)),
    "dob": lambda X: np.select(
        [col(X, "dob_exact") > 0, col(X, "dob_swapped") > 0, col(X, "dob_digits") == 1], [0, 1, 2], 3),
    "gender": lambda X: np.select([col(X, "gender") > 0, col(X, "gender") < 0], [0, 1], 2),
    "phone": lambda X: np.select(
        [(col(X, "phone_shared") > 0) & (col(X, "phone_min_freq") < 1.5), col(X, "phone_shared") > 0, col(X, "phone_both") > 0],
        [0, 1, 2], 3),
    "address": lambda X: np.select(
        [col(X, "unit_shared") > 0, (col(X, "house_shared") > 0) & (col(X, "house_min_freq") < 1.5), col(X, "house_shared") > 0, col(X, "addr_best_jw") >= 0.9, col(X, "same_locality") > 0, col(X, "addr_both") > 0],
        [0, 1, 2, 3, 4, 5], 6),
}  # fmt: skip


def day_zero(X, iters=40, prior=0.1) -> np.ndarray:
    """Fellegi-Sunter by EM over the levels; an exact name agreement on a rare value weighs more."""
    if not len(X):
        return np.zeros(0)
    L = {k: f(X).astype(int) for k, f in LEVELS.items()}
    sizes = {k: int(v.max()) + 1 for k, v in L.items()}
    u = {k: np.bincount(L[k], minlength=sizes[k]) / len(X) for k in L}
    m = {k: np.linspace(2, 0.2, sizes[k]) / np.linspace(2, 0.2, sizes[k]).sum() for k in L}
    lam = prior
    for _ in range(iters):
        lm = np.log(lam) + sum(np.log(m[k][L[k]] + 1e-9) for k in L)
        lu = np.log(1 - lam) + sum(np.log(u[k][L[k]] + 1e-9) for k in L)
        g = 1 / (1 + np.exp(lu - lm))
        lam = float(np.clip(g.mean(), 1e-4, 1 - 1e-4))
        for k in L:
            m[k] = np.bincount(L[k], weights=g, minlength=sizes[k]) + 1e-3
            m[k] /= m[k].sum()
            u[k] = np.bincount(L[k], weights=1 - g, minlength=sizes[k]) + 1e-3
            u[k] /= u[k].sum()
    lm = np.log(lam) + sum(np.log(m[k][L[k]]) for k in L)
    lu = np.log(1 - lam) + sum(np.log(u[k][L[k]]) for k in L)
    exact = L["name_n"] == 0
    lu = lu + np.where(exact, -col(X, "rare_full") - math.log(u["name_n"][0]), 0)
    return 1 / (1 + np.exp(lu - lm))


# --- logistic regression (scikit-learn's default: L2, C=1, unpenalized intercept, standardized) ---
def fit(X, y, idx, C=1.0, iters=50) -> np.ndarray:
    """Match probabilities for every pair, from a model trained on the pairs `idx`."""
    idx = np.asarray(idx, dtype=int)
    yl = np.asarray(y, dtype=float)[idx]
    if yl.min() == yl.max():
        return np.full(len(X), float(yl[0]))
    Xl = np.asarray(X, dtype=np.float64)[idx]
    mu, sd = Xl.mean(0), Xl.std(0)
    sd[sd == 0] = 1.0
    A = np.hstack([(Xl - mu) / sd, np.ones((len(Xl), 1))])
    reg = np.full(A.shape[1], 1.0 / C)
    reg[-1] = 0.0
    w = np.zeros(A.shape[1])
    for _ in range(iters):  # Newton's method: the loss is convex, a few steps reach the optimum
        p = 1 / (1 + np.exp(-(A @ w)))
        grad = A.T @ (p - yl) + reg * w
        hess = (A * (p * (1 - p))[:, None]).T @ A + np.diag(reg) + 1e-10 * np.eye(len(w))
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-10:
            break
    Z = (np.asarray(X, dtype=np.float64) - mu) / sd
    return 1 / (1 + np.exp(-(Z @ w[:-1] + w[-1])))


def unsure_first(prob, exclude, count) -> list[int]:
    """The `count` pairs closest to 50/50 that are not in `exclude`."""
    out = []
    for i in np.argsort(np.abs(prob - 0.5)):
        if int(i) not in exclude:
            out.append(int(i))
            if len(out) == count:
                break
    return out


def first_batch(base, batch, seed=0) -> list[int]:
    """Day 0: the pairs the day-0 score is least sure of, and a few random ones so the model sees
    both sides."""
    rng = np.random.default_rng(seed)
    order = np.argsort(np.abs(base - 0.5))
    chosen = list(order[:batch]) + list(
        rng.choice(len(base), size=min(len(base), batch), replace=False)
    )
    return list(dict.fromkeys(int(i) for i in chosen))


def simulate(X, y, base, budget, batch=50, seed=0) -> tuple[np.ndarray, list[int]]:
    """A benchmark reviewer who knows the answer: review the first batch, then retrain and review
    the pairs the model is least sure of, until the budget is spent."""
    labelled = first_batch(base, min(batch, budget), seed)
    while True:
        prob = fit(X, y, labelled)
        if len(labelled) >= budget:
            break
        more = unsure_first(prob, set(labelled), batch)
        if not more:
            break
        labelled += more
    prob = prob.copy()
    prob[labelled] = np.asarray(y)[labelled]  # a reviewed pair is decided as reviewed
    return prob, labelled


def disputed(X, y, labelled, seed=99, folds=5, gap=0.7) -> list[int]:
    """Reviews a model trained without them (out of fold) strongly disagrees with."""
    lab = np.asarray(labelled, dtype=int)
    if len(lab) < folds * 4:
        return []
    fold = np.arange(len(lab)) % folds
    np.random.default_rng(seed).shuffle(fold)
    oof = np.zeros(len(lab))
    for f in range(folds):
        oof[fold == f] = fit(X, y, lab[fold != f])[lab[fold == f]]
    return [int(i) for i in lab[np.abs(oof - np.asarray(y)[lab]) > gap]]
