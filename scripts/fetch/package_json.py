#!/usr/bin/env python3
"""Pre-fetch and cache each JS-ecosystem repo's package.json, raw text.

JS/TS repos (scripts/cohort/js_ecosystem.py) currently have zero
dependency edges: scripts/edges/dependency_edges.py's usedPackage signal comes from the
SemRepo dump, which is PyPI-only. package.json's `dependencies` block is a
plain-text, already-fully-declared runtime-dependency list checked into every
npm package at a fixed path -- same shape of gap go.mod closed for Go (see
scripts/edges/go_deps.py), just resolved via the npm registry instead of
Go's github.com/*-or-go-import split.

Cached raw to data/raw/package_json_cache/{owner}__{name}.json. A repo with
no package.json at root (not actually an npm package, or a non-JS-tooled
monorepo despite the language:javascript/typescript search match) gets a
zero-byte marker file so a re-run doesn't re-request it -- same convention as
the README and go.mod caches.

Usage: python3 scripts/fetch/package_json.py
"""
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_JSON_CACHE = ROOT / "data/raw/package_json_cache"
GH_API_THROTTLE_S = 0.4  # see scripts/cohort/dependency_repos.py


def load_js_repos():
    return sorted(set(
        l.strip() for l in
        (ROOT / "data/repo-lists/js_ecosystem_repos.txt").read_text().splitlines()
        if l.strip()
    ))


def fetch_package_json(owner, name):
    cache_path = PACKAGE_JSON_CACHE / f"{owner}__{name}.json"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace"), False

    out = subprocess.run(
        ["gh", "api", f"repos/{owner}/{name}/contents/package.json"], capture_output=True, text=True
    )
    time.sleep(GH_API_THROTTLE_S)
    PACKAGE_JSON_CACHE.mkdir(parents=True, exist_ok=True)
    if out.returncode != 0:
        cache_path.write_text("")  # no package.json at repo root (real, not an error)
        return "", True

    data = json.loads(out.stdout)
    raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    cache_path.write_text(raw, encoding="utf-8")
    return raw, True


def main():
    repos = load_js_repos()
    fetched = 0
    empty = 0
    for repo in repos:
        owner, name = repo.split("/", 1)
        was_cached = (PACKAGE_JSON_CACHE / f"{owner}__{name}.json").exists()
        text, did_fetch = fetch_package_json(owner, name)
        if did_fetch and not was_cached:
            fetched += 1
        if not text.strip():
            empty += 1
    print(
        f"{len(repos)} JS-ecosystem repos, {fetched} fetched fresh this run, "
        f"{empty} have no package.json at repo root (0-byte marker cached, real absence)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
