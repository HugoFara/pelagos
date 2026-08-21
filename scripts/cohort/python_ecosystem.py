#!/usr/bin/env python3
"""Grow the cohort with real Python repos, same mechanism as
scripts/cohort/js_ecosystem.py and scripts/cohort/java_ecosystem.py: GitHub's
Search API ranked by stars, `language:python` only.

Unlike those two scripts, this one is *not* about fixing the cohort's
Python monoculture (see NOTES.md's "per-language quota" entry) -- it's the
opposite direction, adding more Python on top of an already Python-heavy
cohort. It exists purely because it was asked for directly, to round out
the cohort size alongside a Java top-up (68 Python + 2 Java == a round
4000-repo dependency_extra_repos.txt). The dependency-degree "source repo"
stream (scripts/cohort/dependency_repos.py) already covers Python growth in the
normal case; this is a one-off supplement via the same real-popularity
(stars) signal the JS/Java scripts use, not a replacement for that stream.

Output merges into the same data/processed/dependency_repo_aggregates.json
/ data/repo-lists/dependency_extra_repos.txt files the other fetch scripts
write, so every downstream script (repo_descriptions.py, topic_edges.py,
hierarchy.py, repo_readmes.py, text_embeddings.py, web_explorer.py)
picks these repos up with zero changes. Deliberately does NOT touch
data/processed/dependency_source_id_map.json: these repos are keyed by
real GitHub search results, not the SemRepo usedPackage dump, so
scripts/edges/dependency_edges.py correctly leaves them edge-less in the dependency
graph rather than fabricating a relationship.

This script only sources+fetches (aggregate stats via the GitHub API). It
does not run the rest of the pipeline (descriptions, READMEs, semantic
edges, embeddings, clustering, cluster labels, web build) -- these repos
have real aggregate stats after this runs but aren't in the graph or
clustered until that downstream pipeline is run separately.

Usage: python3 scripts/cohort/python_ecosystem.py [count=68]
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GITHUB_CACHE = ROOT / "data/raw/github_cache"
GH_API_THROTTLE_S = 0.4  # see scripts/cohort/dependency_repos.py
SEARCH_THROTTLE_S = 8  # 30 req/min is the documented Search API ceiling, but GitHub's secondary
# (abuse) rate limit trips well before that in practice -- tripped it directly at 2.2s spacing
# (8 successful calls, then a 403). 8s keeps every run comfortably under it.
SEARCH_BACKOFF_S = 90  # cooldown after a secondary-rate-limit 403, before a single retry
SEARCH_PAGE_SIZE = 100
SEARCH_MAX_PAGES = 10  # GitHub caps any single search query at 1000 total results, full stop


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


def search_page(language, page):
    return subprocess.run(
        ["gh", "api", "-X", "GET", "search/repositories",
         "-f", f"q=language:{language}", "-f", "sort=stars", "-f", "order=desc",
         "-f", f"per_page={SEARCH_PAGE_SIZE}", "-f", f"page={page}"],
        capture_output=True, text=True,
    )


def search_repos(language):
    """Real repos ranked by stars for one language. Page cap (1000 results
    total) is GitHub's own Search API ceiling, not a choice made here."""
    results = []
    for page in range(1, SEARCH_MAX_PAGES + 1):
        out = search_page(language, page)
        time.sleep(SEARCH_THROTTLE_S)
        if out.returncode != 0:
            if "secondary rate limit" in out.stderr.lower():
                print(f"search {language} page {page} hit the secondary rate limit, "
                      f"cooling down {SEARCH_BACKOFF_S}s before one retry", file=sys.stderr)
                time.sleep(SEARCH_BACKOFF_S)
                out = search_page(language, page)
                time.sleep(SEARCH_THROTTLE_S)
            if out.returncode != 0:
                print(f"search {language} page {page} failed: {out.stderr.strip()}", file=sys.stderr)
                break
        items = json.loads(out.stdout).get("items", [])
        if not items:
            break
        results.extend(items)
    print(f"{language}: {len(results)} results", file=sys.stderr)
    return results


def aggregate_row(gh_data):
    return {
        "title": gh_data.get("name"),
        "stargazers": gh_data.get("stargazers_count"),
        "forks": gh_data.get("forks_count"),
        "openIssues": gh_data.get("open_issues_count"),
        "watchers": gh_data.get("subscribers_count"),
        "contributors": None,  # not on the repos endpoint; matches scripts/cohort/dependency_repos.py's gh-api fallback
    }


def main(count="68"):
    count = int(count)

    aggregates_path = ROOT / "data/processed/dependency_repo_aggregates.json"
    aggregates = json.loads(aggregates_path.read_text())
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    seen_lower = {r.lower() for r in aggregates} | {r.lower() for r in top50}

    raw_results = search_repos("python")
    by_name = {}
    for item in raw_results:
        full_name = item.get("full_name")
        if not full_name:
            continue
        by_name.setdefault(full_name, item)  # GitHub's search index can drift across the ~80s
        # of paginated fetching, so the same repo can land on two pages -- first hit wins.
    ranked = sorted(by_name.values(), key=lambda it: it.get("stargazers_count", 0), reverse=True)
    print(f"{len(raw_results)} raw search results, {len(ranked)} unique (python, before cohort overlap)",
          file=sys.stderr)

    python_repos = {}
    python_repo_names = []
    for item in ranked:
        if len(python_repos) >= count:
            break
        full_name = item.get("full_name")
        if not full_name or full_name.lower() in seen_lower:
            continue
        owner, name = full_name.split("/", 1)
        gh_data = gh_api_repo(owner, name)  # re-fetch+cache the authoritative single-repo object (search results lack e.g. subscribers_count)
        if not gh_data:  # renamed/deleted between the search response and now
            continue
        seen_lower.add(full_name.lower())
        python_repos[full_name] = aggregate_row(gh_data)
        python_repo_names.append(full_name)

    print(f"added {len(python_repos)} new Python repos "
          f"({len(ranked)} unique candidates, {count} requested)", file=sys.stderr)

    aggregates.update(python_repos)
    aggregates_path.write_text(json.dumps(aggregates, indent=0, sort_keys=True))
    (ROOT / "data/repo-lists/dependency_extra_repos.txt").write_text(
        "\n".join(sorted(aggregates)) + "\n")
    python_list_path = ROOT / "data/repo-lists/python_ecosystem_repos.txt"
    prev_python = set(python_list_path.read_text().split()) if python_list_path.exists() else set()
    python_list_path.write_text(
        "\n".join(sorted(prev_python | set(python_repo_names))) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:])
