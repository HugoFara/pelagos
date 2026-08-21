#!/usr/bin/env python3
"""Pre-fetch and cache each C/C++-ecosystem repo's dependency manifests, raw
text.

C/C++ has no single registry manifest the way Cargo/npm/Go/Maven do, so
unlike go_mod.py/package_json.py/java_manifests.py/cargo_toml.py this looks
for four different files and keeps *every*
one that exists rather than stopping at the first hit -- they declare
genuinely different dependency sets (a repo can vendor via submodules AND
declare vcpkg ports), so first-hit-wins would silently drop real data. Same
fetch-all shape as scripts/fetch/python_manifests.py, which needed it for the
pyproject.toml/requirements.txt pair.

Four candidates x 1000 repos is 4000 throttled requests (~47 min) if each
is probed blind the way 37 probes its two. Instead this lists the repo root
once (`gh api repos/{o}/{n}/contents`, one call, returns every root
filename) and then fetches only the manifests that are actually there --
1000 listings + a few hundred content fetches rather than 4000 misses.
Matching against the listing is case-insensitive, so a `Conanfile.py` is
still found; no other root file can collide with these four names.

Measured coverage across 40 major C/C++ repos before writing this:

    .gitmodules       9/40  (22%)   literal GitHub URLs, no registry needed
    conanfile.py      3/40  ( 7%)   package names -> conan-center-index
    vcpkg.json        2/40  ( 5%)   port names -> microsoft/vcpkg ports
    conanfile.txt     0/40  ( 0%)   kept anyway; it's the documented plain-text
                                    form of the same file and costs one probe

CMakeLists.txt (65%) is deliberately NOT fetched. It is by far the most
common file, but it is a build script, not a manifest: 16 of the 25 sampled
use `find_package(Foo)`, which names a system-provided CMake target with no
registry coordinate and no repo behind it, and only 1 of 25 carried a
literal `FetchContent_Declare(... GIT_REPOSITORY https://github.com/...)`
at repo root. Fetching 1000 CMakeLists.txt files to resolve one edge, with
the standing temptation to guess a repo from a find_package name, is a bad
trade -- see scripts/edges/cpp_deps.py.

Cached raw to data/raw/cpp_manifest_cache/{owner}__{name}.{kind}. A repo
with none of the four gets a zero-byte {owner}__{name}.none marker so a
re-run doesn't re-request all four -- same convention as the README /
go.mod / package.json / java-manifest / Cargo.toml / python-manifest caches.

Usage: python3 scripts/fetch/cpp_manifests.py
"""
import base64
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CPP_MANIFEST_CACHE = ROOT / "data/raw/cpp_manifest_cache"
GH_API_THROTTLE_S = 0.4  # see scripts/cohort/dependency_repos.py

# All four are probed and all present ones kept -- see module docstring.
MANIFEST_KINDS = [".gitmodules", "vcpkg.json", "conanfile.py", "conanfile.txt"]


def load_cpp_repos():
    return sorted(set(
        l.strip() for l in
        (ROOT / "data/repo-lists/cpp_ecosystem_repos.txt").read_text().splitlines()
        if l.strip()
    ))


def cached_kinds(owner, name):
    """Which manifest kinds are already on disk for this repo: a list of
    kinds, ["none"] for the cached real-absence marker, or [] for never
    fetched."""
    found = [kind for kind in MANIFEST_KINDS
             if (CPP_MANIFEST_CACHE / f"{owner}__{name}{kind_suffix(kind)}").exists()]
    if found:
        return found
    if (CPP_MANIFEST_CACHE / f"{owner}__{name}.none").exists():
        return ["none"]
    return []


def kind_suffix(kind):
    # ".gitmodules" already starts with a dot; the others need one added so
    # the cache filename stays {owner}__{name}.{kind}.
    return kind if kind.startswith(".") else f".{kind}"


def root_listing(owner, name):
    """Every filename at the repo root, or None if the listing failed
    (empty repo, renamed/deleted since the search that found it)."""
    out = subprocess.run(
        ["gh", "api", f"repos/{owner}/{name}/contents"], capture_output=True, text=True
    )
    time.sleep(GH_API_THROTTLE_S)
    if out.returncode != 0:
        return None
    try:
        entries = json.loads(out.stdout)
    except ValueError:
        return None
    if not isinstance(entries, list):
        return None
    return [e.get("name") for e in entries
            if isinstance(e, dict) and e.get("type") == "file" and e.get("name")]


def fetch_one(owner, name, filename):
    out = subprocess.run(
        ["gh", "api", f"repos/{owner}/{name}/contents/{filename}"], capture_output=True, text=True
    )
    time.sleep(GH_API_THROTTLE_S)
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except ValueError:
        return None
    return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")


def fetch_cpp_manifests(owner, name):
    """(kinds actually present, whether this run hit the network)."""
    cached = cached_kinds(owner, name)
    if cached:
        return ([] if cached == ["none"] else cached), False

    CPP_MANIFEST_CACHE.mkdir(parents=True, exist_ok=True)
    names = root_listing(owner, name)
    # Case-insensitive so a `Conanfile.py` still matches; the real filename
    # from the listing is what gets requested, the canonical kind is what
    # gets cached.
    by_lower = {n.lower(): n for n in (names or [])}

    found = []
    for candidate in MANIFEST_KINDS:
        actual = by_lower.get(candidate.lower())
        if not actual:
            continue
        raw = fetch_one(owner, name, actual)
        if raw is None:
            continue
        (CPP_MANIFEST_CACHE / f"{owner}__{name}{kind_suffix(candidate)}").write_text(
            raw, encoding="utf-8")
        found.append(candidate)

    if not found:
        # Covers both "root listed fine, none of the four are there" and
        # "listing failed" -- either way there is nothing to parse, and the
        # marker stops a re-run from re-requesting it.
        (CPP_MANIFEST_CACHE / f"{owner}__{name}.none").write_text("")
    return found, True


def main():
    repos = load_cpp_repos()
    fetched = 0
    none_count = 0
    multi = 0
    kind_counts = Counter()
    for repo in repos:
        owner, name = repo.split("/", 1)
        kinds, did_fetch = fetch_cpp_manifests(owner, name)
        if did_fetch:
            fetched += 1
        if not kinds:
            none_count += 1
            continue
        kind_counts.update(kinds)
        if len(kinds) > 1:
            multi += 1
    breakdown = ", ".join(f"{kind_counts[k]} {k}" for k in MANIFEST_KINDS if kind_counts[k])
    print(
        f"{len(repos)} C/C++-ecosystem repos, {fetched} fetched fresh this run, "
        f"{breakdown or 'no manifests at all'} ({multi} have more than one), "
        f"{none_count} have none of the four (0-byte marker cached, real absence)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
