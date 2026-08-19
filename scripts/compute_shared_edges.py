#!/usr/bin/env python3
"""Turn a repo-stargazer bipartite edge list (from 05_shared_stargazers.sh) into a
repo-repo graph weighted by shared stargazer count, then prune it to each node's
top-K strongest edges so it stays legible instead of a near-complete hairball.

Usage: python3 compute_shared_edges.py raw/repo_stargazers_full.nt processed/repo_shared_edges.json processed/repo_shared_edges_pruned.json [min_shared] [top_k] [with_members]

with_members (0/1, default 0): also embed the sorted list of shared person
logins as a 4th tuple element. Off by default since shared-stargazer overlaps
can run into the thousands (useless to ship to a browser); the shared-
contributor graph passes 1 here since its overlaps top out around a few
dozen and the explorer wants to show them on edge hover.

pseudonymize (0/1, default 0): replace each person identifier with a stable
salted-hash label before any of it reaches data/processed/ or the shipped
page. Pass 1 for the two person-based tiers (shared-contributor,
shared-issue-author); leave it 0 for 13_semantic_edges.py, whose "members"
are GitHub topic tags rather than people.

## Why a salt, and why it must stay out of the repo

GitHub usernames are a small, fully enumerable public set. An unsalted hash
-- or a salt committed alongside the data -- is reversible by anyone willing
to hash a username list, which would make this decorative rather than real.
The salt is generated once into data/raw/pseudonym_salt.txt (data/raw/ is
gitignored), so labels stay stable across rebuilds here while nobody else can
recompute the mapping. A fresh checkout mints a different salt and therefore
different labels; nothing downstream depends on their specific values.

This is pseudonymization, not anonymization: which repos a given label links
is itself a quasi-identifier, and someone could intersect public contributor
lists to re-derive who it is. That's an acceptable residual here precisely
because those lists are already public -- the point is that this repo no
longer ships a ready-made, scrapable roster of ~4000 named individuals.
"""
import hashlib
import json
import re
import secrets
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

SALT_PATH = Path(__file__).resolve().parent.parent / "data/raw/pseudonym_salt.txt"
# 12 hex chars = 2.8e14 label space. At the ~29k distinct people in this
# dataset the birthday collision probability is ~1e-6; a collision would
# merge two people into one shared-member entry, undercounting an overlap by
# one rather than corrupting anything structurally.
PSEUDONYM_HEX = 12


def load_or_create_salt():
    """The salt lives in gitignored data/raw/ so it never ships -- see the
    module docstring. Created on first use."""
    if SALT_PATH.exists():
        salt = SALT_PATH.read_text().strip()
        if salt:
            return salt
    salt = secrets.token_hex(32)
    SALT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SALT_PATH.write_text(salt + "\n")
    print(f"minted a new pseudonym salt -> {SALT_PATH} (gitignored; keep it to "
          f"keep labels stable across rebuilds)", file=sys.stderr)
    return salt


def pseudonym(person, salt):
    digest = hashlib.blake2b(f"{salt}:{person}".encode("utf-8"), digest_size=16).hexdigest()
    return f"person-{digest[:PSEUDONYM_HEX]}"

LINE_RE = re.compile(
    r'^<https://semrepo\.org/repository/([^>]+)> <[^>]+> <https://semrepo\.org/person/([^>]+)> \.'
)


def build_edges(bipartite, min_shared=3, top_k=4, with_members=0):
    """bipartite: repo -> set(members). Returns (full_edges, pruned_edges),
    each a list of (a, b, weight[, sorted_members]) tuples, weight-sorted
    descending. Shared core used by every repo-repo overlap tier (shared-
    stargazer/contributor N-triples callers, and the topic-based semantic
    edges built straight from cached JSON -- see 13_semantic_edges.py).
    """
    repos = list(bipartite.keys())

    def edge_tuple(a, b, shared):
        return (a, b, len(shared), sorted(shared)) if with_members else (a, b, len(shared))

    edges = []
    for a, b in combinations(repos, 2):
        shared = bipartite[a] & bipartite[b]
        if len(shared) >= min_shared:
            edges.append(edge_tuple(a, b, shared))
    edges.sort(key=lambda e: -e[2])

    by_node = defaultdict(list)
    for e in edges:
        a, b, w = e[0], e[1], e[2]
        by_node[a].append((w, b))
        by_node[b].append((w, a))
    for n in by_node:
        by_node[n].sort(reverse=True)

    keep = {}
    for e in edges:
        a, b = e[0], e[1]
        a_topk = {x[1] for x in by_node[a][:top_k]}
        b_topk = {x[1] for x in by_node[b][:top_k]}
        if b in a_topk or a in b_topk:
            keep[(a, b)] = e
    pruned = sorted(keep.values(), key=lambda e: -e[2])

    return edges, pruned


def main(raw_nt_path, out_full, out_pruned, min_shared=3, top_k=4, with_members=0,
         pseudonymize=0):
    min_shared, top_k = int(min_shared), int(top_k)
    with_members, pseudonymize = int(with_members), int(pseudonymize)

    # Applied at parse time so no real login reaches the edge tuples at all.
    # Set membership is unaffected: the same person always maps to the same
    # label, so every intersection/count below is identical either way.
    salt = load_or_create_salt() if pseudonymize else None

    repo_stars = defaultdict(set)
    with open(raw_nt_path) as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            repo, person = m.groups()
            repo_stars[repo].add(pseudonym(person, salt) if pseudonymize else person)

    edges, pruned = build_edges(repo_stars, min_shared, top_k, with_members)

    with open(out_full, "w") as f:
        json.dump(edges, f, separators=(",", ":"))
    with open(out_pruned, "w") as f:
        json.dump(pruned, f, separators=(",", ":"))

    touched = {n for e in pruned for n in (e[0], e[1])}
    print(f"{len(repo_stars)} repos, {len(edges)} edges >= {min_shared} shared "
          f"-> {len(pruned)} after top-{top_k} pruning, {len(touched)}/{len(repo_stars)} nodes retain an edge",
          file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(f"usage: {sys.argv[0]} raw_nt out_full_json out_pruned_json "
                 f"[min_shared=3] [top_k=4] [with_members=0] [pseudonymize=0]")
    main(*sys.argv[1:])
