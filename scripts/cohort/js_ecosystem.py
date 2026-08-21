#!/usr/bin/env python3
"""Grow the cohort with real JS-ecosystem repos, to correct the Python
monoculture the PyPI-dependency-driven stream (scripts/cohort/dependency_repos.py)
structurally can't fix on its own -- see NOTES.md's "per-language quota"
entry: that stream's source data (the ~21,100-repo `usedPackage` cohort) is
itself an ML-research corpus with almost no non-Python repos in it at any
rank, so no amount of cap-tuning gets real diversity out of it.

The SemRepo dump has zero coverage of JS repos at all -- checked directly,
`facebook/react`, `vuejs/vue`, `expressjs/express`, `nodejs/node` all return
0 triples. So there's no dependency-degree-style ranking available for JS
the way there is for the PyPI cohort. Falls back to the same kind of signal
the original 51-repo seed used -- real popularity -- via GitHub's Search API
instead of the dump's hasTotalStargazers (which doesn't cover these repos
either).

Ranks `language:javascript` and `language:typescript` repos by stars
(GitHub Search API, capped at 1000 results per query -- a real ceiling,
not a choice made here), merges the two into one combined ranking by
stargazers_count, and walks down that list taking the top N not already in
the cohort. TypeScript isn't split out with its own sub-quota; it's ranked
in the same combined pool as JavaScript since both are the same npm-based
ecosystem this is trying to bring in.

Output merges into the same data/processed/dependency_repo_aggregates.json
/ data/repo-lists/dependency_extra_repos.txt files scripts/cohort/dependency_repos.py
writes, so every downstream script (repo_descriptions.py, topic_edges.py,
hierarchy.py, repo_readmes.py, text_embeddings.py, web_explorer.py)
picks these repos up with zero changes -- they all already read "top50 +
dependency_extra_repos.txt" as the full cohort. Deliberately does NOT touch
data/processed/dependency_source_id_map.json: these repos have no real
usedPackage backing, so scripts/edges/dependency_edges.py correctly leaves them
edge-less in the dependency graph rather than fabricating a relationship.
They still pick up real semantic (topic) and text-embedding edges once
topic_edges.py, repo_readmes.py and text_embeddings.py run, since those two
signals are GitHub-API-derived, not
dump-derived, and don't care which stream a repo came from.

Usage: python3 scripts/cohort/js_ecosystem.py [count=1000]
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


def main(count="1000"):
    count = int(count)

    aggregates_path = ROOT / "data/processed/dependency_repo_aggregates.json"
    aggregates = json.loads(aggregates_path.read_text())
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    seen_lower = {r.lower() for r in aggregates} | {r.lower() for r in top50}

    candidates = search_repos("javascript") + search_repos("typescript")
    print(f"{len(candidates)} raw search results (javascript + typescript, "
          f"before dedup/cohort overlap)", file=sys.stderr)

    by_name = {}
    for item in candidates:
        full_name = item.get("full_name")
        if not full_name:
            continue
        by_name.setdefault(full_name, item)  # first hit wins; same repo can't score differently between the two queries
    ranked = sorted(by_name.values(), key=lambda it: it.get("stargazers_count", 0), reverse=True)

    js_repos = {}
    js_repo_names = []
    for item in ranked:
        if len(js_repos) >= count:
            break
        full_name = item["full_name"]
        if full_name.lower() in seen_lower:
            continue
        owner, name = full_name.split("/", 1)
        gh_data = gh_api_repo(owner, name)  # re-fetch+cache the authoritative single-repo object (search results lack e.g. subscribers_count)
        if not gh_data:  # renamed/deleted between the search response and now
            continue
        seen_lower.add(full_name.lower())
        js_repos[full_name] = aggregate_row(gh_data)
        js_repo_names.append(full_name)

    print(f"added {len(js_repos)} new JS-ecosystem repos "
          f"({len(ranked)} unique ranked candidates, {count} requested)", file=sys.stderr)

    aggregates.update(js_repos)
    aggregates_path.write_text(json.dumps(aggregates, indent=0, sort_keys=True))
    (ROOT / "data/repo-lists/dependency_extra_repos.txt").write_text(
        "\n".join(sorted(aggregates)) + "\n")
    (ROOT / "data/repo-lists/js_ecosystem_repos.txt").write_text(
        "\n".join(sorted(js_repo_names)) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:])
