#!/usr/bin/env python3
"""Turn package.json `dependencies` into real, directed `repo --depends
on--> repo` edges, the JS/TS-ecosystem counterpart to 11_dependency_edges.py's
PyPI-usedPackage-based edges and 29_go_dependency_edges.py's go.mod-based
ones (see scripts/30_fetch_package_json.py for why package.json needed no
build-tool invocation the way go.mod needed none either).

Unlike a Go module path, an npm package name carries no repo-hosting
information at all (`chalk` says nothing about `chalk/chalk` the way
`github.com/chalk/chalk` would), so every distinct package name needs one
registry lookup -- same shape as 09_resolve_packages.py's PyPI resolution,
just against `https://registry.npmjs.org/{name}` instead of PyPI's JSON API.
Checked directly against 4 real packages before writing this: the registry
packument's top-level `repository` field (a string shorthand, a `github:`
prefix, or `{type, url, directory}`) resolves straight to a GitHub owner/repo
for ordinary packages (`react` -> `facebook/react`, `chalk` -> `chalk/chalk`,
`is-odd` -> `jonschlinkert/is-odd`) and for scoped monorepo packages alike
(`@babel/core` -> `babel/babel`, `directory: packages/babel-core` correctly
ignored/collapsed to the repo root, same as a Go subpackage path collapsing
to its module root). A `repository` pointing at gitlab/bitbucket/anywhere
non-GitHub is left unresolved (edge-less) rather than guessed, same "real
signal or none" rule 29 already applies to non-GitHub Go vanity domains.

Only the `dependencies` block counts (real runtime deps), never
`devDependencies`/`peerDependencies`/`optionalDependencies` -- those are
build/test tooling or an author's compatibility note, not this repo's own
"depends on" edge, the npm parallel of 29 only counting go.mod's non-`//
indirect` requires.

Known real coverage gap, not a bug: this only reads the repo-root
package.json (scripts/30_fetch_package_json.py, same repo-root-only scoping
go.mod's fetch used). A JS/TS monorepo that declares most of its real
dependencies in nested workspace package.json files rather than at the root
will show fewer edges here than it actually has -- an honest gap in what got
fetched, not a resolution failure.

Output shape mirrors 11_dependency_edges.py's / 29's full-edge tuples
(source, target, weight, [package names]) so 11 can fold this file straight
into its own combined top-K prune pass alongside the PyPI and Go tiers --
all three are the same semantic tier (a real declared dependency), so they
need one shared prune, not three separate ones concatenated.

Usage: python3 scripts/31_js_dependency_edges.py [out=data/processed/repo_js_dependency_edges.json]
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_JSON_CACHE = ROOT / "data/raw/package_json_cache"
NPM_CACHE = ROOT / "data/raw/npm_registry_cache"
NPM_FETCH_THROTTLE_S = 0.1  # same politeness margin 09_resolve_packages.py uses against PyPI's JSON API

GITHUB_URL_RE = re.compile(r'github\.com[:/]([^/\s]+)/([^/\s#?]+?)(?:\.git)?/?(?:[#?].*)?$')
SHORTHAND_RE = re.compile(r'^([\w.-]+)/([\w.-]+)$')


def github_owner_repo(value):
    if not value:
        return None
    value = value.strip()
    if value.startswith("github:"):
        value = value[len("github:"):]
    if "://" not in value and "@" not in value:
        m = SHORTHAND_RE.match(value)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    m = GITHUB_URL_RE.search(value)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def resolve_repository_field(repo_field):
    if isinstance(repo_field, dict):
        return github_owner_repo(repo_field.get("url"))
    if isinstance(repo_field, str):
        return github_owner_repo(repo_field)
    return None


def npm_cache_path(package):
    return NPM_CACHE / (package.replace("/", "__") + ".json")


def fetch_npm_registry(package):
    cache_path = npm_cache_path(package)
    if cache_path.exists():
        text = cache_path.read_text()
        return json.loads(text) if text.strip() else None

    url = f"https://registry.npmjs.org/{urllib.parse.quote(package, safe='@/')}"
    req = urllib.request.Request(url, headers={"User-Agent": "pelagos-js-dependency-resolve"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        data = None

    NPM_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data) if data is not None else "")
    time.sleep(NPM_FETCH_THROTTLE_S)
    return data


def load_node_ids():
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    all_ids = list(top50) + list(extra)
    return {rid.lower(): rid for rid in all_ids}  # last one wins on collision; none expected


def main(out_path="data/processed/repo_js_dependency_edges.json"):
    canon_lookup = load_node_ids()
    js_repos = sorted(set(
        l.strip() for l in
        (ROOT / "data/repo-lists/js_ecosystem_repos.txt").read_text().splitlines() if l.strip()
    ))

    package_resolved = {}  # npm package name -> owner/repo or None, shared across every repo's dependencies
    resolved_count, unresolved_count = 0, 0
    parsed, empty, unparseable = 0, 0, 0
    direct_dep_total = 0
    edge_packages = defaultdict(set)  # (source, target) -> {package names}

    for repo in js_repos:
        owner, name = repo.split("/", 1)
        cache_path = PACKAGE_JSON_CACHE / f"{owner}__{name}.json"
        if not cache_path.exists():
            continue
        text = cache_path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            empty += 1
            continue
        try:
            manifest = json.loads(text)
        except json.JSONDecodeError:
            unparseable += 1
            continue
        parsed += 1
        source = canon_lookup.get(repo.lower(), repo)

        deps = manifest.get("dependencies")
        if not isinstance(deps, dict):
            continue
        for package in deps:
            direct_dep_total += 1
            if package not in package_resolved:
                data = fetch_npm_registry(package)
                resolved = resolve_repository_field(data.get("repository")) if data else None
                if resolved:
                    resolved_count += 1
                else:
                    unresolved_count += 1
                package_resolved[package] = resolved
            resolved = package_resolved[package]
            if not resolved:
                continue
            target = canon_lookup.get(resolved.lower())
            if not target or target == source:
                continue
            edge_packages[(source, target)].add(package)

    edges = sorted(
        ([a, b, len(pkgs), sorted(pkgs)] for (a, b), pkgs in edge_packages.items()),
        key=lambda e: -e[2],
    )
    Path(out_path).write_text(json.dumps(edges, separators=(",", ":")))

    # Only the resolved mappings are worth persisting as a named lookup
    # (mirrors package_to_repo.json / go_module_to_repo.json).
    package_map = {k: v for k, v in package_resolved.items() if v}
    (ROOT / "data/processed/js_package_to_repo.json").write_text(
        json.dumps(package_map, indent=0, sort_keys=True))

    touched = {n for e in edges for n in (e[0], e[1])}
    sources = {e[0] for e in edges}
    targets = {e[1] for e in edges}
    print(
        f"{parsed}/{len(js_repos)} JS repos have a real package.json ({empty} have none, "
        f"{unparseable} unparseable), {direct_dep_total} dependencies entries, "
        f"{len(package_resolved)} distinct package names ({resolved_count} resolved to a GitHub repo, "
        f"{unresolved_count} left unresolved/non-GitHub) -> {len(edges)} dependency edges "
        f"({len(sources)} source repos -> {len(targets)} target repos, {len(touched)} nodes total) "
        f"-> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
