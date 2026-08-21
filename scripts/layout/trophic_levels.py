#!/usr/bin/env python3
"""Trophic level per repo (Phase 12's y-axis): a continuous height where
each repo floats ~1 above the mean height of what it depends on, solved
from the dependency graph rather than walked as longest-path DAG depth --
robust to the rare cross-ecosystem dependency cycles this data has and to
a single mis-resolved edge, unlike longest-path depth (see ROADMAP.md
Phase 12; MacKay/Johnson/Sansom's trophic-coherence formalism for food
webs, applied here to "a depends on b" in place of "a eats b").

The linear system: minimizing, over every dependency edge a -> b ("a
depends on b"), (h_a - h_b - 1)^2 -- i.e. wanting every consumer to sit
one level above the mean of what it depends on -- has a stationary point
at Λh = v, where Λ is the Laplacian of the *undirected* dependency graph
(degree = in+out per node) and v_k = outdeg(k) - indeg(k) counted on the
original directed edges (derived directly from the gradient of that
objective, not copied from a food-web paper's edge-direction convention,
since this data's edges point the opposite way -- consumer to resource,
not resource to consumer).

Λ is singular (one-dimensional nullspace = the constant vector, since the
dependency graph turns out to be a single connected component -- checked
directly, all 244 nodes that touch any dependency edge form one
component). `lstsq` returns the minimum-norm solution, equivalent to
fixing mean(h) = 0; normalized to [0,1] afterward for rendering.

Repos with zero dependency edges (75/319 in this cohort) have no defined
trophic level -- this outputs null for them rather than a guessed
placeholder; the frontend supplies a rendering-only fallback.

Usage: python3 scripts/layout/trophic_levels.py
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def load_cohort():
    top50 = (ROOT / "data/repo-lists/top50_repos.txt").read_text().splitlines()
    extra = (ROOT / "data/repo-lists/dependency_extra_repos.txt").read_text().splitlines()
    return sorted(set(l.strip() for l in top50 + extra if l.strip()))


def main():
    edges = json.loads((ROOT / "data/processed/repo_dependency_edges.json").read_text())
    nodes = sorted({e[0] for e in edges} | {e[1] for e in edges})
    idx = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)

    L = np.zeros((n, n))
    v = np.zeros(n)
    for a, b, *_ in edges:
        ia, ib = idx[a], idx[b]
        v[ia] += 1  # a's out-degree
        v[ib] -= 1  # b's in-degree
        L[ia, ia] += 1
        L[ib, ib] += 1
        L[ia, ib] -= 1
        L[ib, ia] -= 1

    h, *_ = np.linalg.lstsq(L, v, rcond=None)

    # Sanity check + self-correct rather than trust the derivation blindly:
    # a real dependency edge a -> b should end up with h[a] > h[b] (the
    # consumer sits above what it depends on). Flip globally if not.
    deltas = np.array([h[idx[a]] - h[idx[b]] for a, b, *_ in edges])
    mean_delta = deltas.mean()
    if mean_delta < 0:
        h = -h
        deltas = -deltas
        mean_delta = -mean_delta

    # Trophic incoherence (MacKay/Johnson/Sansom): how far real edges
    # deviate from the ideal "exactly one level apart" -- 0 means a
    # perfectly layered graph, a free readability signal before any
    # rendering happens.
    incoherence = float(np.sqrt(np.mean((deltas - 1) ** 2)))

    hmin, hmax = float(h.min()), float(h.max())
    normalized = (h - hmin) / (hmax - hmin) if hmax > hmin else np.full(n, 0.5)

    cohort = load_cohort()
    out = {repo: (float(normalized[idx[repo]]) if repo in idx else None) for repo in cohort}

    (ROOT / "data/processed/repo_trophic_levels.json").write_text(
        json.dumps(out, separators=(",", ":"))
    )

    # The raw -> [0,1] rescaling constants, so a consumer of this file can
    # still work in the units the objective was written in. Everything above
    # minimizes (h_a - h_b - 1)^2, i.e. "one level" is literally 1.0 raw --
    # but only the normalized heights ship, where one level is 1/(hmax-hmin).
    # The explorer needs that to place a repo outside this solve: holding
    # every cohort height fixed, the same objective has an exact closed-form
    # minimum for a single new node, and it is stated in raw units. Emitted
    # here rather than recomputed there because these two numbers are an
    # output of this solve and nothing else can derive them.
    (ROOT / "data/processed/trophic_scale.json").write_text(
        json.dumps({"raw_min": hmin, "raw_max": hmax,
                    "level_step_normalized": (1.0 / (hmax - hmin)) if hmax > hmin else 0.0},
                   separators=(",", ":"))
    )

    print(
        f"{len(nodes)}/{len(cohort)} cohort repos have a dependency edge and a real "
        f"trophic level; {len(cohort) - len(nodes)} get no y-constraint (null). "
        f"mean edge delta (consumer height - dependency height) = {mean_delta:.3f} "
        f"(want > 0: consumers sit above what they depend on), "
        f"trophic incoherence = {incoherence:.3f} (0 = perfectly layered)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
