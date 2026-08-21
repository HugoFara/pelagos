#!/usr/bin/env python3
"""Circular topic embedding per repo (Phase 12's theta-axis + free radius):
a repo has 0-20 GitHub topics, so "the" topic doesn't exist, and placing
topics around a circle in an arbitrary (e.g. alphabetical) order creates
seams between unrelated adjacent sectors. Instead:

1. Build a topic-topic PMI co-occurrence graph over the cohort's tagged
   repos (topics are the "items", repos are the "documents") -- reuses the
   same cached `topics` field as scripts/edges/topic_edges.py, no new
   network calls. Only positive-PMI pairs become edges (negative PMI means
   "co-occur less than chance", not a similarity signal), and both the
   individual topics and the pair itself need >=2 supporting repos (this
   codebase's usual min-shared threshold, see scripts/lib/shared_edges.py) --
   PMI from a single coincidental co-occurrence is too noisy to trust.
2. Spectral-embed that graph in 2D (Laplacian eigenmaps: the eigenvectors
   of the two smallest *non-zero* eigenvalues of the graph Laplacian).
   Originally a single connected component (175 topics/545 edges at the
   319-repo cohort, checked directly), so there was exactly one trivial
   zero-eigenvalue eigenvector to skip -- no longer true at larger cohort
   sizes (verified via scipy's connected_components, not assumed), so this
   embeds only the largest component; topics stranded in a smaller one
   fall back to theta=None like an untagged topic. `theta_topic = atan2`
   of a topic's two embedding coordinates.
3. Per repo, `theta_repo` is the TF-IDF-weighted circular mean of its
   topics' angles -- multi-topic repos land naturally between sectors
   instead of needing an arbitrary tiebreak. The resultant vector length
   R falls out of that same circular mean for free: low R (a repo whose
   topics point in scattered directions -- diffuse/generalist) sits near
   the shared axis in the frontend, high R (topics that agree) sits at
   the periphery.

Repos with no topics at all, or whose only topics didn't clear the
min-support thresholds above, get theta=null (genuinely undefined -- no
signal to derive an angle from) and r=0 (also genuinely correct: zero
resultant length is what "no information" means here, not a placeholder).

Usage: python3 scripts/layout/topic_theta.py
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.linalg import eigh
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

ROOT = Path(__file__).resolve().parents[2]
GITHUB_CACHE = ROOT / "data/raw/github_cache"

MIN_TOPIC_SUPPORT = 2
MIN_PAIR_SUPPORT = 2


def load_cohort():
    top50 = (ROOT / "data/repo-lists/top50_repos.txt").read_text().splitlines()
    extra = (ROOT / "data/repo-lists/dependency_extra_repos.txt").read_text().splitlines()
    return sorted(set(l.strip() for l in top50 + extra if l.strip()))


def load_repo_topics(repos):
    repo_topics = {}
    for repo in repos:
        owner, name = repo.split("/", 1)
        cache_path = GITHUB_CACHE / f"{owner}__{name}.json"
        if not cache_path.exists():
            continue
        data = json.loads(cache_path.read_text())
        topics = set(data.get("topics") or [])
        if topics:
            repo_topics[repo] = topics
    return repo_topics


def build_topic_graph(repo_topics):
    n_docs = len(repo_topics)
    topic_count = Counter()
    for topics in repo_topics.values():
        topic_count.update(topics)

    pair_count = Counter()
    for topics in repo_topics.values():
        ts = sorted(topics)
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                pair_count[(ts[i], ts[j])] += 1

    edges = []
    for (t1, t2), c in pair_count.items():
        if c < MIN_PAIR_SUPPORT or topic_count[t1] < MIN_TOPIC_SUPPORT or topic_count[t2] < MIN_TOPIC_SUPPORT:
            continue
        p1, p2, p12 = topic_count[t1] / n_docs, topic_count[t2] / n_docs, c / n_docs
        pmi = math.log(p12 / (p1 * p2))
        if pmi > 0:
            edges.append((t1, t2, pmi))
    return edges, topic_count, n_docs


def spectral_embed(edges):
    all_topics = sorted({t for e in edges for t in (e[0], e[1])})
    all_idx = {t: i for i, t in enumerate(all_topics)}
    n_all = len(all_topics)
    rows, cols = [], []
    for t1, t2, w in edges:
        i, j = all_idx[t1], all_idx[t2]
        rows += [i, j]
        cols += [j, i]
    adjacency = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n_all, n_all))
    # A spectral embedding is only meaningful within a single connected
    # component -- a fragmented graph's Laplacian has one trivial
    # zero-eigenvalue eigenvector *per component*, so blindly skipping just
    # one (this project's original 175-topic/319-repo run really was a
    # single component, checked directly at the time -- see NOTES.md Phase
    # 12) silently returns component-membership noise instead of a real
    # embedding once the graph fragments. Checked directly rather than
    # assumed: restrict to the largest component and embed only that;
    # topics stranded in a smaller component fall back to the existing,
    # already-documented "no signal" contract (theta=None) exactly like an
    # untagged repo, rather than getting a meaningless angle.
    n_components, labels = connected_components(adjacency, directed=False)
    giant_label = Counter(labels).most_common(1)[0][0]
    topics = [t for t in all_topics if labels[all_idx[t]] == giant_label]
    idx = {t: i for i, t in enumerate(topics)}
    n = len(topics)
    W = np.zeros((n, n))
    for t1, t2, w in edges:
        if t1 not in idx or t2 not in idx:
            continue
        i, j = idx[t1], idx[t2]
        W[i, j] = w
        W[j, i] = w
    D = np.diag(W.sum(axis=1))
    L = D - W
    # Generalized eigenproblem (Lv = lambda*D*v), not plain eigh(L): this
    # topic co-occurrence graph has a heavily skewed degree distribution --
    # a handful of ecosystem-wide hub topics ("javascript", "python", ...)
    # co-occur with almost everything (degree in the hundreds), vs. most
    # topics sitting near the two-repo support floor (degree ~1-2). Under
    # the unnormalized Laplacian, a node's low-frequency eigenvector entries
    # shrink roughly with its own degree, so those hub topics -- which are
    # also the topics most repos actually carry -- collapse to near-zero
    # (x, y) coordinates, and atan2 of a near-origin point is dominated by
    # noise rather than real structure (checked directly: this collapsed
    # 75% of topics into one 30-degree wedge). Dividing through by D is
    # exactly what Laplacian Eigenmaps (Belkin & Niyogi) prescribes to stop
    # degree from suppressing a node's own coordinate.
    eigvals, eigvecs = eigh(L, D)
    # Smallest eigenvalue is ~0 (constant eigenvector -- the graph is now a
    # single component by construction above); the next two give the 2D
    # embedding.
    coords = eigvecs[:, 1:3]
    theta = {t: float(math.atan2(coords[idx[t], 1], coords[idx[t], 0])) for t in topics}
    return theta, eigvals, n_components, len(topics), n_all


def repo_circular_mean(repo_topics, topic_theta, topic_count, n_docs):
    out = {}
    for repo, topics in repo_topics.items():
        embedded = [t for t in topics if t in topic_theta]
        if not embedded:
            out[repo] = {"theta": None, "r": 0.0}
            continue
        sx = sy = wsum = 0.0
        for t in embedded:
            idf = math.log(n_docs / topic_count[t])
            w = idf  # tf is 1 for presence-only topic tags
            sx += w * math.cos(topic_theta[t])
            sy += w * math.sin(topic_theta[t])
            wsum += w
        if wsum == 0:
            out[repo] = {"theta": None, "r": 0.0}
            continue
        r = math.hypot(sx, sy) / wsum
        out[repo] = {"theta": math.atan2(sy, sx), "r": float(r)}
    return out


def main():
    cohort = load_cohort()
    repo_topics = load_repo_topics(cohort)
    edges, topic_count, n_docs = build_topic_graph(repo_topics)
    topic_theta, eigvals, n_components, n_giant, n_all_topics = spectral_embed(edges)
    repo_circular = repo_circular_mean(repo_topics, topic_theta, topic_count, n_docs)

    out = {repo: repo_circular.get(repo, {"theta": None, "r": 0.0}) for repo in cohort}

    (ROOT / "data/processed/topic_circular_embedding.json").write_text(
        json.dumps(topic_theta, separators=(",", ":"))
    )
    (ROOT / "data/processed/repo_topic_circular.json").write_text(
        json.dumps(out, separators=(",", ":"))
    )

    with_theta = sum(1 for v in out.values() if v["theta"] is not None)
    r_values = [v["r"] for v in out.values() if v["theta"] is not None]
    print(
        f"{len(repo_topics)}/{len(cohort)} cohort repos have topics, "
        f"{n_all_topics} distinct topics ({n_components} connected components) -- embedded "
        f"the largest ({n_giant}/{n_all_topics} topics, {len(edges)} positive-PMI edges total) "
        f"-> {with_theta}/{len(cohort)} repos get a real theta. R spread: "
        f"min={min(r_values):.3f} max={max(r_values):.3f} "
        f"mean={sum(r_values)/len(r_values):.3f} (low R = diffuse, high R = specialized). "
        f"Smallest 4 eigenvalues: {[round(float(x), 4) for x in eigvals[:4]]}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
