#!/usr/bin/env python3
"""Co-star-driven circular embedding (Phase 14's contrarian test): the
external review's claim was that co-star similarity alone might drive a
*better* theta than topic tags do -- arguably the real signal behind why
Anvaka's Map of GitHub works, since audience overlap reflects real usage
patterns instead of a maintainer's self-declared topic tags (which 155+/319
of this cohort don't even have -- see NOTES.md).

Unlike scripts/layout/topic_theta.py, no aggregation step is
needed here: that script embeds a *topic-topic* PMI graph, then derives
each repo's theta as the TF-IDF-weighted circular mean of its topics'
angles (a repo has 0-20 topics, so "the" topic doesn't exist). Co-star PMI
is already a *repo-repo* graph (scripts/clusters/hierarchy.py's
build_costar_pmi_edges), so spectral-embedding it directly gives each
touched repo its own (x, y) point -- theta = atan2(y, x), r = that point's
own distance from the origin (not a circular-mean resultant length, since
there's nothing being averaged here -- the closest comparable role: a
repo far from the origin sits in a tight, distinctive corner of co-star
space; near the origin means a more generic/central position).

See scripts/layout/compare_theta_sources.py for the evaluation this feeds and
NOTES.md for the result.

Usage: python3 scripts/layout/costar_theta.py
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_cohort():
    top50 = (ROOT / "data/repo-lists/top50_repos.txt").read_text().splitlines()
    extra = (ROOT / "data/repo-lists/dependency_extra_repos.txt").read_text().splitlines()
    return sorted(set(l.strip() for l in top50 + extra if l.strip()))


def connected_components(nodes, edges):
    adj = {}
    for a, b, _ in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen, components = set(), []
    for n in nodes:
        if n in seen:
            continue
        comp, stack = [], [n]
        seen.add(n)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nb in adj.get(cur, []):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        components.append(comp)
    return sorted(components, key=len, reverse=True)


def spectral_embed(nodes, edges):
    idx = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)
    W = np.zeros((n, n))
    for a, b, w in edges:
        if a not in idx or b not in idx:
            continue
        i, j = idx[a], idx[b]
        W[i, j] = w
        W[j, i] = w
    D = np.diag(W.sum(axis=1))
    L = D - W
    eigvals, eigvecs = np.linalg.eigh(L)
    coords = eigvecs[:, 1:3]
    return coords, eigvals


def main():
    from clusters import hierarchy as cluster_mod  # lazy: pulls in igraph/leidenalg
    cohort = load_cohort()
    edges = cluster_mod.build_costar_pmi_edges(cohort)
    nodes = sorted({r for a, b, _ in edges for r in (a, b)})

    components = connected_components(nodes, edges)
    largest = components[0]
    print(
        f"co-star PMI graph: {len(nodes)} touched repos, {len(components)} connected "
        f"component(s), sizes {[len(c) for c in components][:5]}{'...' if len(components) > 5 else ''} "
        f"-- embedding only the largest ({len(largest)} repos); the rest (if any) get theta=null, "
        f"genuinely disconnected from it, not droppable into an arbitrary angle",
        file=sys.stderr,
    )

    largest_set = set(largest)
    component_edges = [(a, b, w) for a, b, w in edges if a in largest_set and b in largest_set]
    coords, eigvals = spectral_embed(largest, component_edges)

    r_raw = np.hypot(coords[:, 0], coords[:, 1])
    r_min, r_max = float(r_raw.min()), float(r_raw.max())
    r_span = (r_max - r_min) or 1.0

    out = {}
    for i, repo in enumerate(largest):
        theta = float(math.atan2(coords[i, 1], coords[i, 0]))
        r = float((r_raw[i] - r_min) / r_span)
        out[repo] = {"theta": theta, "r": r}
    for repo in cohort:
        if repo not in out:
            out[repo] = {"theta": None, "r": 0.0}

    (ROOT / "data/processed/repo_costar_circular.json").write_text(
        json.dumps(out, separators=(",", ":"))
    )
    with_theta = sum(1 for v in out.values() if v["theta"] is not None)
    print(
        f"{with_theta}/{len(cohort)} repos get a real co-star-driven theta. "
        f"Smallest 4 eigenvalues: {[round(float(x), 4) for x in eigvals[:4]]}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
