#!/usr/bin/env python3
"""Grow the cohort with the repos a real dependency edge needs on both ends:

- "library" repos: distinct values of data/processed/package_to_repo.json
  (what a usedPackage triple's package resolves to), minus whatever's
  already in data/repo-lists/top50_repos.txt.
- "source" repos: the top-N dependency-cohort repos (data/raw/repo_package_degree.txt,
  built by counting distinct resolved packages per repo in data/raw/repo_packages.nt)
  -- a real "how plugged into this library ecosystem is this repo" signal.

The dependency-cohort repo IDs are inconsistently ordered (some are
`owner/repo`, some are backwards `repo/owner` -- e.g. `kvquant/squeezeailab`
404s, `SqueezeAILab/KVQuant` is the real repo; see NOTES.md), so every source
candidate is resolved by trying both orderings against the GitHub API and
keeping whichever exists. That resolution also naturally catches cases where
a "new" source repo turns out to already be one of the 51 (`dgl/dmlc` in the
dump is really `dmlc/dgl`, already in top50_repos.txt) -- those are folded
into the existing node instead of duplicated.

Stats: tried against the SemRepo dump first (free), gh api as fallback for
whatever's missing, every gh api response cached to data/raw/github_cache/
so reruns are free too.

Usage: SEMREPO_NT=/path/to/SemRepo.nt python3 10_fetch_new_repo_stats.py [top_n_source=100]
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from ntparse import parse_line, short_predicate, repo_name  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GITHUB_CACHE = ROOT / "data/raw/github_cache"
CANDIDATE_POOL_FACTOR = 2.2  # raw ids tried per desired source repo, to comfortably yield top_n_source after drops (dedup/404s/backwards-order misses)
CANDIDATE_POOL_MIN = 220
GH_API_THROTTLE_S = 0.4  # pause after every uncached gh api call -- large runs (thousands of
# candidates) hammer the endpoint fast enough to trip GitHub's secondary/abuse rate limit
# even while the primary 5000/hour quota is nowhere near exhausted

# The usedPackage/dependency-degree signal this stream ranks by is inherently
# PyPI-only, so a flat top-N cut just keeps taking the most-Python(/notebook)
# repos: a sample of everything resolved through 1019->1983 growth came out
# 83.2% Python + 12.1% Jupyter Notebook, vs. 69% Python in the original
# hand-curated 51-repo seed (ranked by language-agnostic hasTotalStargazers).
# Capping each language bucket forces the ranking to keep walking past
# top-ranked Python/notebook candidates and surface the non-Python repos that
# already exist further down the same dependency-degree ranking, instead of
# letting them get crowded out entirely.
LANGUAGE_QUOTA_CAP = 0.5  # a single language bucket may claim at most this fraction of a round's new source-repo slots
LANGUAGE_GROUPS = {"Jupyter Notebook": "Python"}  # notebook-heavy ML code is Python-family for quota purposes; capping them separately would let Python-family repos still fill ~100% of slots between the two buckets


def language_bucket(gh_data):
    lang = (gh_data or {}).get("language") or "Unknown"
    return LANGUAGE_GROUPS.get(lang, lang)


def load_top50():
    lines = (ROOT / "data/repo-lists/top50_repos.txt").read_text().splitlines()
    return {l.strip() for l in lines if l.strip()}


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


def resolve_source_id(raw_id):
    """Try raw_id as owner/repo, then reversed, against the GitHub API.
    Returns (canonical "owner/repo", api_data) or (None, None)."""
    if raw_id.count("/") != 1:
        return None, None
    a, b = raw_id.split("/")
    for owner, repo in ((a, b), (b, a)):
        data = gh_api_repo(owner, repo)
        if data:
            return data["full_name"], data
    return None, None


def dump_aggregates(nt_path, repo_ids):
    """Single-pass grep for hasTotal*/title triples over an arbitrary repo id
    list -- same approach as 03_repo_aggregates.sh, generalized to a list
    computed at runtime instead of a static repo_list.txt file."""
    if not repo_ids:
        return {}
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
        for rid in repo_ids:
            f.write(f"<https://semrepo.org/repository/{rid}>\n")
        patterns_path = f.name
    try:
        grep1 = subprocess.run(["grep", "-a", "-F", "-f", patterns_path, nt_path],
                                capture_output=True, text=True, check=False)
        grep2 = subprocess.run(["grep", "-a", "-E", "property/hasTotal|dc/terms/title"],
                                input=grep1.stdout, capture_output=True, text=True, check=False)
    finally:
        os.unlink(patterns_path)

    agg = {}
    for line in grep2.stdout.splitlines():
        parsed = parse_line(line)
        if not parsed:
            continue
        subj, pred, obj, _kind = parsed
        agg.setdefault(repo_name(subj), {})[short_predicate(pred)] = obj
    return agg


def aggregate_row(repo, dump_row, gh_data):
    a = dump_row or {}
    if a.get("hasTotalStargazers") is not None:
        return {
            "title": a.get("title", repo.split("/")[-1]),
            "stargazers": int(a["hasTotalStargazers"]) if "hasTotalStargazers" in a else None,
            "forks": int(a["hasTotalForks"]) if "hasTotalForks" in a else None,
            "openIssues": int(a["hasTotalOpenIssues"]) if "hasTotalOpenIssues" in a else None,
            "watchers": int(a["hasTotalWatchers"]) if "hasTotalWatchers" in a else None,
            "contributors": int(a["hasTotalContributor"]) if "hasTotalContributor" in a else None,
        }
    if gh_data:
        return {
            "title": gh_data.get("name", repo.split("/")[-1]),
            "stargazers": gh_data.get("stargazers_count"),
            "forks": gh_data.get("forks_count"),
            "openIssues": gh_data.get("open_issues_count"),
            "watchers": gh_data.get("subscribers_count"),
            "contributors": None,  # not on the repos endpoint; a real value would need another call
        }
    return None


def main(top_n_source="100"):
    nt_path = os.environ.get("SEMREPO_NT")
    if not nt_path:
        sys.exit("usage: SEMREPO_NT=/path/to/SemRepo.nt python3 10_fetch_new_repo_stats.py [top_n_source]")
    top_n_source = int(top_n_source)

    top50 = load_top50()
    top50_lower = {r.lower() for r in top50}

    # Canonicalize every PyPI-resolved repo through the GitHub API before
    # deduping: PyPI project_urls can point at an old slug (e.g. `mediapipe`
    # resolves to `google/mediapipe`, which GitHub now redirects to
    # `google-ai-edge/mediapipe` -- already one of the 51). Using the raw
    # PyPI-provided name would both miss that dedupe and mislabel the node.
    package_to_repo = json.loads((ROOT / "data/processed/package_to_repo.json").read_text())
    library_gh_data = {}
    for raw in sorted(set(package_to_repo.values())):
        owner, name = raw.split("/", 1)
        data = gh_api_repo(owner, name)
        if data:
            library_gh_data[data["full_name"]] = data
    library_repos = sorted({r for r in library_gh_data if r.lower() not in top50_lower})

    candidate_pool = max(CANDIDATE_POOL_MIN, int(top_n_source * CANDIDATE_POOL_FACTOR))
    degree_lines = (ROOT / "data/raw/repo_package_degree.txt").read_text().splitlines()
    candidates = [l.split(None, 1)[1] for l in degree_lines[:candidate_pool]]

    seen_lower = set(top50_lower) | {r.lower() for r in library_repos}
    source_repos = []
    source_id_map = {}
    language_counts = {}
    quota_cap = max(1, int(top_n_source * LANGUAGE_QUOTA_CAP))
    skipped_by_cap = 0
    for raw_id in candidates:
        canonical, gh_data = resolve_source_id(raw_id)
        if not canonical:
            continue
        # Record the id map entry even when this resolves to a repo we
        # already have (e.g. the dependency-cohort id `dgl/dmlc` turns out
        # to really be `dmlc/dgl`, already one of the 51) -- that's not a
        # new node, but it IS a real usedPackage source the edge-builder
        # should still attribute, so it shouldn't be silently dropped here.
        source_id_map[raw_id] = canonical
        if canonical.lower() in seen_lower:
            continue
        if len(source_repos) >= top_n_source:
            continue
        bucket = language_bucket(gh_data)
        if language_counts.get(bucket, 0) >= quota_cap:
            skipped_by_cap += 1
            continue
        seen_lower.add(canonical.lower())
        source_repos.append(canonical)
        language_counts[bucket] = language_counts.get(bucket, 0) + 1

    new_repos = sorted(set(library_repos) | set(source_repos))
    print(f"{len(library_repos)} new library repos, {len(source_repos)} new source repos "
          f"({len(candidates)} candidates tried, {skipped_by_cap} skipped by the "
          f"{int(LANGUAGE_QUOTA_CAP * 100)}% per-language cap) -> {len(new_repos)} total new nodes",
          file=sys.stderr)
    lang_summary = ", ".join(f"{lang}: {n} ({100 * n / max(1, len(source_repos)):.0f}%)"
                              for lang, n in sorted(language_counts.items(), key=lambda kv: -kv[1]))
    print(f"source repo languages this round -- {lang_summary}", file=sys.stderr)

    dump_agg = dump_aggregates(nt_path, new_repos)

    aggregates = {}
    missing = []
    for repo in new_repos:
        dump_row = dump_agg.get(repo)
        gh_data = library_gh_data.get(repo)
        if not dump_row or dump_row.get("hasTotalStargazers") is None:
            if not gh_data:
                owner, name = repo.split("/", 1)
                gh_data = gh_api_repo(owner, name)
        row = aggregate_row(repo, dump_row, gh_data)
        if row:
            aggregates[repo] = row
        else:
            missing.append(repo)

    out_dir = ROOT / "data/processed"
    (out_dir / "dependency_repo_aggregates.json").write_text(json.dumps(aggregates, indent=0, sort_keys=True))
    (ROOT / "data/repo-lists/dependency_extra_repos.txt").write_text("\n".join(sorted(aggregates)) + "\n")
    (out_dir / "dependency_source_id_map.json").write_text(json.dumps(source_id_map, indent=0, sort_keys=True))

    print(f"wrote {len(aggregates)} repos with real stats", file=sys.stderr)
    if missing:
        print(f"no stats found (dropped): {missing}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
