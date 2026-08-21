#!/usr/bin/env python3
"""Real `repo --depends on--> repo` edges for the Rust cohort, from each
repo's root Cargo.toml (cached by scripts/fetch/cargo_toml.py).

Fourth ecosystem wired into scripts/edges/dependency_edges.py's combined dependency
tier, after Go (29), JS/TS (31) and Java (33), and by some distance the
cheapest of the four:

  * Cargo.toml is real TOML, so stdlib `tomllib` parses it exactly -- no
    hand-rolled brace-depth scanner like Gradle needed (33), and no
    namespace-stripping XML walk like Maven POMs.
  * crates.io answers with the GitHub URL in ONE call:
    `crates.io/api/v1/crates/{name}` -> .crate.repository. Checked directly
    against rand, tokio, libc, anyhow, serde_json, clap and regex -- all 7
    returned a clean https://github.com/owner/repo. Maven needed a
    two-hop search.maven.org -> repo1.maven.org/*.pom -> <scm><url>
    chain to get the same thing, and only 175/1021 coordinates ever
    resolved.

## [workspace.dependencies] is what makes the Rust coverage good

Fetching only the repo-root manifest used to cost JS and Java most of their
coverage: a monorepo's real dependencies sit in per-package/per-submodule
files that were never fetched (npm workspaces; okhttp's
okhttp/build.gradle.kts). Since scripts/fetch/repo_manifests.py that scoping is
gone -- every Cargo.toml in the tree is read, and a workspace's member
crates are parsed alongside its root. The note below is kept because it
still explains why Rust's coverage was good even *before* that, and why
this ecosystem lost the least to root-only fetching: Cargo's
workspace-inheritance feature (Rust 1.64+) means the root manifest
of a workspace usually still carries the full dependency list, in
`[workspace.dependencies]`, for members to inherit with `foo.workspace =
true`. Measured directly on real roots before writing this:

    denoland/deno        [dependencies] 0   [workspace.dependencies] 344
    paradigmxyz/reth     [dependencies] 0   [workspace.dependencies] 335
    helix-editor/helix   [dependencies] 0   [workspace.dependencies]  22
    rust-lang/cargo      [dependencies] 76  [workspace.dependencies] 109
    starship/starship    [dependencies] 47  [workspace.dependencies]   0

deno, reth and helix would contribute nothing at all without it. Note this
is slightly permissive in one direction: `[workspace.dependencies]` lists
what members *may* inherit, so an entry no member actually opts into is
still counted. That's a declared dependency of the workspace either way,
not a guess, and in practice these lists are curated to what the workspace
builds against.

## What counts as a dependency here

Included: `[dependencies]`, `[workspace.dependencies]`, and
`[target.<cfg>.dependencies]` (platform-gated but genuinely built and
linked on that platform -- e.g. ripgrep's tikv-jemallocator on musl).

Excluded: `[dev-dependencies]` (tests/benches/examples only) and
`[build-dependencies]` (build-script tooling such as cc/bindgen/prost-build,
not the crate's own functional surface), plus their `[target.<cfg>.*]`
variants. Same tier of exclusion as Go's `// indirect`, npm's
`devDependencies` and Maven's `scope=test`.

`optional = true` is deliberately NOT excluded, for the same reason
scripts/edges/java_deps.py keeps Maven's optional deps: a Cargo optional
dependency is a real, compiled-against dependency that some feature turns
on, not something the crate merely tolerates.

`path` deps are skipped without a lookup. In a Cargo workspace a path
dependency is always a member of that same workspace, so it either isn't on
crates.io at all (reth's 189-odd `reth-*` internal crates) or resolves
straight back to the source repo and dies on the self-loop filter anyway
(ripgrep's `grep = { version = "0.4.1", path = "crates/grep" }` -> the
crates.io `grep` crate -> BurntSushi/ripgrep, which is the source). Skipping
them up front is the same outcome for hundreds fewer API calls.

`git = "https://github.com/owner/repo"` deps resolve for free with no
crates.io call at all, the same fast path Go's `github.com/owner/...`
module prefix takes in 29.

`package = "real-name"` renames are followed to the real crate name, since
that -- not the local alias -- is what crates.io is keyed by (deno's
`tower-lsp = { package = "deno_tower_lsp", ... }`).

Output shape matches go/js/java_deps.py: [source, target, weight, [crate names]],
weight = how many distinct crates of that target this repo depends on.
Folded into the one shared top-K prune by scripts/edges/dependency_edges.py.

Usage: python3 scripts/edges/rust_deps.py [out=data/processed/repo_rust_dependency_edges.json]
"""
import json
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.identity import canonical_lookup  # noqa: E402
from lib.manifests import iter_manifests, manifest_stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CRATES_IO_CACHE = ROOT / "data/raw/crates_io_cache"
# crates.io actively enforces its crawler policy -- a request with no
# User-Agent gets a flat 403 (verified directly), unlike PyPI/npm/Maven. 0.3s
# is a slightly wider margin than the PyPI/npm/Maven resolvers use for that
# reason; 15 requests at 0.2s spacing came back 200 across the board, so this is comfortably under.
CRATES_FETCH_THROTTLE_S = 0.3
CRATES_USER_AGENT = "pelagos-rust-dependency-resolve (github.com/HugoFara/pelagos)"

GITHUB_URL_RE = re.compile(r'github\.com[:/]([^/\s]+)/([^/\s#?]+?)(?:\.git)?/?(?:[#?].*)?$')

# Dependency tables to read out of a root Cargo.toml. Anything not named here
# (dev-dependencies, build-dependencies) is deliberately left out -- see the
# module docstring.
DEP_TABLES = ["dependencies"]


def github_owner_repo(value):
    if not value:
        return None
    m = GITHUB_URL_RE.search(value.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def dep_tables(manifest):
    """Every dependency table in a root Cargo.toml worth reading, as raw
    {name: spec} dicts: the plain [dependencies], [workspace.dependencies]
    (the workspace-inheritance list -- see docstring), and each
    [target.<cfg>.dependencies]."""
    tables = []
    for key in DEP_TABLES:
        table = manifest.get(key)
        if isinstance(table, dict):
            tables.append(table)
    workspace = manifest.get("workspace")
    if isinstance(workspace, dict):
        for key in DEP_TABLES:
            table = workspace.get(key)
            if isinstance(table, dict):
                tables.append(table)
    target = manifest.get("target")
    if isinstance(target, dict):
        for cfg_table in target.values():
            if not isinstance(cfg_table, dict):
                continue
            for key in DEP_TABLES:
                table = cfg_table.get(key)
                if isinstance(table, dict):
                    tables.append(table)
    return tables


def parse_cargo_dependencies(text):
    """[(crate_name, git_owner_repo_or_None), ...] for this manifest's real,
    direct dependencies. crate_name is the crates.io name (following a
    `package = ` rename); a git dependency carries its owner/repo along so
    the caller can skip the registry entirely."""
    try:
        manifest = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []

    deps = []
    for table in dep_tables(manifest):
        for name, spec in table.items():
            if isinstance(spec, str):  # anyhow = "1.0.75"
                deps.append((name, None))
                continue
            if not isinstance(spec, dict):
                continue
            if "path" in spec:  # workspace-internal member, see docstring
                continue
            git_repo = github_owner_repo(spec.get("git"))
            if git_repo:
                deps.append((name, git_repo))
                continue
            if spec.get("git"):  # real git dep, just not GitHub-hosted
                continue
            deps.append((spec.get("package") or name, None))
    return deps


def crates_cache_path(crate):
    # Crate names are [A-Za-z0-9_-] only, so they're already filesystem-safe.
    return CRATES_IO_CACHE / f"{crate}.json"


def fetch_crate_repo(crate):
    """owner/repo for a crates.io crate, or None. Cached per crate (empty
    file = a real "no GitHub repository field"), so a crate depended on by
    200 repos costs exactly one request."""
    cache_path = crates_cache_path(crate)
    if cache_path.exists():
        text = cache_path.read_text()
        return json.loads(text) if text.strip() else None

    resolved = _resolve_crate_repo_live(crate)
    CRATES_IO_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resolved) if resolved else "")
    return resolved


def _resolve_crate_repo_live(crate):
    url = f"https://crates.io/api/v1/crates/{urllib.parse.quote(crate, safe='')}"
    req = urllib.request.Request(url, headers={"User-Agent": CRATES_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    finally:
        time.sleep(CRATES_FETCH_THROTTLE_S)
    try:
        payload = json.loads(data).get("crate") or {}
    except ValueError:
        return None
    # `repository` is the declared source; `homepage` is a docs site far more
    # often than a repo, but it's a real GitHub URL often enough to be worth
    # the fallback (same shape as java_deps.py's <scm><url> -> <url> fallback).
    for candidate in (payload.get("repository"), payload.get("homepage")):
        resolved = github_owner_repo(candidate)
        if resolved:
            return resolved
    return None


def load_node_ids():
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    return canonical_lookup(list(top50) + list(extra))  # duplicate slugs resolved; see scripts/lib/identity.py


def main(out_path="data/processed/repo_rust_dependency_edges.json"):
    canon_lookup = load_node_ids()
    cohort = sorted(set(canon_lookup.values()))

    crate_resolved = {}  # crate name -> owner/repo or None, shared across every repo
    resolved_count, unresolved_count = 0, 0
    parsed, no_manifest = 0, 0
    direct_dep_total, git_dep_total = 0, 0
    edge_crates = defaultdict(set)  # (source, target) -> {crate name, ...}

    for repo, _path, text in iter_manifests("rust", cohort):
        if not text.strip():
            no_manifest += 1
            continue
        deps = parse_cargo_dependencies(text)
        parsed += 1
        if not deps:
            continue
        source = canon_lookup.get(repo.lower(), repo)

        for crate, git_repo in deps:
            direct_dep_total += 1
            if git_repo:  # free, no registry call
                git_dep_total += 1
                resolved = git_repo
            else:
                if crate not in crate_resolved:
                    found = fetch_crate_repo(crate)
                    if found:
                        resolved_count += 1
                    else:
                        unresolved_count += 1
                    crate_resolved[crate] = found
                resolved = crate_resolved[crate]
            if not resolved:
                continue
            target = canon_lookup.get(resolved.lower())
            if not target or target == source:
                continue
            edge_crates[(source, target)].add(crate)

    edges = sorted(
        ([a, b, len(crates), sorted(crates)] for (a, b), crates in edge_crates.items()),
        key=lambda e: -e[2],
    )
    Path(out_path).write_text(json.dumps(edges, separators=(",", ":")))

    # Only the resolved mappings are worth persisting as a named lookup
    # (mirrors package_to_repo.json / go_module_to_repo.json /
    # js_package_to_repo.json / java_coord_to_repo.json).
    crate_map = {k: v for k, v in crate_resolved.items() if v}
    (ROOT / "data/processed/crate_to_repo.json").write_text(
        json.dumps(crate_map, indent=0, sort_keys=True))

    repos_with, files_seen, nested = manifest_stats("rust", cohort)
    touched = {n for e in edges for n in (e[0], e[1])}
    sources = {e[0] for e in edges}
    targets = {e[1] for e in edges}
    print(
        f"{parsed} Cargo.toml files parsed across {repos_with} cohort repos "
        f"({nested} of the {files_seen} files are nested, unreachable by root-only fetching; "
        f"{no_manifest} empty), "
        f"{direct_dep_total} dependency entries ({git_dep_total} git deps resolved without a "
        f"registry call), {len(crate_resolved)} distinct crates looked up on crates.io "
        f"({resolved_count} resolved to a GitHub repo, {unresolved_count} left "
        f"unresolved/non-GitHub) -> {len(edges)} dependency edges ({len(sources)} source repos -> "
        f"{len(targets)} target repos, {len(touched)} nodes total) -> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
