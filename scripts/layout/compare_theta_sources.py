#!/usr/bin/env python3
"""Phase 14's contrarian-claim test: does a co-star-PMI-driven theta
(scripts/layout/costar_theta.py) correlate with genuine relatedness
better than the topic-PMI-driven theta Phase 12 shipped
(scripts/layout/topic_theta.py)? "Better" needs an actual metric,
not a vibe -- this script's answer: within-cluster circular concentration
against Phase 14's real fused-similarity Leiden clusters
(data/processed/repo_cluster_hierarchy.json), the best available ground
truth for "these repos are genuinely related" this project has.

For each real (memberCount >= 2) level-1 cluster and each theta source,
take the circular mean resultant length R = |mean(e^{i*theta})| over
whichever cluster members have a real theta from that source (repos
without one are skipped, not imputed) -- high R means members agree on
angle (the axis reflects the clustering), low R means their angles are
scattered (the axis is noise relative to what "related" means here).
Cluster-level R values are combined into one weighted-mean score
per source, weighted by how many members contributed (so a 3-member
cluster that happens to agree by chance doesn't outweigh a 20-member one).

Reports both the full-coverage comparison (every source scored on
whatever subset of each cluster it actually covers) and a same-subset
comparison restricted to repos with a real theta from *both* sources, to
separate "which is more precise" from "which just covers different repos".

Usage: python3 scripts/layout/compare_theta_sources.py
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data/processed"


def circular_r(thetas):
    if not thetas:
        return None, 0
    sx = sum(math.cos(t) for t in thetas)
    sy = sum(math.sin(t) for t in thetas)
    return math.hypot(sx, sy) / len(thetas), len(thetas)


def score_source(clusters, theta_of, restrict_to=None):
    total_weight = 0.0
    weighted_r_sum = 0.0
    per_cluster = []
    for c in clusters:
        members = c["children"]
        if restrict_to is not None:
            members = [m for m in members if m in restrict_to]
        thetas = [theta_of[m]["theta"] for m in members if theta_of.get(m, {}).get("theta") is not None]
        r, n = circular_r(thetas)
        if r is None or n < 2:
            continue
        per_cluster.append((c["hub"], n, r))
        weighted_r_sum += r * n
        total_weight += n
    overall = weighted_r_sum / total_weight if total_weight else None
    return overall, total_weight, per_cluster


def main():
    hierarchy = json.loads((PROCESSED / "repo_cluster_hierarchy.json").read_text())
    real_clusters = [c for c in hierarchy["clusters"].values() if c["level"] == 1 and c["memberCount"] >= 2]

    topic_theta = json.loads((PROCESSED / "repo_topic_circular.json").read_text())
    costar_theta = json.loads((PROCESSED / "repo_costar_circular.json").read_text())

    print(f"{len(real_clusters)} real level-1 clusters (memberCount >= 2) to evaluate against", file=sys.stderr)

    topic_full, topic_weight, _ = score_source(real_clusters, topic_theta)
    costar_full, costar_weight, _ = score_source(real_clusters, costar_theta)
    print(
        f"\nFull coverage (each source scored on whatever it covers):\n"
        f"  topic-driven:  weighted-mean R = {topic_full:.4f} over {topic_weight:.0f} member-cluster memberships\n"
        f"  co-star-driven: weighted-mean R = {costar_full:.4f} over {costar_weight:.0f} member-cluster memberships",
        file=sys.stderr,
    )

    both = {r for r in topic_theta if topic_theta[r]["theta"] is not None} & {
        r for r in costar_theta if costar_theta[r]["theta"] is not None
    }
    topic_same, topic_same_w, _ = score_source(real_clusters, topic_theta, restrict_to=both)
    costar_same, costar_same_w, _ = score_source(real_clusters, costar_theta, restrict_to=both)
    print(
        f"\nSame-subset comparison ({len(both)} repos with a real theta from *both* sources):\n"
        f"  topic-driven:  weighted-mean R = {topic_same:.4f} over {topic_same_w:.0f} member-cluster memberships\n"
        f"  co-star-driven: weighted-mean R = {costar_same:.4f} over {costar_same_w:.0f} member-cluster memberships",
        file=sys.stderr,
    )

    winner = "co-star" if costar_same and topic_same and costar_same > topic_same else "topic"
    print(
        f"\nOn the same-subset comparison (the fair one -- full-coverage numbers above conflate "
        f"'more precise' with 'covers different repos'), {winner}-driven theta wins on within-"
        f"cluster circular concentration.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
