#!/usr/bin/env python3
"""Pre-fetch and cache each cohort repo's live GitHub description.

The explorer's hover tooltip used to trigger a live, debounced GitHub API
call for every repo node the visitor hovered over -- fine for a handful of
nodes, but with the cohort at 319 repos a normal browsing session can burn
through a meaningful slice of the unauthenticated 60-req/hour cap just from
sweeping the mouse around, before ever clicking anything. Descriptions
rarely change, so fetch them once here (via `gh api`, cached to
data/raw/github_cache/ -- the same cache 10_fetch_new_repo_stats.py already
populated for the 268 newly-added repos) and ship them inline in
repo_aggregates.json instead. The frontend then only needs a live fetch for
things that actually are live: current star/fork counts and language,
fetched on click, not on every hover.

Usage: python3 12_cache_repo_descriptions.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB_CACHE = ROOT / "data/raw/github_cache"
GH_API_THROTTLE_S = 0.4  # see scripts/10_fetch_new_repo_stats.py


def load_cohort():
    top50 = (ROOT / "data/repo-lists/top50_repos.txt").read_text().splitlines()
    extra = (ROOT / "data/repo-lists/dependency_extra_repos.txt").read_text().splitlines()
    repos = [l.strip() for l in top50 + extra if l.strip()]
    return sorted(set(repos))


def gh_api_repo(owner, repo):
    cache_path = GITHUB_CACHE / f"{owner}__{repo}.json"
    if cache_path.exists():
        data = json.loads(cache_path.read_text())
        return None if data.get("_404") else data
    out = subprocess.run(["gh", "api", f"repos/{owner}/{repo}"], capture_output=True, text=True)
    time.sleep(GH_API_THROTTLE_S)
    if out.returncode != 0:
        GITHUB_CACHE.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"_404": True}))
        return None
    data = json.loads(out.stdout)
    GITHUB_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data))
    return data


def main():
    repos = load_cohort()
    descriptions = {}
    fetched = 0
    missing = []
    for repo in repos:
        owner, name = repo.split("/", 1)
        was_cached = (GITHUB_CACHE / f"{owner}__{name}.json").exists()
        data = gh_api_repo(owner, name)
        if not was_cached:
            fetched += 1
        if data is None:
            missing.append(repo)
            continue
        descriptions[repo] = data.get("description")

    out_path = ROOT / "data/processed/repo_descriptions.json"
    out_path.write_text(json.dumps(descriptions, indent=0, sort_keys=True))
    print(f"wrote {len(descriptions)} descriptions to {out_path} ({fetched} fetched fresh, "
          f"{len(descriptions) - fetched} already cached)", file=sys.stderr)
    if missing:
        print(f"no GitHub data at all (dropped): {missing}", file=sys.stderr)


if __name__ == "__main__":
    main()
