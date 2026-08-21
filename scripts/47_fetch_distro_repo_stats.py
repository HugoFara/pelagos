#!/usr/bin/env python3
"""Fetch GitHub stats for the repos 46_debian_dependency_edges.py wants to add.

46 resolves Debian's package graph to repositories and works out which of them
deserve to be nodes -- already in the cohort, or carrying at least a threshold
of reverse-dependencies. The ones that are new have no aggregate row, no
description and no topics, so they cannot be rendered or clustered yet. This
fetches exactly that, and nothing else.

Same shape and same cache as 24_fetch_js_ecosystem_repos.py: one
`gh api repos/{owner}/{name}` per repo, cached to data/raw/github_cache/, then
merged into data/processed/dependency_repo_aggregates.json and
data/repo-lists/dependency_extra_repos.txt, which is what every downstream
script already reads as "the cohort". Nothing downstream needs to know these
repos arrived from a distribution rather than from a language registry.

A repo that 404s is skipped rather than added as an empty node. That happens
for real reasons here -- a Homepage pointing at a repo that has since been
renamed or deleted -- and an aggregate row full of nulls would render as a
node with no size, no label and no data behind it.

Usage: python3 scripts/47_fetch_distro_repo_stats.py
"""
import json
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB_CACHE = ROOT / "data/raw/github_cache"
EXTRA_LIST = ROOT / "data/repo-lists/distro_extra_repos.txt"
AGGREGATES_PATH = ROOT / "data/processed/dependency_repo_aggregates.json"
COHORT_LIST = ROOT / "data/repo-lists/dependency_extra_repos.txt"

GH_API_THROTTLE_S = 0.4  # see scripts/10_fetch_new_repo_stats.py
WORKERS = 6  # latency-bound; the core REST budget is 5000/hour and this is ~1200 calls

_lock = threading.Lock()
_progress = Counter()


def gh_api_repo(owner, name):
    """The cached repo object, or None for a 404. Identical to
    24_fetch_js_ecosystem_repos.py's, including the negative cache."""
    cache_path = GITHUB_CACHE / f"{owner}__{name}.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text())
        except ValueError:
            data = None
        if data is not None:
            return None if data.get("_404") else data
    out = subprocess.run(["gh", "api", f"repos/{owner}/{name}"], capture_output=True, text=True)
    time.sleep(GH_API_THROTTLE_S)
    GITHUB_CACHE.mkdir(parents=True, exist_ok=True)
    if out.returncode != 0:
        cache_path.write_text(json.dumps({"_404": True}))
        return None
    try:
        data = json.loads(out.stdout)
    except ValueError:
        return None
    cache_path.write_text(json.dumps(data))
    return data


def aggregate_row(data):
    return {
        "title": data.get("name"),
        "stargazers": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "openIssues": data.get("open_issues_count"),
        "watchers": data.get("subscribers_count"),
        "contributors": None,  # not on the repos endpoint, same as 24/25/26/27
        "description": data.get("description") or "",
    }


def fetch(repo, total):
    owner, _, name = repo.partition("/")
    data = gh_api_repo(owner, name)
    with _lock:
        _progress["done" if data else "missing"] += 1
        done = sum(_progress.values())
        if done % 200 == 0 or done == total:
            print(f"  {done}/{total} ({_progress['missing']} gone or renamed)", file=sys.stderr)
    return repo, data


def main():
    if not EXTRA_LIST.exists():
        print("no distro_extra_repos.txt -- run 46_debian_dependency_edges.py first",
              file=sys.stderr)
        return
    wanted = sorted({line.strip() for line in EXTRA_LIST.read_text().splitlines() if line.strip()})
    aggregates = json.loads(AGGREGATES_PATH.read_text())
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    known = {r.lower() for r in aggregates} | {r.lower() for r in top50}
    todo = [r for r in wanted if r.lower() not in known]
    print(f"{len(wanted)} repos from the distro pass, {len(todo)} not yet in the cohort",
          file=sys.stderr)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda r: fetch(r, len(todo)), todo))

    added, missing = {}, 0
    for repo, data in results:
        if not data:
            missing += 1
            continue
        # GitHub's own current name for the repo, so a renamed one lands on
        # the name it actually answers to -- the same rule 44_repo_identity.py
        # applies, applied at the point of entry rather than cleaned up later.
        full_name = data.get("full_name") or repo
        if full_name.lower() in known:
            continue
        added[full_name] = aggregate_row(data)
        known.add(full_name.lower())

    aggregates.update(added)
    AGGREGATES_PATH.write_text(json.dumps(aggregates, indent=0, sort_keys=True))
    cohort = sorted(set(json.loads(AGGREGATES_PATH.read_text())))
    COHORT_LIST.write_text("\n".join(cohort) + "\n")

    stars = sorted((v["stargazers"] or 0) for v in added.values())
    median = stars[len(stars) // 2] if stars else 0
    print(f"added {len(added)} repos to the cohort ({missing} were gone or renamed away), "
          f"median {median} stars -- cohort is now {len(cohort) + len(top50)} entries "
          f"across both aggregate files", file=sys.stderr)


if __name__ == "__main__":
    main()
