#!/usr/bin/env python3
"""Pre-fetch and cache each Java-ecosystem repo's build manifest, raw text.

Java repos (scripts/cohort/java_ecosystem.py) currently have zero
dependency edges, the same gap go.mod closed for Go (go_mod.py/go_deps.py)
and package.json closed for JS (package_json.py/js_deps.py). Java has no single canonical
manifest filename though -- checked directly against 10 real, popular Java
repos (spring-boot, elasticsearch, kafka, okhttp, guava, dubbo, fastjson2,
mybatis-3, netty, rocketmq): roughly half use Maven (`pom.xml` at repo root)
and half use Gradle (`build.gradle` or, increasingly, `build.gradle.kts`).
So each repo needs up to three contents-API probes, tried in that priority
order, first hit wins -- unlike go.mod/package.json's single fixed path, but
still no build-tool invocation needed, just a few extra cached GETs for the
repos where the first guess 404s.

Cached raw to data/raw/java_manifest_cache/{owner}__{name}.{kind}, where kind
is whichever of pom.xml/build.gradle/build.gradle.kts actually existed -- the
extension itself records which build system the repo uses, so
scripts/edges/java_deps.py knows how to parse it without re-probing. A repo
with none of the three (not actually Maven/Gradle-built, or dependencies
declared some other way despite the language:java search match) gets a
zero-byte {owner}__{name}.none marker so a re-run doesn't re-probe any of the
three paths -- same convention as the README/go.mod/package.json caches.

SECOND PASS: submodule manifests. The root file alone is not enough for Java
the way go.mod and Cargo.toml are enough for Go and Rust, and this was
measured, not assumed -- running java_deps.py's own parser over all 811 cached root
manifests, only 192 yield a single dependency, and the reason is visible in
the files that yield none:

    185 of 196 zero-dependency pom.xml   contain <modules>     (aggregator POM)
    331 of 423 zero-dependency gradle    contain allprojects   (multi-project root)

Both are the same shape: a root file that lists child projects and configures
them, while every real `implementation`/`<dependency>` line lives one
directory down in `<module>/build.gradle` or `<module>/pom.xml`. That is not a
parser bug and not a Java-specific quirk of ours -- it is the normal layout of
a Maven/Gradle multi-project build, and reading only the root is the same
root-only scoping 31 accepts for JS workspaces, except that in Java it is the
majority case rather than a minority one.

Discovery is one `git/trees/HEAD?recursive=1` request per repo. That was
chosen over reading the declared module list (a POM's <modules>, a
settings.gradle `include`) because the tree is a single request, finds nested
modules that a one-level <modules> read would miss, and cannot be defeated by
a settings.gradle that builds its include list dynamically. Fetching the
files it finds is one *batched* GraphQL query per MODULE_BATCH paths, using
aliases -- checked directly at the real batch size: a query with 50 aliased
blobs costs 1 rate-limit point, exactly what a single-blob query costs, so a
524-module repo like apache/camel costs ~11 requests total instead of 525.

Cached to data/raw/java_module_manifest_cache/{owner}__{name}.json as a plain
{path: text} object. An empty object is a real, cached answer ("swept the
tree, no submodule manifests"), the JSON analogue of the zero-byte marker --
so a re-run re-requests nothing. Every repo in the list is swept, including
the 189 with no root manifest at all: "no pom.xml at root" and "no manifests
anywhere" are different facts, and only the sweep can tell them apart.

Nothing is filtered here. buildSrc/ and other build-logic directories are
cached like any other path and excluded at *parse* time by
scripts/edges/java_deps.py, so that exclusion can be measured and changed
without re-fetching a single file.

The sweep is the one fetch pass in this project that runs threaded, and the
reason is measured rather than assumed: timed end-to-end, a single repo costs
~9s, of which the throttle is 0.8s -- the rest is plain round-trip latency to
GitHub. Serially that is ~2.6 hours for 1000 repos while using barely a third
of the rate limit. Rate limit is genuinely not the binding constraint here,
and the two halves don't even share a pool: tree reads are REST core (1000
requests against 5000/hour) and blob reads are GraphQL (~1000 queries against
a separate 5000/hour, since aliasing makes a whole repo cost 1 point). Repos
are independent and each writes only its own cache file, so SWEEP_WORKERS
threads is a safe ~6x, still leaving both pools around a third used.

Usage: python3 scripts/fetch/java_manifests.py
"""
import base64
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JAVA_MANIFEST_CACHE = ROOT / "data/raw/java_manifest_cache"
JAVA_MODULE_CACHE = ROOT / "data/raw/java_module_manifest_cache"
GH_API_THROTTLE_S = 0.4  # see scripts/cohort/dependency_repos.py
GH_RETRY_BACKOFF_S = 2.0  # GitHub answers a burst of tree/GraphQL calls with occasional 502s
GH_RETRIES = 4

MANIFEST_KINDS = ["pom.xml", "build.gradle", "build.gradle.kts"]

# Aliased blobs per GraphQL query. The rate-limit cost is 1 per query whatever
# this is, so the only reason to bound it is response size -- 50 whole pom.xml
# bodies in one JSON response is already a few MB for the largest repos.
MODULE_BATCH = 50
SWEEP_WORKERS = 6  # see module docstring: latency-bound, not rate-limit-bound


def load_java_repos():
    return sorted(set(
        l.strip() for l in
        (ROOT / "data/repo-lists/java_ecosystem_repos.txt").read_text().splitlines()
        if l.strip()
    ))


def cached_manifest(owner, name):
    """(text, kind) from whichever cache file already exists, or (None, None)."""
    for kind in MANIFEST_KINDS + ["none"]:
        cache_path = JAVA_MANIFEST_CACHE / f"{owner}__{name}.{kind}"
        if cache_path.exists():
            if kind == "none":
                return "", "none"
            return cache_path.read_text(encoding="utf-8", errors="replace"), kind
    return None, None


def fetch_java_manifest(owner, name):
    """(text, kind, did_fetch). kind is None when no manifest exists at all."""
    text, kind = cached_manifest(owner, name)
    if kind is not None:
        return text, (None if kind == "none" else kind), False

    for candidate in MANIFEST_KINDS:
        out = subprocess.run(
            ["gh", "api", f"repos/{owner}/{name}/contents/{candidate}"],
            capture_output=True, text=True,
        )
        time.sleep(GH_API_THROTTLE_S)
        if out.returncode == 0:
            data = json.loads(out.stdout)
            raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
            JAVA_MANIFEST_CACHE.mkdir(parents=True, exist_ok=True)
            (JAVA_MANIFEST_CACHE / f"{owner}__{name}.{candidate}").write_text(raw, encoding="utf-8")
            return raw, candidate, True

    JAVA_MANIFEST_CACHE.mkdir(parents=True, exist_ok=True)
    (JAVA_MANIFEST_CACHE / f"{owner}__{name}.none").write_text("")
    return "", None, True


def gh_json(args):
    """Parsed `gh api` output, or None. Retries the transient 502 GitHub
    returns under a burst of tree/GraphQL calls ("No server is currently
    available to service your request" -- hit repeatedly while measuring this
    pass), but returns immediately on a real 404 so an absent tree costs one
    request rather than four."""
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


def module_cache_path(owner, name):
    return JAVA_MODULE_CACHE / f"{owner}__{name}.json"


def list_module_manifests(owner, name):
    """(sorted non-root manifest paths, tree_was_truncated), or (None, False)
    when the tree could not be read at all -- which is deliberately NOT cached,
    so a transient failure is retried on the next run instead of being frozen
    in as "this repo has no modules"."""
    tree = gh_json(["gh", "api", f"repos/{owner}/{name}/git/trees/HEAD?recursive=1"])
    if tree is None:
        return None, False
    paths = [
        entry["path"] for entry in tree.get("tree", [])
        if entry.get("type") == "blob"
        and "/" in entry.get("path", "")
        and entry["path"].rsplit("/", 1)[-1] in MANIFEST_KINDS
    ]
    return sorted(paths), bool(tree.get("truncated"))


def fetch_module_texts(owner, name, paths):
    """({path: text} for the paths GitHub returns a text blob for, all_ok),
    fetched MODULE_BATCH at a time through aliased GraphQL. Paths are embedded
    with json.dumps so a quote or backslash in a path can't break out of the
    GraphQL string literal.

    all_ok is False if any batch failed outright. That matters more than it
    looks: a repo with one batch and a failed query would otherwise produce an
    empty dict indistinguishable from an honest "this repo has no submodules",
    and get cached as such forever."""
    texts = {}
    all_ok = True
    for start in range(0, len(paths), MODULE_BATCH):
        chunk = paths[start:start + MODULE_BATCH]
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


def fetch_java_modules(owner, name):
    """(module_count, did_fetch, truncated).

    Cached as {"truncated": bool, "paths": [...], "texts": {path: text}}.
    `paths` is what the tree said exists and `texts` is what came back, so a
    reader can tell an honest empty sweep from a short one -- an empty `texts`
    next to an empty `paths` means "no submodules", while `texts` shorter than
    `paths` means those specific blobs had no text (binary, or deleted between
    the two calls). Nothing is written at all unless every batch succeeded, so
    a transient failure is retried next run rather than frozen in."""
    cache_path = module_cache_path(owner, name)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            return len(cached.get("texts", {})), False, bool(cached.get("truncated"))
        except (ValueError, AttributeError):
            pass  # half-written or an older format: re-sweep it

    paths, truncated = list_module_manifests(owner, name)
    if paths is None:
        return 0, False, False
    texts, all_ok = fetch_module_texts(owner, name, paths) if paths else ({}, True)
    if not all_ok:
        return 0, False, truncated
    JAVA_MODULE_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(
        {"truncated": truncated, "paths": paths, "texts": texts}, separators=(",", ":")))
    return len(texts), True, truncated


def main():
    repos = load_java_repos()
    fetched = 0
    by_kind = {kind: 0 for kind in MANIFEST_KINDS}
    none_count = 0
    for repo in repos:
        owner, name = repo.split("/", 1)
        was_cached = cached_manifest(owner, name)[1] is not None
        _text, kind, did_fetch = fetch_java_manifest(owner, name)
        if did_fetch and not was_cached:
            fetched += 1
        if kind is None:
            none_count += 1
        else:
            by_kind[kind] += 1
    print(
        f"{len(repos)} Java-ecosystem repos, {fetched} fetched fresh this run, "
        f"{by_kind['pom.xml']} pom.xml + {by_kind['build.gradle']} build.gradle + "
        f"{by_kind['build.gradle.kts']} build.gradle.kts, {none_count} have none of the three "
        f"at repo root (0-byte marker cached, real absence)",
        file=sys.stderr,
    )

    # Second pass: the submodule sweep. Separate pass rather than folded into
    # the loop above so an interrupted run leaves the (cheap, already-complete)
    # root cache intact and only resumes the expensive half.
    def sweep(repo):
        owner, name = repo.split("/", 1)
        count, did_fetch, truncated = fetch_java_modules(owner, name)
        return count, did_fetch, truncated, module_cache_path(owner, name).exists()

    with ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
        results = list(pool.map(sweep, repos))

    swept, swept_fresh, with_modules, module_files, truncated_trees, unreadable = 0, 0, 0, 0, 0, 0
    for count, did_fetch, truncated, cached in results:
        if not cached:
            unreadable += 1  # tree or blob read failed; retried on the next run
            continue
        swept += 1
        swept_fresh += 1 if did_fetch else 0
        with_modules += 1 if count else 0
        module_files += count
        truncated_trees += 1 if truncated else 0
    print(
        f"submodule sweep: {swept}/{len(repos)} repos swept ({swept_fresh} fresh this run), "
        f"{with_modules} have at least one non-root pom.xml/build.gradle[.kts], "
        f"{module_files} module manifests cached, {truncated_trees} trees came back truncated "
        f"(those repos are under-covered, not silently complete), {unreadable} repos failed to "
        f"read this run (nothing cached for them, retried next run)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
