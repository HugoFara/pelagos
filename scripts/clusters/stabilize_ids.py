#!/usr/bin/env python3
"""Phase 15, part one: cluster ID stability across pipeline reruns.

scripts/clusters/hierarchy.py mints cluster ids positionally
(`cluster/{level}/{idx}`, idx = rank in that level's group_items sort).
Leiden itself is seeded (seed=1) so *re-running on unchanged data* is
already stable -- but this pipeline's whole point is to be re-run on
refreshed data (new stars, new topics, a repo's README changed), and any
such change can shuffle which community sorts to idx 0 vs idx 3 even when
19 of 20 clusters kept the same members. Every permalink that names a
cluster id (web/template.html's `?id=` hash param) would silently start
pointing at a different cluster after any refresh. Unacceptable for
something meant to be shared/bookmarked -- see ROADMAP.md Phase 15.

Fix: keep a snapshot of the *previous* stabilized hierarchy
(repo_cluster_hierarchy_prev.json) and, each run, match this run's fresh
clusters against it by Jaccard similarity on flattened repo membership,
solved as an assignment problem (Hungarian / scipy.optimize.
linear_sum_assignment -- not greedy nearest-match, which can let two new
clusters both claim the same old id and leave a worse pairing stranded).
A matched pair keeps the *old* cluster's id; an unmatched new cluster
(genuinely new theme, or membership changed too much to count as "the
same" cluster) mints a fresh id from a monotonic counter that never
reuses a value seen in either snapshot -- ids are permanent identifiers,
not slots to recycle.

Matching runs independently per level (a level-2 super-cluster's
flattened membership can be much larger than any level-1 cluster's, so
comparing across levels isn't meaningful) -- moot in practice today,
since this cohort's data has so far never produced a level 2 (see
scripts/clusters/hierarchy.py's real_clusters budget check), but the
hierarchy format supports it and this should too.

Labels are deliberately NOT touched here -- scripts/clusters/labels.py
owns labels entirely, keyed off the *stable* ids this script produces,
with its own membership-signature cache check to decide whether a label
needs recomputing. Doing that here too would be redundant and could
disagree with labels.py's own cache.

Usage: python3 scripts/clusters/stabilize_ids.py
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data/processed"
HIERARCHY_PATH = PROCESSED / "repo_cluster_hierarchy.json"
PREV_PATH = PROCESSED / "repo_cluster_hierarchy_prev.json"

CLUSTER_ID_RE = re.compile(r"^cluster/(\d+)/(\d+)$")

# Minimum Jaccard overlap (on flattened repo membership) for two clusters
# across runs to count as "the same cluster" rather than coincidentally
# related. Picked from the general dynamic-community-matching literature
# (e.g. Greene et al. 2010's "Tracking the Evolution of Communities in
# Dynamic Social Networks" uses a comparable bar), not empirically tuned
# against this project's own before/after data the way MIN_TEXT_COSINE
# etc. were -- there was no second real snapshot to tune against until
# this script's own first two runs produce one. Revisit once real
# data-refresh history accumulates (see NOTES.md).
MATCH_THRESHOLD = 0.5


def flatten_members(clusters, cid):
    """A cluster's children can be real repo ids (level 1) or nested
    cluster ids (level 2+, after collapse_singletons promotes any
    singleton reference but leaves real sub-clusters as-is) -- walk down
    to the real repo ids so two clusters from different runs, and
    possibly different levels of nesting, can be compared on the same
    footing."""
    result = set()
    stack = [cid]
    while stack:
        cur = stack.pop()
        if cur in clusters:
            stack.extend(clusters[cur]["children"])
        else:
            result.add(cur)
    return result


def max_seq(ids):
    best = -1
    for i in ids:
        m = CLUSTER_ID_RE.match(i)
        if m:
            best = max(best, int(m.group(2)))
    return best


def match_level(old_clusters, new_clusters, old_ids, new_ids):
    """Returns {new_id: old_id} for pairs whose Jaccard overlap clears
    MATCH_THRESHOLD. Hungarian assignment first (so two new clusters
    can't both grab the same old id at the other's expense), threshold
    filter after -- the assignment step must run on the full rectangular
    matrix regardless of threshold, since dropping low-overlap
    candidates before solving could starve a pair that's the *best
    available* match for both sides even if not a great one, which the
    filter is exactly meant to catch instead."""
    if not old_ids or not new_ids:
        return {}
    old_members = {i: flatten_members(old_clusters, i) for i in old_ids}
    new_members = {i: flatten_members(new_clusters, i) for i in new_ids}
    cost = np.ones((len(new_ids), len(old_ids)))
    for a, ni in enumerate(new_ids):
        for b, oi in enumerate(old_ids):
            inter = len(new_members[ni] & old_members[oi])
            if inter == 0:
                continue
            union = len(new_members[ni] | old_members[oi])
            cost[a, b] = 1 - inter / union
    row_ind, col_ind = linear_sum_assignment(cost)
    mapping = {}
    for a, b in zip(row_ind, col_ind):
        jaccard = 1 - cost[a, b]
        if jaccard >= MATCH_THRESHOLD:
            mapping[new_ids[a]] = old_ids[b]
    return mapping


def main():
    new_hier = json.loads(HIERARCHY_PATH.read_text())

    if not PREV_PATH.exists():
        print(
            "no previous snapshot to match against -- seeding baseline as-is "
            f"({len(new_hier['clusters'])} clusters keep scripts/clusters/hierarchy.py's positional ids)",
            file=sys.stderr,
        )
        PREV_PATH.write_text(json.dumps(new_hier, separators=(",", ":")))
        return

    old_hier = json.loads(PREV_PATH.read_text())
    old_clusters, new_clusters = old_hier["clusters"], new_hier["clusters"]

    old_by_level = defaultdict(list)
    for cid, c in old_clusters.items():
        old_by_level[c["level"]].append(cid)
    new_by_level = defaultdict(list)
    for cid, c in new_clusters.items():
        new_by_level[c["level"]].append(cid)

    next_seq = max_seq(list(old_clusters) + list(new_clusters)) + 1

    rename = {}
    for level in sorted(new_by_level):
        matched = match_level(old_clusters, new_clusters, old_by_level.get(level, []), new_by_level[level])
        rename.update(matched)
        for new_id in new_by_level[level]:
            if new_id not in rename:
                rename[new_id] = f"cluster/{level}/{next_seq}"
                next_seq += 1

    renamed_clusters = {}
    for cid, c in new_clusters.items():
        nid = rename[cid]
        c["id"] = nid
        c["children"] = [rename.get(ch, ch) for ch in c["children"]]
        if c["parent"] is not None:
            c["parent"] = rename.get(c["parent"], c["parent"])
        renamed_clusters[nid] = c
    new_hier["clusters"] = renamed_clusters
    new_hier["topLevelIds"] = [rename.get(i, i) for i in new_hier["topLevelIds"]]
    new_hier["edgesByLevel"] = {
        lvl: [[rename.get(a, a), rename.get(b, b), w] for a, b, w in es]
        for lvl, es in new_hier["edgesByLevel"].items()
    }

    n_matched = sum(1 for nid, oid in rename.items() if oid in old_clusters)
    n_renamed = sum(1 for nid, oid in rename.items() if oid in old_clusters and nid != oid)
    n_new = sum(1 for nid, oid in rename.items() if oid not in old_clusters)
    print(
        f"{n_matched} clusters matched to a previous stable id (>= {MATCH_THRESHOLD} Jaccard) "
        f"-- {n_renamed} needed an actual id rename to restore it, "
        f"{n_matched - n_renamed} already matched by coincidence; "
        f"{n_new} minted a fresh id (no confident match, new/reshaped theme)",
        file=sys.stderr,
    )

    HIERARCHY_PATH.write_text(json.dumps(new_hier, separators=(",", ":")))
    PREV_PATH.write_text(json.dumps(new_hier, separators=(",", ":")))


if __name__ == "__main__":
    main()
