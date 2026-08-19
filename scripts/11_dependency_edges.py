#!/usr/bin/env python3
"""Turn `repo --uses--> package` (usedPackage triples) into real, directed
`repo --depends on--> repo` edges, using package_to_repo.json (package ->
GitHub repo, from 09_resolve_packages.py) and dependency_source_id_map.json
(dependency-cohort raw id -> canonical owner/repo, from
10_fetch_new_repo_stats.py -- those ids are inconsistently ordered, see
NOTES.md).

Only emits an edge when both ends are real nodes (the 51 original +
whatever 10_fetch_new_repo_stats.py added). Matching is case-insensitive
and snaps to the node set's own casing: GitHub's API and the dependency
cohort don't always agree on casing/ordering with the curated repo lists
(e.g. the raw id `dgl/dmlc` resolves to `dmlc/dgl`, already one of the 51 --
without case-insensitive snapping that edge would silently vanish instead
of attaching to the existing node).

Shape mirrors compute_shared_edges.py's with_members=1 output (source,
target, weight, [packages]) so the explorer can reuse the same edge-tuple
handling -- just directed instead of symmetric, and sourced from real
dependency data instead of shared-person overlap.

Also folds in data/processed/repo_go_dependency_edges.json (Go's go.mod-
derived edges, see 29_go_dependency_edges.py), data/processed/
repo_js_dependency_edges.json (JS/TS's package.json-derived edges, see
31_js_dependency_edges.py), data/processed/repo_java_dependency_edges.json
(Java's pom.xml/build.gradle-derived edges, see 33_java_dependency_edges.py),
data/processed/repo_rust_dependency_edges.json (Rust's Cargo.toml-derived
edges, see 36_rust_dependency_edges.py), and data/processed/
repo_python_dependency_edges.json (the GitHub-search-sourced Python repos'
pyproject.toml/requirements.txt-derived edges, see
38_python_dependency_edges.py -- those repos postdate the SemRepo dump, so
the usedPackage triples above never covered them), and data/processed/
repo_cpp_dependency_edges.json (C/C++'s .gitmodules/vcpkg.json/conanfile
-derived edges, see 41_cpp_dependency_edges.py)
when present, before the single top-K prune pass below runs -- all seven
sources are the same semantic tier (a real, declared package
dependency, the explorer's one "real dependency edge" type), so they need one
shared prune over the combined graph, not several separate prunes
concatenated (a repo with deps from more than one source would otherwise show
more than top-K neighbors while one with only one source shows fewer).

Every PyPI-sourced edge here has weight 1 (each package resolves to one
specific repo -- torch -> pytorch/pytorch, torchvision -> pytorch/vision,
never the same target twice for one source); Go-, JS-, Java-, Rust- and
C/C++-sourced edges can have weight > 1 (a repo commonly requires several
submodules/packages of the same target, e.g. github.com/aws/aws-sdk-go-v2/config
and .../credentials both collapsing to aws/aws-sdk-go-v2, @babel/core and
@babel/parser both collapsing to babel/babel, org.junit.jupiter:junit-jupiter-engine
and .../junit-jupiter-params both collapsing to junit-team/junit5, or the
serde/serde_derive crate pair both collapsing to serde-rs/serde -- a real "how
many distinct modules of this dependency do you use" signal there). Either
way raw weight carries no pruning signal:
the unpruned graph is a dense ~21%-of-all-pairs bipartite-ish mess (104
sources x 140 targets -> 3032 edges, PyPI-only). Pruned the same way
compute_shared_edges.py prunes (keep an edge if it's in either endpoint's
top-K list), just with a different, still-real ranking signal: target
in-degree (how many source repos in this set depend on it -- "is this a
load-bearing library here") for a source's top-K, and source out-degree
(how many distinct targets it depends on -- "how plugged into this
ecosystem is it") for a target's top-K.

Usage: python3 11_dependency_edges.py [out_full=data/processed/repo_dependency_edges.json]
    [out_pruned=data/processed/repo_dependency_edges_pruned.json] [top_k=4]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB_CACHE = ROOT / "data/raw/github_cache"

TRIPLE_RE = re.compile(
    r'^<https://semrepo\.org/repository/([^>]+)> '
    r'<https://semrepo\.org/property/usedPackage> '
    r'<https://semrepo\.org/package/([^>]+)> \.'
)


def load_node_ids():
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    all_ids = list(top50) + list(extra)
    return {rid.lower(): rid for rid in all_ids}  # last one wins on collision; none expected


def package_target_cache(package_to_repo, canon_lookup):
    """package -> canonical node id (or None), using the same cached GitHub
    API responses 10_fetch_new_repo_stats.py already fetched for every
    resolved package -- no new network calls."""
    cache = {}
    for package, raw in package_to_repo.items():
        owner, name = raw.split("/", 1)
        cache_path = GITHUB_CACHE / f"{owner}__{name}.json"
        resolved = None
        if cache_path.exists():
            data = json.loads(cache_path.read_text())
            full_name = data.get("full_name")
            if full_name:
                resolved = canon_lookup.get(full_name.lower())
        cache[package] = resolved
    return cache


def main(out_full="data/processed/repo_dependency_edges.json",
         out_pruned="data/processed/repo_dependency_edges_pruned.json", top_k=4):
    top_k = int(top_k)
    canon_lookup = load_node_ids()
    package_to_repo = json.loads((ROOT / "data/processed/package_to_repo.json").read_text())
    source_id_map = json.loads((ROOT / "data/processed/dependency_source_id_map.json").read_text())
    pkg_target = package_target_cache(package_to_repo, canon_lookup)

    # Snap source_id_map's canonical values to the node set's own casing too.
    source_lookup = {raw: canon_lookup.get(canonical.lower())
                      for raw, canonical in source_id_map.items()}

    edge_packages = defaultdict(set)  # (source, target) -> {package,...}
    total_triples = 0
    with open(ROOT / "data/raw/repo_packages.nt") as f:
        for line in f:
            m = TRIPLE_RE.match(line)
            if not m:
                continue
            total_triples += 1
            raw_source, package = m.groups()
            source = source_lookup.get(raw_source)
            if not source:
                continue
            target = pkg_target.get(package)
            if not target or target == source:
                continue
            edge_packages[(source, target)].add(package)
    pypi_edge_count = len(edge_packages)

    # Fold in each other ecosystem's dependency-derived edges (Go's go.mod,
    # 29_go_dependency_edges.py; JS/TS's package.json, 31_js_dependency_edges.py;
    # Java's pom.xml/build.gradle, 33_java_dependency_edges.py; Rust's
    # Cargo.toml, 36_rust_dependency_edges.py; Python's pyproject.toml/
    # requirements.txt, 38_python_dependency_edges.py), if that
    # script has been run -- same (source, target) -> {module/package/
    # coordinate strings} shape, so this is a plain dict merge before the one
    # shared prune pass below. Optional: an older checkout or a from-scratch
    # run before a given ecosystem's scripts existed should still work with
    # whatever sources are present, same "degrades gracefully" idiom
    # 18_text_embeddings.py uses for ISSUE_TITLES_PATH.
    extra_sources = [
        ("go.mod requires", ROOT / "data/processed/repo_go_dependency_edges.json"),
        ("package.json requires", ROOT / "data/processed/repo_js_dependency_edges.json"),
        ("pom.xml/build.gradle requires", ROOT / "data/processed/repo_java_dependency_edges.json"),
        ("Cargo.toml requires", ROOT / "data/processed/repo_rust_dependency_edges.json"),
        ("pyproject/requirements requires", ROOT / "data/processed/repo_python_dependency_edges.json"),
        (".gitmodules/vcpkg/conan requires", ROOT / "data/processed/repo_cpp_dependency_edges.json"),
    ]
    extra_summary = []
    for label, path in extra_sources:
        triples = 0
        before = len(edge_packages)
        if path.exists():
            for a, b, _w, modules in json.loads(path.read_text()):
                triples += len(modules)
                edge_packages[(a, b)].update(modules)
        extra_summary.append(f"{triples} {label} ({len(edge_packages) - before} new pairs)")

    edges = sorted(
        ([a, b, len(pkgs), sorted(pkgs)] for (a, b), pkgs in edge_packages.items()),
        key=lambda e: -e[2],
    )
    Path(out_full).write_text(json.dumps(edges, separators=(",", ":")))

    # Prune to a legible graph: keep an edge if it's in either endpoint's
    # top-K list, same shape as compute_shared_edges.py's pruning, but
    # ranked by degree instead of weight (every edge here has weight 1 --
    # see module docstring). Real, derived numbers either way, never guessed.
    target_in_degree = defaultdict(int)
    source_out_degree = defaultdict(int)
    for a, b, _w, _pkgs in edges:
        target_in_degree[b] += 1
        source_out_degree[a] += 1

    by_source = defaultdict(list)
    by_target = defaultdict(list)
    for e in edges:
        a, b = e[0], e[1]
        by_source[a].append((target_in_degree[b], b))
        by_target[b].append((source_out_degree[a], a))
    for lst in by_source.values():
        lst.sort(reverse=True)
    for lst in by_target.values():
        lst.sort(reverse=True)

    keep = []
    for e in edges:
        a, b = e[0], e[1]
        a_topk = {x[1] for x in by_source[a][:top_k]}
        b_topk = {x[1] for x in by_target[b][:top_k]}
        if b in a_topk or a in b_topk:
            keep.append(e)
    Path(out_pruned).write_text(json.dumps(keep, separators=(",", ":")))

    touched = {n for e in edges for n in (e[0], e[1])}
    pruned_touched = {n for e in keep for n in (e[0], e[1])}
    sources = {e[0] for e in edges}
    targets = {e[1] for e in edges}
    print(f"{total_triples} usedPackage triples ({pypi_edge_count} PyPI-sourced pairs) + "
          f"{' + '.join(extra_summary)} -> {len(edges)} dependency edges ({len(sources)} source "
          f"repos -> {len(targets)} target repos, {len(touched)} nodes total) -> {out_full}",
          file=sys.stderr)
    print(f"pruned to top-{top_k}: {len(keep)} edges, {len(pruned_touched)} nodes -> {out_pruned}",
          file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
