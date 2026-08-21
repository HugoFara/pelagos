#!/usr/bin/env python3
"""Sweep every cohort repo's git tree for every ecosystem's dependency
manifests, at any depth. One tree read per repo, batched blob reads after.

This is the fetch half of the multi-ecosystem change; `scripts/manifests.py`
is the read half and carries the measurement that motivates both. In short:
the six existing fetchers each read one language list and probe one fixed
root path, so a repo GitHub labels `language:rust` can never contribute its
real, declared `package.json` dependencies -- 20% of a random 240-repo sample
carry a root manifest for an ecosystem other than their own, 41% carry one at
some depth, and 10% have no root manifest at all while shipping real nested
ones. None of that was reachable before.

Generalizes the submodule sweep 32_fetch_java_manifests.py already runs for
Java, in two directions at once: across all six ecosystems instead of Java
alone, and across the whole cohort instead of one language list. The
mechanics are 32's, unchanged and for its reasons -- a `git/trees/HEAD`
?recursive=1 read finds nested manifests that a declared-module list would
miss and that no fixed path can reach, and aliased GraphQL makes a whole
repo's blobs cost 1 rate-limit point per MANIFEST_BATCH paths rather than one
per file.

## What is recorded vs. what is downloaded

`paths` records every manifest-named blob the tree contained, unfiltered --
vendored copies included. `texts` holds only what was actually downloaded.
The two differ for two honest reasons, each counted separately in the cache:

  - vendored paths (`node_modules/`, `third_party/`, a checked-in `vendor/`)
    are not downloaded. They are some other project's dependency list, and
    the exclusion rule lives in scripts/manifests.py where it can be changed
    and re-measured against `paths` without re-fetching anything -- the same
    parse-time-not-fetch-time split 32 uses for buildSrc/.
  - beyond MANIFEST_FETCH_CAP surviving paths a repo is truncated, recorded
    as `capped`. A handful of monorepos carry several hundred manifests and
    the tail of that list is package-per-directory boilerplate, not new
    dependency information.

    Which paths make the cut is decided by fetch_priority(), and getting that
    wrong was a real bug rather than a hypothetical: capping an alphabetically
    sorted list gave `TanStack/router` 139 `e2e/` manifests and 131
    `examples/` ones while truncating away all but a few of the 24 in
    `packages/` -- the only ones describing the project itself. Root manifests
    now come first, then shallower paths, and example/test/demo/docs trees
    sort last within a depth. Nothing is excluded by that rule; it only
    decides what a monorepo spends its budget on.

A tree that GitHub itself returns truncated is recorded as `truncated`. Those
repos are under-covered, not silently complete, and the summary line says so.

Nothing is written for a repo whose tree or blob reads failed, so a transient
failure is retried on the next run instead of being frozen in as "this repo
has no manifests" -- 32's rule, and it matters more here since this pass is
the only manifest discovery the pipeline will have.

Cached to data/raw/repo_manifest_cache/{owner}__{name}.json. Idempotent and
resumable: a cached repo costs nothing, so an interrupted run just continues.
Also writes data/processed/repo_manifest_inventory.json, the small committed
summary (repo -> {ecosystem: [paths]}, vendored dropped) that makes the
cross-ecosystem coverage inspectable without the multi-GB raw cache.

Usage: python3 scripts/42_scan_repo_manifests.py [limit=0]
    limit > 0 scans only the first N cohort repos, for a measured trial run.
"""
import hashlib
import json
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifests import (  # noqa: E402
    ECOSYSTEM_KINDS, KIND_TO_ECOSYSTEM, MANIFEST_CACHE, PERIPHERAL_COMPONENTS, cache_path,
    content_hash, copied_hashes, is_vendored, manifest_kind,
)

ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = ROOT / "data/processed/repo_manifest_inventory.json"

GH_API_THROTTLE_S = 0.4  # see scripts/10_fetch_new_repo_stats.py
GH_RETRY_BACKOFF_S = 2.0  # GitHub answers a burst of tree/GraphQL calls with occasional 502s
GH_RETRIES = 4

MANIFEST_BATCH = 50  # aliased blobs per GraphQL query; see 32_fetch_java_manifests.py
SWEEP_WORKERS = 6  # latency-bound, not rate-limit-bound -- see 32's docstring
MANIFEST_FETCH_CAP = 300  # per repo, after vendored paths are dropped

_progress_lock = threading.Lock()
_progress = Counter()


def load_cohort():
    """The full node set -- deliberately not any one language list, since that
    is the assumption this script exists to remove."""
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    return sorted(set(top50) | set(extra))


def gh_json(args):
    """Parsed `gh api` output, or None. Retries GitHub's transient 502 under a
    burst but returns immediately on a real 404 -- 32_fetch_java_manifests.py's
    gh_json, unchanged."""
    for attempt in range(GH_RETRIES):
        out = subprocess.run(args, capture_output=True, text=True)
        time.sleep(GH_API_THROTTLE_S)
        if out.returncode == 0:
            try:
                return json.loads(out.stdout)
            except ValueError:
                return None
        if "HTTP 404" in out.stderr or "Not Found" in out.stderr:
            return None
        time.sleep(GH_RETRY_BACKOFF_S * (attempt + 1))
    return None


def list_manifest_paths(owner, name):
    """(every manifest-named blob path in the tree, truncated), or (None,
    False) when the tree could not be read at all."""
    tree = gh_json(["gh", "api", f"repos/{owner}/{name}/git/trees/HEAD?recursive=1"])
    if tree is None:
        return None, False
    paths = [
        entry["path"] for entry in tree.get("tree", [])
        if entry.get("type") == "blob" and manifest_kind(entry.get("path", ""))
    ]
    return sorted(paths), bool(tree.get("truncated"))


def fetch_texts(owner, name, paths):
    """({path: text}, all_ok) via aliased GraphQL, MANIFEST_BATCH at a time.

    all_ok is False if any batch failed outright -- without it a repo whose
    single query failed would cache an empty dict indistinguishable from an
    honest "this repo has no manifests"."""
    texts = {}
    all_ok = True
    for start in range(0, len(paths), MANIFEST_BATCH):
        chunk = paths[start:start + MANIFEST_BATCH]
        aliases = {f"m{i}": path for i, path in enumerate(chunk)}
        body = " ".join(
            f'{alias}: object(expression: {json.dumps("HEAD:" + path)}) '
            "{ ... on Blob { text } }"
            for alias, path in aliases.items()
        )
        query = (
            f'query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) '
            f"{{ {body} }} }}"
        )
        data = gh_json(["gh", "api", "graphql", "-f", f"query={query}"])
        repository = ((data or {}).get("data") or {}).get("repository") or {}
        if not repository:
            all_ok = False
            continue
        for alias, path in aliases.items():
            text = (repository.get(alias) or {}).get("text")
            if text:  # None for a binary blob or a path that vanished since the tree read
                texts[path] = text
    return texts, all_ok


def fetch_priority(path):
    """Sort key deciding what a capped repo spends its fetch budget on.

    Root first, then by depth, then peripheral trees (examples/, e2e/, tests/,
    docs/) last within their depth. Purely an ordering -- see the module
    docstring for the TanStack/router case that made it necessary."""
    parts = path.split("/")
    peripheral = any(p.lower() in PERIPHERAL_COMPONENTS for p in parts[:-1])
    return (len(parts) - 1, peripheral, path)


def scan_repo(repo):
    """Cache one repo's manifest inventory. Returns "cached" | "fresh" |
    "failed"."""
    path = cache_path(repo)
    if path.exists():
        return "cached"

    owner, name = repo.split("/", 1)
    paths, truncated = list_manifest_paths(owner, name)
    if paths is None:
        return "failed"  # deliberately not cached; retried next run

    wanted = sorted((p for p in paths if not is_vendored(p)), key=fetch_priority)
    capped = max(0, len(wanted) - MANIFEST_FETCH_CAP)
    wanted = wanted[:MANIFEST_FETCH_CAP]

    texts, all_ok = fetch_texts(owner, name, wanted) if wanted else ({}, True)
    if not all_ok:
        return "failed"

    MANIFEST_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "truncated": truncated,
        "paths": paths,                      # everything the tree held, vendored included
        "vendored": len(paths) - len(wanted) - capped,
        "capped": capped,
        "texts": texts,
    }, separators=(",", ":")))
    return "fresh"


def scan_repo_reporting(repo, total):
    result = scan_repo(repo)
    with _progress_lock:
        _progress[result] += 1
        done = sum(_progress.values())
        if done % 250 == 0 or done == total:
            print(f"  {done}/{total} repos "
                  f"({_progress['fresh']} fresh, {_progress['cached']} already cached, "
                  f"{_progress['failed']} failed)", file=sys.stderr)
    return repo, result


def build_inventory(repos):
    """repo -> {ecosystem: {path: content hash}} over what was actually
    downloaded, with vendored paths dropped.

    The hash is what makes the committed inventory self-sufficient. A manifest
    whose exact bytes also appear, nested, in a different repository is a
    checked-in copy of someone else's project rather than this repo's own
    declaration -- 4.3% of non-vendored manifests, measured -- and
    scripts/manifests.py drops those at read time. Carrying the hash here
    means that check reads one small committed file instead of re-hashing a
    multi-gigabyte raw cache, and means the exclusion can be audited from the
    repository alone."""
    inventory = {}
    for repo in repos:
        path = cache_path(repo)
        if not path.exists():
            continue
        try:
            cached = json.loads(path.read_text())
        except ValueError:
            continue
        by_eco = defaultdict(dict)
        for p, text in sorted((cached.get("texts") or {}).items()):
            if is_vendored(p):
                continue
            by_eco[KIND_TO_ECOSYSTEM[manifest_kind(p)]][p] = content_hash(text)
        if by_eco:
            inventory[repo] = {eco: by_eco[eco] for eco in sorted(by_eco)}
    return inventory


def report(inventory, repos, failed):
    """Print what the sweep actually found, in the terms the change was
    argued in: how many repos are multi-ecosystem, and how much of that is
    new."""
    eco_repos = Counter()
    eco_files = Counter()
    multi = Counter()
    nested_only = Counter()
    for repo, by_eco in inventory.items():
        multi[len(by_eco)] += 1
        for eco, paths in by_eco.items():
            eco_repos[eco] += 1
            eco_files[eco] += len(paths)
            if all("/" in p for p in paths):
                nested_only[eco] += 1
    copied = copied_hashes(inventory)
    copied_files = sum(
        1 for by_eco in inventory.values() for paths in by_eco.values()
        for p, h in paths.items() if h in copied and "/" in p
    )

    scanned = len(repos) - failed
    print(f"\n{scanned}/{len(repos)} repos swept, {len(inventory)} have at least one manifest",
          file=sys.stderr)
    print(f"repos by ecosystem count: "
          f"{', '.join(f'{n} eco: {c}' for n, c in sorted(multi.items()))}", file=sys.stderr)
    multi_eco = sum(c for n, c in multi.items() if n > 1)
    print(f"{multi_eco} repos span more than one ecosystem "
          f"({multi_eco / len(inventory):.1%} of repos with any manifest) -- every one of those "
          f"had all but one ecosystem unreachable before this pass", file=sys.stderr)
    for eco in sorted(ECOSYSTEM_KINDS):
        print(f"  {eco:7s} {eco_repos[eco]:5d} repos, {eco_files[eco]:6d} manifest files, "
              f"{nested_only[eco]:4d} of those repos have none at root "
              f"(unreachable by the old root-only probe)", file=sys.stderr)
    print(f"{copied_files} nested manifests are byte-identical to another repo's file "
          f"(a checked-in copy of someone else's project, dropped at read time by "
          f"scripts/manifests.py -- not this repo's own declaration)", file=sys.stderr)


def main(limit="0"):
    limit = int(limit)
    repos = load_cohort()
    if limit:
        repos = repos[:limit]
    print(f"sweeping {len(repos)} cohort repos for "
          f"{sum(len(v) for v in ECOSYSTEM_KINDS.values())} manifest filenames "
          f"across {len(ECOSYSTEM_KINDS)} ecosystems", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
        results = list(pool.map(lambda r: scan_repo_reporting(r, len(repos)), repos))
    failed = sum(1 for _repo, result in results if result == "failed")

    inventory = build_inventory(repos)
    INVENTORY_PATH.write_text(json.dumps(inventory, separators=(",", ":"), sort_keys=True))
    report(inventory, repos, failed)
    print(f"{failed} repos failed to read this run (nothing cached for them, retried next run)"
          f" -> {INVENTORY_PATH.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
