#!/usr/bin/env python3
"""Real repo-repo edges from shared GitHub topics ("tags"), a first cut at a
semantic relationship: two repos that self-declare overlapping subject-matter
tags (e.g. both tagged "diffusion-models" and "pytorch") probably relate on
what they're *about*, independent of dependency or audience overlap.

Topics come from the `topics` field already present in the GitHub API
responses cached at data/raw/github_cache/{owner}__{repo}.json by scripts
10-12 -- no new network calls needed. The SemRepo dump has a matching
foaf:topic predicate, but it only covers 55/319 cohort repos (checked
directly); the cached live API responses cover 184/319, so this uses those
instead of re-deriving from the dump.

This is deliberately the crude version: literal tag-string overlap, not NLP/
embedding similarity over descriptions or READMEs. A future pass could
replace or supplement this with an LLM/embedding-based semantic-similarity
edge over repo descriptions or README content -- see NOTES.md.

Usage: python3 scripts/13_semantic_edges.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compute_shared_edges import build_edges

ROOT = Path(__file__).resolve().parent.parent
GITHUB_CACHE = ROOT / "data/raw/github_cache"

MIN_SHARED = 2
TOP_K = 4


def load_cohort():
    top50 = (ROOT / "data/repo-lists/top50_repos.txt").read_text().splitlines()
    extra = (ROOT / "data/repo-lists/dependency_extra_repos.txt").read_text().splitlines()
    repos = [l.strip() for l in top50 + extra if l.strip()]
    return sorted(set(repos))


def load_topics(repos):
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


def main():
    repos = load_cohort()
    repo_topics = load_topics(repos)
    edges, pruned = build_edges(repo_topics, min_shared=MIN_SHARED, top_k=TOP_K, with_members=1)

    out_full = ROOT / "data/processed/repo_semantic_edges.json"
    out_pruned = ROOT / "data/processed/repo_semantic_edges_pruned.json"
    out_full.write_text(json.dumps(edges, separators=(",", ":")))
    out_pruned.write_text(json.dumps(pruned, separators=(",", ":")))

    # The per-repo tag sets themselves, not just the edges they produce.
    # data/raw/github_cache is gitignored, so without this the only committed
    # record of who is tagged what is the pruned edge list -- which has
    # already dropped every tag that didn't clear min-shared/top-K. The
    # explorer needs the unpruned sets to place a repo the pipeline has never
    # seen (its shared-tag edges against this cohort, and the TF-IDF weights
    # behind its topic angle -- see build_web_explorer.py).
    (ROOT / "data/processed/repo_topics.json").write_text(
        json.dumps({r: sorted(t) for r, t in sorted(repo_topics.items())},
                   separators=(",", ":"))
    )

    touched = {n for e in pruned for n in (e[0], e[1])}
    print(
        f"{len(repo_topics)}/{len(repos)} cohort repos have topics, "
        f"{len(edges)} edges >= {MIN_SHARED} shared tags "
        f"-> {len(pruned)} after top-{TOP_K} pruning, "
        f"{len(touched)} nodes retain an edge",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
