#!/usr/bin/env python3
"""Grow the cohort with real C and C++ repos, same mechanism as
scripts/cohort/js_ecosystem.py / scripts/cohort/java_ecosystem.py /
scripts/cohort/python_ecosystem.py / scripts/cohort/go_ecosystem.py /
scripts/cohort/rust_ecosystem.py: GitHub's Search API ranked by stars.

C/C++ was the last visibly-thin language left -- counted directly off
data/raw/github_cache before this script ran: Python 4791, Java 1010, Rust
1004, Go 1001, TypeScript 563, JavaScript 455, C++ 103, C 19. The 122 C/C++
repos that were already here arrived as PyPI dependency targets of an
ML-research corpus (tensorflow, faiss, xgboost, mediapipe...), so they're a
biased slice of C/C++ rather than the ecosystem itself.

## Correcting the record in rust_ecosystem.py's docstring

scripts/cohort/rust_ecosystem.py claims C/C++'s dependency signal
"effectively does not exist", off a probe of 4 repos (nlohmann/json,
fmtlib/fmt, opencv/opencv, protocolbuffers/protobuf) for vcpkg.json /
conanfile.txt / conanfile.py where all 12 requests 404'd. That sample was
too small and the conclusion was wrong. Re-probed across 40 major C/C++
repos:

    .gitmodules       9/40  (22%)
    CMakeLists.txt   26/40  (65%)
    meson.build       5/40  (12%)
    conanfile.py      3/40  ( 7%)
    vcpkg.json        2/40  ( 5%)

The original probe happened to pick four repos that are all
header-only/self-contained and use none of them. There IS a real manifest
layer; it just isn't a single registry manifest the way Cargo/npm/Go are --
see scripts/edges/cpp_deps.py for which parts of it are actually
resolvable (`.gitmodules` carries literal GitHub URLs and does the heavy
lifting; CMake's find_package does not and stays excluded).

Unlike the single-language fetch scripts above, this one draws from two
language qualifiers and consumes the merged candidate pool in global star
order, so the split between C and C++ is whatever the real star ranking
produces rather than a quota picked here.

Output merges into the same data/processed/dependency_repo_aggregates.json
/ data/repo-lists/dependency_extra_repos.txt files the other fetch scripts
write, so every downstream script (repo_descriptions.py, topic_edges.py,
hierarchy.py, repo_readmes.py, text_embeddings.py, web_explorer.py)
picks these repos up with zero changes. Deliberately does NOT touch
data/processed/dependency_source_id_map.json: these repos are keyed by real
GitHub search results, not the SemRepo usedPackage dump, so
scripts/edges/dependency_edges.py would correctly leave them edge-less there -- their
real dependency edges come from cpp_manifests.py/cpp_deps.py instead.

This script only sources+fetches (aggregate stats via the GitHub API). It
does not run the rest of the pipeline (descriptions, READMEs, semantic
edges, embeddings, clustering, cluster labels, web build) -- these repos
have real aggregate stats after this runs but aren't in the graph or
clustered until that downstream pipeline is run separately.

Usage: python3 scripts/cohort/cpp_ecosystem.py [count=1000]
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
# (abuse) rate limit trips well before that in practice -- see scripts/cohort/go_ecosystem.py.
SEARCH_BACKOFF_S = 90  # cooldown after a secondary-rate-limit 403, before a single retry
SEARCH_PAGE_SIZE = 100
SEARCH_MAX_PAGES = 10  # GitHub caps any single search query at 1000 total results, full stop

# Two separate GitHub language labels, one ecosystem. Both qualifiers verified
# to return distinct real results (`language:c++` -> tensorflow/tensorflow,
# `language:c` -> torvalds/linux).
LANGUAGES = ["c++", "c"]


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


def search_page(query, page):
    return subprocess.run(
        ["gh", "api", "-X", "GET", "search/repositories",
         "-f", f"q={query}", "-f", "sort=stars", "-f", "order=desc",
         "-f", f"per_page={SEARCH_PAGE_SIZE}", "-f", f"page={page}"],
        capture_output=True, text=True,
    )


def search_repos(language, max_stars=None):
    """Real repos ranked by stars for one language. Page cap (1000 results
    total) is GitHub's own Search API ceiling, not a choice made here --
    pass max_stars (an exclusive upper bound) to run a follow-up query below
    a previous batch's star floor and step past that ceiling."""
    query = f"language:{language}" if max_stars is None else f"language:{language} stars:<{max_stars}"
    results = []
    for page in range(1, SEARCH_MAX_PAGES + 1):
        out = search_page(query, page)
        time.sleep(SEARCH_THROTTLE_S)
        if out.returncode != 0:
            if "secondary rate limit" in out.stderr.lower():
                print(f"search '{query}' page {page} hit the secondary rate limit, "
                      f"cooling down {SEARCH_BACKOFF_S}s before one retry", file=sys.stderr)
                time.sleep(SEARCH_BACKOFF_S)
                out = search_page(query, page)
                time.sleep(SEARCH_THROTTLE_S)
            if out.returncode != 0:
                print(f"search '{query}' page {page} failed: {out.stderr.strip()}", file=sys.stderr)
                break
        items = json.loads(out.stdout).get("items", [])
        if not items:
            break
        results.extend(items)
    print(f"'{query}': {len(results)} results", file=sys.stderr)
    return results


def ranked_unique(language, max_stars=None):
    raw_results = search_repos(language, max_stars)
    by_name = {}
    for item in raw_results:
        full_name = item.get("full_name")
        if not full_name:
            continue
        by_name.setdefault(full_name, item)  # GitHub's search index can drift across the ~80s
        # of paginated fetching, so the same repo can land on two pages -- first hit wins.
    ranked = sorted(by_name.values(), key=lambda it: it.get("stargazers_count", 0), reverse=True)
    print(f"{len(raw_results)} raw search results, {len(ranked)} unique "
          f"({language}, max_stars={max_stars}, before cohort overlap)", file=sys.stderr)
    return ranked


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

    cpp_repos = {}
    cpp_repo_names = []
    star_floor = {lang: None for lang in LANGUAGES}
    exhausted = set()
    total_unique = 0
    # Same star-floor continuation as java/go/rust_ecosystem.py, run per
    # language: a single query's top-1000-by-stars window can be exhausted by
    # cohort overlap well
    # before `count` new repos are found, so each round re-queries strictly
    # under that language's previous star floor to step past the 1000-result
    # cap. Candidates from both languages are merged and consumed in one
    # global star order per round, so neither language gets a quota.
    for _ in range(5):  # generous continuation cap; each round is its own 8-80s+ search pass
        round_candidates = []
        for language in LANGUAGES:
            if language in exhausted:
                continue
            ranked = ranked_unique(language, star_floor[language])
            if not ranked:
                exhausted.add(language)
                continue
            total_unique += len(ranked)
            round_candidates.extend(ranked)
            floor = ranked[-1].get("stargazers_count")
            if not floor:
                exhausted.add(language)
            else:
                star_floor[language] = floor
        if not round_candidates:
            break
        round_candidates.sort(key=lambda it: it.get("stargazers_count", 0), reverse=True)

        for item in round_candidates:
            if len(cpp_repos) >= count:
                break
            full_name = item.get("full_name")
            if not full_name or full_name.lower() in seen_lower:
                continue
            owner, name = full_name.split("/", 1)
            gh_data = gh_api_repo(owner, name)  # re-fetch+cache the authoritative single-repo object (search results lack e.g. subscribers_count)
            if not gh_data:  # renamed/deleted between the search response and now
                continue
            seen_lower.add(full_name.lower())
            cpp_repos[full_name] = aggregate_row(gh_data)
            cpp_repo_names.append(full_name)
        if len(cpp_repos) >= count or len(exhausted) == len(LANGUAGES):
            break

    print(f"added {len(cpp_repos)} new C/C++ repos "
          f"({total_unique} unique candidates seen across all rounds, {count} requested)", file=sys.stderr)

    aggregates.update(cpp_repos)
    aggregates_path.write_text(json.dumps(aggregates, indent=0, sort_keys=True))
    (ROOT / "data/repo-lists/dependency_extra_repos.txt").write_text(
        "\n".join(sorted(aggregates)) + "\n")
    cpp_list_path = ROOT / "data/repo-lists/cpp_ecosystem_repos.txt"
    prev_cpp = set(cpp_list_path.read_text().split()) if cpp_list_path.exists() else set()
    cpp_list_path.write_text(
        "\n".join(sorted(prev_cpp | set(cpp_repo_names))) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:])
