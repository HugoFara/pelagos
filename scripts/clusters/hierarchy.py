#!/usr/bin/env python3
"""Precomputed multi-level cluster hierarchy for level-of-detail rendering:
as the repo cohort grows past what a browser can comfortably render/simulate
at once, the explorer needs to collapse distant/off-screen repos into a
bounded number of cluster meta-nodes instead of always materializing all of
them. See NOTES.md.

Phase 13 rewrite. Through Phase 10-12 this clustered a blended union of all
four repo-repo edge tiers, dependency included -- which turned out to be a
real problem, not a hypothetical: communities found on a dependency-heavy
graph converge on package-manager/language boundaries ("npm", "PyPI")
instead of anything a viewer couldn't already guess. Dependency edges are
pulled out of the clustering substrate entirely now (they still drive Phase
12's y-axis, and still render as meta-edges between collapsed cluster nodes
via the frontend's generic materializedAncestorOf() aggregation -- nothing
about that path reads this file's edge weights). Clustering instead runs on
a PMI-weighted co-star + topic similarity graph -- see build_costar_pmi_edges
/build_topic_pmi_edges below -- sparsified with mutual-kNN before the first
clustering pass, then partitioned with Leiden (leidenalg/python-igraph, this
project's second tracked Python dependency after Phase 12's numpy) instead
of hand-rolled Louvain: Louvain has a known bug (Traag, Waltman & van Eck,
"From Louvain to Leiden", 2019) where its local-moving phase can leave a
community internally disconnected, which is a correctness issue, not a
style preference -- worth the new dependency even though a validated
single-level Louvain port already existed here (see git history for that
version). `build_hierarchy` keeps Phase 10's coarsen-and-repeat scaffold
unchanged: coarsen the graph (each community becomes a super-node, inter-
community edges summed), rerun Leiden on the coarsened graph, repeat until
the top level drops under a legibility budget or no further merging
happens -- "recursive Leiden", one of the two hierarchy strategies the
Phase 13 design named as options.

Phase 14 adds a third similarity signal to the substrate:
build_text_embedding_edges below fuses in cosine similarity over per-repo
text embeddings (scripts/fetch/repo_readmes.py, scripts/layout/text_embeddings.py)
-- addressing the "coverage, not noise" gap in the two PMI signals (co-star
only ever touched the original top-50; topic PMI clears its support
threshold for ~155/319). Optional at import time (falls back to the
Phase 13 two-signal substrate if repo_text_embeddings.json doesn't exist
yet), required for the coverage improvement in practice.

Usage: python3 scripts/clusters/hierarchy.py
"""
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import igraph as ig
import leidenalg as la
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data/processed"
RAW = ROOT / "data/raw"
GITHUB_CACHE = RAW / "github_cache"

# Stop coarsening once a level has this few clusters or fewer -- matches the
# rough size the app already renders comfortably today (the 51-repo cohort,
# pre-dependency-expansion).
TOP_LEVEL_BUDGET = 40
MAX_LEVELS = 8

# Leiden resolution for the *first* pass (RBConfigurationVertexPartition's
# resolution_parameter -- the standard modularity resolution generalization,
# same knob Phase 10's hand-rolled Louvain multiplied the "expected edges"
# penalty by). Picked empirically the same way Phase 10's FIRST_PASS_RESOLUTION
# was: tried 1.0/1.2/1.4/1.6/2.0/3.0 and took the point where the largest
# level-1 cluster stops dominating the touched (has-any-signal) population --
# below 2.0 one deep-learning-flavored cluster alone held 40+ of ~185 touched
# repos, a smaller version of the same resolution-limit problem Phase 10 hit;
# above 2.0 clusters kept splitting into smaller pieces without getting more
# thematically coherent to read (checked directly -- see NOTES.md for the
# actual member lists at 2.0: distinct, sensible clusters for web-framework/
# async, generative-AI/LLM-chat, classic-ML/data-science, computer-vision,
# NLP/transformers, core-pytorch, and jupyter/notebook-tooling, not a single
# npm/PyPI-flavored one among them). Later coarsening passes (level 2+) run
# at resolution=1: the coarsened graph is already much sparser, so plain
# modularity doesn't hit the same resolution-limit issue there (same
# reasoning Phase 10 used) -- though see the real_clusters budget check
# below for why this cohort's data usually never reaches a level 2 at all.
FIRST_PASS_RESOLUTION = 2.0

# Resolution for level-2+ coarsening passes. Used to be a flat 1.0 (see
# comment above) on the assumption the coarsened super-node graph was
# already sparse enough not to hit the same resolution-limit problem --
# true until this cohort actually reached a level 2 for the first time
# (JS-ecosystem growth pushed level 1 to 146 real clusters, over
# TOP_LEVEL_BUDGET). At 1.0, level 2 merged 21 unrelated ML-research
# level-1 clusters into one 1085-member blob and 17 unrelated JS-ecosystem
# ones (trpc, remotion, monaco-editor, react-testing-library, ...) into a
# second 960-member blob -- 68% of the whole cohort behind two
# undifferentiated top-level nodes, the exact failure FIRST_PASS_RESOLUTION
# was chosen to prevent at level 1, now recurring one level up. Same fix,
# same knob: re-tuned empirically the same way (checked directly against
# level-2 member lists), landing on 3.0 -- the largest level-2 cluster
# drops to 292/2983 (~10%), and its members read as a real, if broad,
# theme (see NOTES.md) rather than "everything that has any edge at all".
LATER_PASS_RESOLUTION = 3.0

# Mutual-kNN sparsification before the first clustering pass: the raw PMI
# similarity graph is near-complete among the repos that have any signal at
# all, which is both slow to cluster meaningfully and noisy (a repo's 40th-
# strongest PMI edge is rarely a real thematic signal). An edge survives only
# if each endpoint ranks the other among its own K strongest neighbors --
# denoises and bounds degree at the same time, needed regardless of cohort
# size per the Phase 13 design, not just as a scale optimization.
MUTUAL_KNN_K = 20

# This codebase's usual min-shared-support threshold (see scripts/lib/shared_edges.py's
# default) applied on both sides of the PMI computation: an item (repo, for
# co-star; topic, for topic-PMI) needs at least this many supporting
# documents before its individual probability is trusted, and a pair needs
# at least this many co-occurrences before its PMI is trusted. A PMI computed
# from 1-2 coincidental co-occurrences is noise, not signal.
MIN_COSTAR_SUPPORT = 3
MIN_TOPIC_SUPPORT = 2

# Phase 14: cosine-similarity floor before a text-embedding pair is even
# considered -- unlike PMI, cosine similarity between two arbitrary repo
# blurbs is never exactly 0 (shared stopwords, shared "Python library
# for..." phrasing), so an unthresholded graph would be complete/near-
# complete. Picked empirically from this cohort's actual pairwise
# distribution (50,086 pairs): median 0.578, p90 0.669, p95 0.696 -- most
# of that mass is generic background similarity, not a real signal. 0.7 is
# past that background; checked directly, pairs right at this threshold
# are still genuinely related (`agronholm/anyio` <-> `aio-libs/aiohttp`,
# `PyMySQL/PyMySQL` <-> `urllib3/urllib3`) while everything above it up to
# near-duplicates (`huggingface/pytorch-image-models` <-> `rwightman/
# pytorch-image-models`, cosine 1.000 -- literally the same project, one's
# a fork of the other) reads as obviously correct. See NOTES.md.
MIN_TEXT_COSINE = 0.7

# Text-embedding-specific mutual-kNN, applied *before* combining with the
# other two signals (unlike MUTUAL_KNN_K below, which sparsifies the
# already-combined graph) -- found necessary, not just nice-to-have, by
# actually running the fused substrate: at MIN_TEXT_COSINE=0.7 alone, ~50
# small research-paper repos (see build_costar_pmi_edges -- exactly the
# population with the least co-star/topic signal, so text ends up their
# *only* signal) welded into one incoherent "generic AI/ML paper" mega-
# cluster, because a small general-purpose embedding model doesn't
# separate short, jargon-similar ML blurbs by subfield well -- checked
# directly (a cluster mixing poetry generation, anti-spoofing, and
# wireless protocols papers, connected by nothing but shared "language
# model"/"paper"/"code" vocabulary). Lowering the fusion weight on the
# text tier didn't fix it (for a node with *no* other signal, whatever
# weight text gets is 100% of what determines its cluster either way) --
# capping each node to its 4 strongest text matches before fusion did:
# it turns text embedding from "a dense similarity graph" into "a sparse
# nearest-neighbor hint", the same role co-star/topic PMI already play
# naturally. Re-running with this in place split that one mega-cluster
# into several genuinely coherent research sub-themes (hallucination/
# decoding, LoRA/adapter-tuning, diffusion/multimodal, ...) instead. See
# NOTES.md.
TEXT_MUTUAL_KNN_K = 4

LINE_RE = re.compile(
    r'^<https://semrepo\.org/repository/([^>]+)> <[^>]+> <https://semrepo\.org/person/([^>]+)> \.'
)


def load_cohort():
    top50 = (ROOT / "data/repo-lists/top50_repos.txt").read_text().splitlines()
    extra = (ROOT / "data/repo-lists/dependency_extra_repos.txt").read_text().splitlines()
    repos = [l.strip() for l in top50 + extra if l.strip()]
    return sorted(set(repos))


def pmi_edges(item_by_doc, min_item_support, min_pair_support):
    """Generic positive-PMI co-occurrence graph: item_by_doc is doc -> set(items)
    (documents are the "context" items co-occur within -- stargazers for the
    co-star graph, topics for the topic graph; see the two callers below).
    Same shape as scripts/layout/topic_theta.py's build_topic_graph,
    generalized since that function's topic/repo roles are exactly swapped
    between the two calls here (repo is the *item* in both, not the doc)."""
    n_docs = len(item_by_doc)
    item_count = Counter()
    for items in item_by_doc.values():
        item_count.update(items)

    pair_count = Counter()
    for items in item_by_doc.values():
        kept = sorted(i for i in items if item_count[i] >= min_item_support)
        for a in range(len(kept)):
            for b in range(a + 1, len(kept)):
                pair_count[(kept[a], kept[b])] += 1

    edges = []
    for (a, b), c in pair_count.items():
        if c < min_pair_support:
            continue
        pa, pb, pab = item_count[a] / n_docs, item_count[b] / n_docs, c / n_docs
        pmi = math.log(pab / (pa * pb))
        if pmi > 0:
            edges.append((a, b, pmi))
    return edges


def build_costar_pmi_edges(cohort):
    """Co-star PMI: documents are stargazers, items are repos they starred.
    The raw bipartite file only has hasStargazer triples for the original
    top-50 cohort (uncapped shared-stargazer overlap was only ever computed
    for that subset -- see README's pipeline section), so repos outside it
    structurally get zero co-star signal here; genuinely true of the data,
    not a bug -- see NOTES.md."""
    cohort_set = set(cohort)
    stars_by_person = defaultdict(set)
    with (RAW / "repo_stargazers_full.nt").open() as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            repo, person = m.groups()
            if repo in cohort_set:
                stars_by_person[person].add(repo)
    edges = pmi_edges(stars_by_person, MIN_COSTAR_SUPPORT, MIN_COSTAR_SUPPORT)
    touched = {r for a, b, _ in edges for r in (a, b)}
    print(
        f"co-star PMI: {len(stars_by_person)} stargazers -> {len(edges)} positive-PMI "
        f"repo-repo edges, {len(touched)} repos touched",
        file=sys.stderr,
    )
    return edges


def build_topic_pmi_edges(cohort):
    """Topic PMI: documents are GitHub topic tags, items are the repos
    carrying them -- the transpose of topic_theta.py's topic-topic PMI (there,
    repos are the documents and topics are the items)."""
    repos_by_topic = defaultdict(set)
    for repo in cohort:
        owner, name = repo.split("/", 1)
        cache_path = GITHUB_CACHE / f"{owner}__{name}.json"
        if not cache_path.exists():
            continue
        data = json.loads(cache_path.read_text())
        for topic in data.get("topics") or []:
            repos_by_topic[topic].add(repo)
    edges = pmi_edges(repos_by_topic, MIN_TOPIC_SUPPORT, MIN_TOPIC_SUPPORT)
    touched = {r for a, b, _ in edges for r in (a, b)}
    print(
        f"topic PMI: {len(repos_by_topic)} distinct topics -> {len(edges)} positive-PMI "
        f"repo-repo edges, {len(touched)} repos touched",
        file=sys.stderr,
    )
    return edges


def build_text_embedding_edges(cohort):
    """Text-embedding cosine similarity (Phase 14): co-star and topic PMI
    both have real coverage gaps (see build_costar_pmi_edges/
    build_topic_pmi_edges) -- the raw co-star bipartite only ever touches
    the original top-50, and only ~155 of 319 repos clear the topic-PMI
    support thresholds. Embedding `description + topics + a cleaned README
    first paragraph` per repo (scripts/fetch/repo_readmes.py,
    scripts/layout/text_embeddings.py) reaches 317/319 -- this is the "coverage,
    not noise" signal the Phase 14 design is named for. Missing the
    embeddings file (text_embeddings.py not run yet) degrades gracefully to the
    Phase 13 two-signal substrate rather than erroring."""
    path = PROCESSED / "repo_text_embeddings.json"
    if not path.exists():
        return []
    embeddings = json.loads(path.read_text())
    ids = [r for r in cohort if r in embeddings]
    if len(ids) < 2:
        return []
    mat = np.array([embeddings[r] for r in ids])
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = mat / norms
    sims = unit @ unit.T
    edges = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            cos = float(sims[i, j])
            if cos >= MIN_TEXT_COSINE:
                edges.append((ids[i], ids[j], cos))
    sparse = mutual_knn(ids, edges, TEXT_MUTUAL_KNN_K)
    print(
        f"text embedding: {len(ids)} repos embedded -> {len(edges)} edges "
        f">= cosine {MIN_TEXT_COSINE} -> {len(sparse)} after top-{TEXT_MUTUAL_KNN_K} "
        f"mutual-kNN (see TEXT_MUTUAL_KNN_K)",
        file=sys.stderr,
    )
    return sparse


def normalize(edges):
    """edges: [(a, b, w), ...]. Min-max normalize w to [0, 1] so co-star PMI
    and topic PMI (different scales -- co-star has vastly more supporting
    documents per item) don't drown each other out when summed into one
    combined similarity graph. A zero-spread tier normalizes to a uniform
    1.0, not 0 -- "this tier's signal is binary presence", never observed in
    practice here but kept for the same reason Phase 10's version did."""
    if not edges:
        return []
    ws = [e[2] for e in edges]
    wmin, wmax = min(ws), max(ws)
    span = wmax - wmin
    if span == 0:
        return [(a, b, 1.0) for a, b, _ in edges]
    return [(a, b, (w - wmin) / span) for a, b, w in edges]


def build_similarity_graph(cohort):
    combined = defaultdict(float)
    all_edges = (
        normalize(build_costar_pmi_edges(cohort))
        + normalize(build_topic_pmi_edges(cohort))
        + normalize(build_text_embedding_edges(cohort))
    )
    for a, b, w in all_edges:
        key = (a, b) if a < b else (b, a)
        combined[key] += w
    return [(a, b, w) for (a, b), w in combined.items()]


def mutual_knn(ids, edges, k):
    """Keep an edge only if each endpoint ranks the other among its own K
    strongest neighbors -- see MUTUAL_KNN_K's comment. A no-op for any node
    whose real degree is already <= k (most of this cohort, in practice)."""
    neighbor_weights = defaultdict(list)
    for a, b, w in edges:
        neighbor_weights[a].append((w, b))
        neighbor_weights[b].append((w, a))
    topk = {
        i: {other for _, other in sorted(neighbor_weights.get(i, []), reverse=True)[:k]}
        for i in ids
    }
    return [(a, b, w) for a, b, w in edges if b in topk.get(a, ()) and a in topk.get(b, ())]


def leiden_communities(ids, weighted_edges, resolution=1.0, seed=None):
    """Leiden replacement for Phase 10's louvain_communities -- same
    (ids, weighted_edges, resolution) -> {id: community_label} contract, so
    build_hierarchy's coarsen-and-repeat loop below needed no other changes.
    Nodes with no surviving edge (isolated after mutual-kNN, or genuinely no
    co-star/topic signal at all -- see build_costar_pmi_edges) each land in
    their own singleton community, same as the old m2==0 fallback did."""
    id_set = set(ids)
    idx_of = {i: n for n, i in enumerate(ids)}
    g = ig.Graph()
    g.add_vertices(len(ids))
    g_edges, g_weights = [], []
    for a, b, w in weighted_edges:
        if a not in id_set or b not in id_set or a == b:
            continue
        g_edges.append((idx_of[a], idx_of[b]))
        g_weights.append(w)
    g.add_edges(g_edges)
    partition = la.find_partition(
        g,
        la.RBConfigurationVertexPartition,
        weights=g_weights or None,
        resolution_parameter=resolution,
        seed=seed,
    )
    return {ids[n]: comm for n, comm in enumerate(partition.membership)}


def load_repo_stats(cohort):
    aggregates = json.loads((PROCESSED / "repo_aggregates.json").read_text())
    aggregates.update(json.loads((PROCESSED / "dependency_repo_aggregates.json").read_text()))
    stats = {}
    for r in cohort:
        a = aggregates.get(r, {})
        stats[r] = {
            "stargazers": a.get("stargazers") or 0,
            "forks": a.get("forks") or 0,
            "memberCount": 1,
        }
    return stats


def build_hierarchy(cohort):
    raw_edges = mutual_knn(cohort, build_similarity_graph(cohort), MUTUAL_KNN_K)
    raw_degree = defaultdict(float)
    for a, b, w in raw_edges:
        raw_degree[a] += w
        raw_degree[b] += w

    id_degree = dict(raw_degree)
    id_hub = {r: r for r in cohort}
    id_stat = load_repo_stats(cohort)

    clusters = {}
    edges_by_level = {}
    current_ids, current_edges = cohort, raw_edges
    top_level = 0

    for level in range(1, MAX_LEVELS + 1):
        resolution = FIRST_PASS_RESOLUTION if level == 1 else LATER_PASS_RESOLUTION
        community = leiden_communities(current_ids, current_edges, resolution, seed=1)
        groups = defaultdict(list)
        for i in current_ids:
            groups[community[i]].append(i)
        if len(groups) == len(current_ids):
            break  # no merge gained anything this pass -- modularity plateaued

        group_items = sorted(
            groups.items(),
            key=lambda kv: (-sum(id_degree.get(m, 0.0) for m in kv[1]), kv[1][0]),
        )

        parent_of_current = {}
        for idx, (_, members) in enumerate(group_items):
            cid = f"cluster/{level}/{idx}"
            hub_member = max(members, key=lambda m: id_degree.get(m, 0.0))
            stargazers = sum(id_stat[m]["stargazers"] for m in members)
            forks = sum(id_stat[m]["forks"] for m in members)
            member_count = sum(id_stat[m]["memberCount"] for m in members)
            degree = sum(id_degree.get(m, 0.0) for m in members)
            hub = id_hub[hub_member]
            clusters[cid] = {
                "id": cid,
                "level": level,
                "parent": None,
                "children": members,
                "hub": hub,
                "label": hub,
                "memberCount": member_count,
                "stargazers": stargazers,
                "forks": forks,
            }
            for m in members:
                parent_of_current[m] = cid
            id_degree[cid] = degree
            id_hub[cid] = hub
            id_stat[cid] = {"stargazers": stargazers, "forks": forks, "memberCount": member_count}

        if level > 1:
            for cluster in clusters.values():
                if cluster["level"] == level - 1 and cluster["id"] in parent_of_current:
                    cluster["parent"] = parent_of_current[cluster["id"]]

        level_edges = defaultdict(float)
        for a, b, w in current_edges:
            ca, cb = parent_of_current[a], parent_of_current[b]
            if ca == cb:
                continue
            key = (ca, cb) if ca < cb else (cb, ca)
            level_edges[key] += w
        edges_by_level[level] = [(a, b, w) for (a, b), w in level_edges.items()]

        top_level = level
        # Budget check counts only the multi-member ("real", click-through)
        # clusters, not singleton super-nodes -- a large, structurally
        # irreducible population of zero-signal repos (see
        # build_costar_pmi_edges/build_topic_pmi_edges) can never itself
        # drop under any budget by further coarsening (an isolated node has
        # no edge to merge along, at any resolution), and counting them here
        # made the loop keep "coarsening" past a perfectly good level-1
        # partition, over-merging the *real* clusters into one meaningless
        # blob for no legibility gain. Singletons still end up in the
        # rendered top level via collapse_singletons below regardless of
        # how many levels run -- this only changes when to stop looking for
        # more real structure.
        real_clusters = sum(1 for _, members in group_items if len(members) > 1)
        if real_clusters <= TOP_LEVEL_BUDGET:
            break
        current_ids = list(dict.fromkeys(parent_of_current[m] for m in current_ids))
        current_edges = edges_by_level[level]

    return clusters, edges_by_level, top_level


def collapse_singletons(clusters):
    """A cluster with memberCount == 1 wraps exactly one real repo and is
    not an interesting cluster -- the repo should be shown directly rather
    than behind a pointless one-member "cluster" the user has to click
    through. Leiden produces plenty of these -- a hub repo, or (now that
    dependency edges no longer feed the clustering substrate) any repo with
    no co-star/topic signal at all, just stays alone at every level.

    Every reference to a singleton cluster (as another cluster's child, or
    as a top-level root) gets replaced by the real repo id it ultimately
    wraps -- resolved by walking its children chain, which is guaranteed
    by memberCount == 1 to bottom out at exactly one repo, however many
    levels of singleton-wrapping-a-singleton that takes (a high-degree hub
    repo can easily fail to merge with anything at *every* level). The
    singleton entries themselves are then dropped from the output
    entirely. Surviving (memberCount >= 2) clusters never need their own
    `parent` pointer adjusted: a cluster containing >= 2 repos can only
    ever be grouped, at the next level up, into another cluster whose
    memberCount is at least as large -- i.e. also >= 2 -- so a survivor's
    parent is never a singleton.

    Returns (survivor_clusters, top_level_ids) -- the latter a mix of
    cluster ids and real repo ids (any repo that's a singleton all the way
    to the top has no cluster wrapping it at all anymore and appears here
    directly), which is what the frontend now materializes at startup
    instead of always-a-cluster CLUSTER_ROOTS.
    """
    singleton_ids = {cid for cid, c in clusters.items() if c["memberCount"] == 1}

    def promote(id_):
        while id_ in singleton_ids:
            id_ = clusters[id_]["children"][0]
        return id_

    survivors = {cid: c for cid, c in clusters.items() if cid not in singleton_ids}
    for c in survivors.values():
        c["children"] = [promote(ch) for ch in c["children"]]

    # Sorted by descending size with id as an explicit tiebreak -- id alone
    # (not memberCount) since Python's set iteration order for the dedup
    # above isn't guaranteed stable across runs (string hash randomization),
    # and this ordering only ever affects cosmetic initial fan-out angle,
    # but should still be reproducible run to run.
    top_level_ids = sorted(
        {promote(cid) for cid, c in clusters.items() if c["parent"] is None},
        key=lambda id_: (-(survivors[id_]["memberCount"] if id_ in survivors else 1), id_),
    )
    return survivors, top_level_ids


def main():
    cohort = load_cohort()
    clusters, edges_by_level, top_level = build_hierarchy(cohort)
    n_before = len(clusters)
    clusters, top_level_ids = collapse_singletons(clusters)

    out = {
        "topLevel": top_level,
        "clusters": clusters,
        "topLevelIds": top_level_ids,
        "edgesByLevel": {str(l): [[a, b, w] for a, b, w in es] for l, es in edges_by_level.items()},
    }
    (PROCESSED / "repo_cluster_hierarchy.json").write_text(json.dumps(out, separators=(",", ":")))

    by_level = defaultdict(list)
    for c in clusters.values():
        by_level[c["level"]].append(c)
    for level in sorted(by_level):
        sizes = sorted((c["memberCount"] for c in by_level[level]), reverse=True)
        print(f"level {level}: {len(by_level[level])} clusters, sizes {sizes[:8]}...", file=sys.stderr)
    n_repo_roots = sum(1 for id_ in top_level_ids if id_ not in clusters)
    print(
        f"collapsed {n_before - len(clusters)} singleton clusters -> "
        f"{len(top_level_ids)} top-level entities ({len(top_level_ids) - n_repo_roots} clusters, "
        f"{n_repo_roots} repos shown directly, no wrapping cluster at any level) covering {len(cohort)} repos",
        file=sys.stderr,
    )
    top = sorted((c for c in clusters.values() if c["level"] == top_level), key=lambda c: -c["memberCount"])[:10]
    for c in top:
        print(f"  {c['id']}: hub={c['hub']} members={c['memberCount']}", file=sys.stderr)


if __name__ == "__main__":
    main()
