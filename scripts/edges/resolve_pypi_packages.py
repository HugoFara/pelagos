#!/usr/bin/env python3
"""Resolve the distinct PyPI package names behind usedPackage triples to the
GitHub repo that publishes them, turning `repo --uses--> package` into
`repo --depends on--> repo` (see NOTES.md's long-open "what should the main
repo-repo edge be" question).

Only 186 distinct packages back the 95,505 usedPackage triples in the
2025-05-11 dump, so this is a small, one-time PyPI JSON API lookup, not a
per-repo job. Every response is cached to data/raw/pypi_cache/{package}.json
so reruns are free and offline.

Usage: SEMREPO_NT=/path/to/SemRepo.nt python3 scripts/edges/resolve_pypi_packages.py \
    [pypi_cache_dir=data/raw/pypi_cache] [out=data/processed/package_to_repo.json]
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GITHUB_RE = re.compile(r'https?://github\.com/([^/\s#]+)/([^/\s#?]+?)(?:\.git)?/?(?:#.*)?$')
URL_KEYS = ("Repository", "Source", "Source Code", "Code", "GitHub", "Homepage", "Home")


def distinct_packages(nt_path):
    # A plain fixed-string grep first (fast even over the full 12GB dump),
    # then -oP only on the much smaller matching subset -- running -oP with a
    # lookbehind/lookahead directly over the whole file is dramatically
    # slower than this two-pass filter (same reasoning as the bash scripts,
    # e.g. scripts/extract/repos_with_packages.sh, which never run -P over the raw dump).
    narrowed = subprocess.run(
        ["grep", "-a", "property/usedPackage>", nt_path],
        capture_output=True, text=True, check=False,
    ).stdout
    out = subprocess.run(
        ["grep", "-oP", r'(?<=<https://semrepo\.org/package/)[^>]+(?=>)'],
        input=narrowed, capture_output=True, text=True, check=False,
    ).stdout
    return sorted(set(out.splitlines()))


def github_owner_repo(url):
    if not url:
        return None
    m = GITHUB_RE.match(url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if owner.lower() in ("orgs", "sponsors", "apps", "marketplace"):
        return None
    return f"{owner}/{repo}"


def resolve_from_pypi_json(data):
    info = data.get("info", {})
    project_urls = info.get("project_urls") or {}
    for key in URL_KEYS:
        for actual_key, url in project_urls.items():
            if actual_key.strip().lower() == key.lower():
                resolved = github_owner_repo(url)
                if resolved:
                    return resolved
    # Fall back to any project_urls value, then home_page/download_url.
    for url in project_urls.values():
        resolved = github_owner_repo(url)
        if resolved:
            return resolved
    return github_owner_repo(info.get("home_page")) or github_owner_repo(info.get("download_url"))


def fetch_pypi_json(package, cache_dir):
    cache_path = cache_dir / f"{package}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    url = f"https://pypi.org/pypi/{package}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "pelagos-package-resolve"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        data = {"_error": f"{exc.code} {exc.reason}"}
    cache_path.write_text(json.dumps(data))
    time.sleep(0.1)
    return data


def main(nt_path=None, cache_dir="data/raw/pypi_cache", out_path="data/processed/package_to_repo.json"):
    # data/raw/repo_packages.nt is the already-narrowed usedPackage subset
    # (grep 'property/usedPackage>' over the dump, cached once); use it
    # instead of re-scanning the full 12GB dump if it's already there.
    cached_subset = Path("data/raw/repo_packages.nt")
    nt_path = nt_path or os.environ.get("SEMREPO_NT")
    if not nt_path and not cached_subset.exists():
        sys.exit("usage: SEMREPO_NT=/path/to/SemRepo.nt python3 scripts/edges/resolve_pypi_packages.py "
                 "[pypi_cache_dir] [out]")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    packages = distinct_packages(str(cached_subset) if cached_subset.exists() else nt_path)
    mapping = {}
    unresolved = []
    for package in packages:
        data = fetch_pypi_json(package, cache_dir)
        if "_error" in data:
            unresolved.append((package, data["_error"]))
            continue
        resolved = resolve_from_pypi_json(data)
        if resolved:
            mapping[package] = resolved
        else:
            unresolved.append((package, "no github url"))

    Path(out_path).write_text(json.dumps(mapping, indent=0, sort_keys=True))
    print(f"resolved {len(mapping)}/{len(packages)} packages to GitHub repos -> {out_path}", file=sys.stderr)
    if unresolved:
        print(f"unresolved ({len(unresolved)}): {unresolved}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
