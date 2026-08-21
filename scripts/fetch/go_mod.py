#!/usr/bin/env python3
"""Pre-fetch and cache each Go-ecosystem repo's go.mod, raw text.

Go repos (scripts/cohort/go_ecosystem.py) currently have zero
dependency edges: scripts/edges/dependency_edges.py's usedPackage signal comes from the
SemRepo dump, which is PyPI-only. go.mod's `require` block is a plain-text,
already-fully-declared dependency list checked into every Go repo at a fixed
path -- no registry API guessing needed the way PyPI package names needed
resolving to a GitHub repo (scripts/edges/resolve_pypi_packages.py). See
scripts/edges/go_deps.py for the parsing/resolution that turns this into
real repo-repo edges.

Unlike scripts/fetch/repo_readmes.py's `repos/{o}/{r}/readme` (which finds
whichever README variant exists), go.mod always lives at exactly
`go.mod` at repo root by Go module convention, so a plain contents-API fetch
for that literal path is enough -- no well-known-file resolution needed.

Cached raw to data/raw/go_mod_cache/{owner}__{name}.mod. A repo with no
go.mod (not actually a Go module, or Go isn't its primary build system
despite the `language:go` search match, e.g. a mixed-language monorepo) gets
a zero-byte marker file so a re-run doesn't re-request it -- same convention
as the README cache.

Usage: python3 scripts/fetch/go_mod.py
"""
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GO_MOD_CACHE = ROOT / "data/raw/go_mod_cache"
GH_API_THROTTLE_S = 0.4  # see scripts/cohort/dependency_repos.py


def load_go_repos():
    return sorted(set(
        l.strip() for l in
        (ROOT / "data/repo-lists/go_ecosystem_repos.txt").read_text().splitlines()
        if l.strip()
    ))


def fetch_go_mod(owner, name):
    cache_path = GO_MOD_CACHE / f"{owner}__{name}.mod"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace"), False

    out = subprocess.run(
        ["gh", "api", f"repos/{owner}/{name}/contents/go.mod"], capture_output=True, text=True
    )
    time.sleep(GH_API_THROTTLE_S)
    GO_MOD_CACHE.mkdir(parents=True, exist_ok=True)
    if out.returncode != 0:
        cache_path.write_text("")  # no go.mod at repo root (real, not an error)
        return "", True

    data = json.loads(out.stdout)
    raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    cache_path.write_text(raw, encoding="utf-8")
    return raw, True


def main():
    repos = load_go_repos()
    fetched = 0
    empty = 0
    for repo in repos:
        owner, name = repo.split("/", 1)
        was_cached = (GO_MOD_CACHE / f"{owner}__{name}.mod").exists()
        text, did_fetch = fetch_go_mod(owner, name)
        if did_fetch and not was_cached:
            fetched += 1
        if not text.strip():
            empty += 1
    print(
        f"{len(repos)} Go-ecosystem repos, {fetched} fetched fresh this run, "
        f"{empty} have no go.mod at repo root (0-byte marker cached, real absence)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
