#!/usr/bin/env python3
"""Real `repo --depends on--> repo` edges from declared C/C++ dependencies.

Reads every .gitmodules / vcpkg.json / conanfile in the cohort, at any depth,
via scripts/lib/manifests.py -- not one file at the root of the repos GitHub's
`language:` facet labelled C/C++. That facet reports a repo's *dominant*
language, one value per repo, so under the old scoping a repo could not
contribute C/C++ dependencies unless C/C++ happened to be its biggest
language by bytes. Vendored trees and byte-identical copies of other
projects' manifests are dropped by that module before anything is parsed
here; see its docstring for the measurement behind the change.

Sixth ecosystem wired into scripts/edges/dependency_edges.py's combined dependency
tier, after Go (29), JS/TS (31), Java (33), Rust (36) and Python (38), and
the only one with no single registry behind it. C/C++ never converged on
one package manager, so instead of one manifest this reads three real ones
and resolves each the way that manifest actually permits.

## .gitmodules is what makes this work at all

It's the *least* fashionable of the three and by far the highest-yield.
Measured on the sample of 40 major C/C++ repos probed in 40:

    .gitmodules   7/8 files with deps, 237 refs, 223 distinct GitHub repos
    vcpkg.json    2/2 files with deps,   3 refs
    conanfile.py  1/3 files with deps,   1 ref

and crucially a submodule URL is a *literal* `https://github.com/owner/repo`
already sitting in the file -- the same free, zero-network resolution Go's
`github.com/owner/...` module prefix gets in 29, with no registry hop like
crates.io (36) or Maven Central (33). Vendoring a dependency as a git
submodule is the dominant way C/C++ projects declare a source dependency,
and it is fully machine-readable.

Relative submodule URLs (`../foo.git`) are resolved against the source
repo's own location, which is exactly git's documented semantics for them
-- `../foo.git` under `github.com/owner/repo` is `owner/foo`, and
`../../other/foo.git` is `other/foo`. Real resolution, not a guess; counted
separately in the summary so the split stays visible.

## CMake is excluded on purpose

`CMakeLists.txt` is present in 65% of these repos -- three times
.gitmodules' coverage -- and it is still not used here. `find_package(fmt)`
names a CMake target expected to already exist on the system; it carries no
registry coordinate and no repo, so turning it into an edge would mean
guessing which GitHub repo a bare name like `Threads` or `OpenSSL` or `ZLIB`
refers to. That is exactly the kind of fabricated signal this pipeline
refuses elsewhere (Go's `golang.org/x/*` left unresolved rather than mapped
to the golang/net mirror; the theta=None contract). The one CMake construct
that IS resolvable, `FetchContent_Declare(... GIT_REPOSITORY <url>)`,
appeared in exactly 1 of 25 sampled root CMakeLists.txt -- real, but not
worth 1000 fetches and the standing temptation to loosen it into
find_package.

## Registry resolution for the two package managers

vcpkg: `microsoft/vcpkg/ports/{port}/vcpkg.json` -> `homepage`. Verified
directly -- fmt -> github.com/fmtlib/fmt, ms-gsl -> github.com/Microsoft/GSL,
while boost-multi-index/openssl/zlib point at project websites and are
honestly left unresolved.

Conan: `conan-io/conan-center-index/recipes/{name}/all/conanfile.py` ->
`homepage` (falling back to the first folder in config.yml when a recipe
doesn't use the `all` layout). Note the recipe's `url` field is deliberately
NOT read: it is *always* `https://github.com/conan-io/conan-center-index`
(the index repo itself, verified on openssl/fmt/zlib), so accepting it would
manufacture an edge from every Conan-using repo to conan-center-index.
`homepage` is the upstream project.

## What counts as a dependency here

Included: every `.gitmodules` submodule, vcpkg `dependencies` (including
per-feature ones -- same "optional is still real" rule that keeps Cargo's
`optional = true` and Maven's `<optional>true</optional>`), and Conan
`requires`.

Excluded: vcpkg's own `vcpkg-*` infrastructure ports (vcpkg-cmake,
vcpkg-cmake-config...) and any `"host": true` entry, plus Conan's
`build_requires`/`tool_requires`/`test_requires` -- build tooling rather
than the project's own functional surface, the same tier of exclusion as
Go's `// indirect`, npm's `devDependencies`, Maven's `scope=test` and
Cargo's `[dev-dependencies]`/`[build-dependencies]`.

Output shape matches go/js/java/rust/python_deps.py:
[source, target, weight, [coordinates]], weight = how many distinct
submodules/ports/packages of that target this repo depends on. Folded into the one shared top-K prune by
scripts/edges/dependency_edges.py.

Usage: python3 scripts/edges/cpp_deps.py [out=data/processed/repo_cpp_dependency_edges.json]
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.identity import canonical_lookup  # noqa: E402
from lib.manifests import load_manifests, manifest_stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VCPKG_PORT_CACHE = ROOT / "data/raw/vcpkg_port_cache"
CONAN_RECIPE_CACHE = ROOT / "data/raw/conan_recipe_cache"

REGISTRY_THROTTLE_S = 0.2  # raw.githubusercontent.com; same as PyPI/npm/Maven
REGISTRY_USER_AGENT = "pelagos-cpp-dependency-resolve (github.com/HugoFara/pelagos)"
VCPKG_PORT_URL = "https://raw.githubusercontent.com/microsoft/vcpkg/master/ports/{}/vcpkg.json"
CONAN_RECIPE_URL = "https://raw.githubusercontent.com/conan-io/conan-center-index/master/recipes/{}/{}/conanfile.py"
CONAN_CONFIG_URL = "https://raw.githubusercontent.com/conan-io/conan-center-index/master/recipes/{}/config.yml"

GITHUB_URL_RE = re.compile(r'github\.com[:/]([^/\s]+)/([^/\s#?]+?)(?:\.git)?/?(?:[#?].*)?$')
# `homepage = "https://github.com/fmtlib/fmt"` in a Conan recipe. Deliberately
# not `url = ` -- see module docstring.
CONAN_HOMEPAGE_RE = re.compile(r'^\s*homepage\s*=\s*["\']([^"\']+)["\']', re.M)
CONAN_FOLDER_RE = re.compile(r'^\s*folder:\s*["\']?([^"\'\s]+)', re.M)

MANIFEST_KINDS = [".gitmodules", "vcpkg.json", "conanfile.py", "conanfile.txt"]


def github_owner_repo(value):
    if not value:
        return None
    m = GITHUB_URL_RE.search(value.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


# --------------------------------------------------------------------------
# .gitmodules -- literal URLs, no registry
# --------------------------------------------------------------------------

def parse_gitmodules(text, source_repo):
    """[(label, owner/repo), ...] for submodules hosted on GitHub, plus a
    count of how many resolved via a relative URL.

    Hand-scanned rather than fed to configparser: .gitmodules indents its
    keys under each `[submodule "name"]` header with a tab, which
    configparser reads as a line continuation of the previous value."""
    deps = []
    relative = 0
    section = None
    path = None
    url = None

    def flush():
        nonlocal relative
        if not url:
            return
        resolved = github_owner_repo(url)
        if resolved is None and url.startswith(".."):
            resolved = resolve_relative_submodule(url, source_repo)
            if resolved:
                relative += 1
        if resolved:
            deps.append((path or section or resolved, resolved))

    for line in text.splitlines():
        stripped = line.strip()
        header = re.match(r'\[submodule\s+"?(.*?)"?\]', stripped)
        if header:
            flush()
            section, path, url = header.group(1), None, None
            continue
        m = re.match(r'(\w+)\s*=\s*(.*)', stripped)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key == "path":
            path = value
        elif key == "url":
            url = value
    flush()
    return deps, relative


def resolve_relative_submodule(url, source_repo):
    """git resolves a relative submodule URL against the superproject's own
    remote: under github.com/owner/repo, `../foo.git` is owner/foo and
    `../../other/foo.git` is other/foo."""
    # The superproject's URL path is owner/repo; each leading ../ pops one
    # segment off it, then whatever remains is appended.
    parts = source_repo.split("/")
    rest = url
    while rest.startswith("../"):
        rest = rest[3:]
        if not parts:
            return None  # climbed above the host -- not a repo path any more
        parts.pop()
    if rest.startswith("./"):
        rest = rest[2:]
    if rest.endswith(".git"):
        rest = rest[:-4]
    combined = [p for p in parts + rest.split("/") if p]
    # Anything that doesn't land on exactly owner/repo isn't a GitHub repo
    # root (a gist, a nested path, a climb past the owner) -- left unresolved.
    return "/".join(combined) if len(combined) == 2 else None


# --------------------------------------------------------------------------
# vcpkg.json / conanfile.{py,txt} -- names needing a registry hop
# --------------------------------------------------------------------------

def parse_vcpkg_dependencies(text):
    """Port names this manifest declares, including per-feature ones.
    vcpkg's own vcpkg-* infrastructure ports and host (build-tool) entries
    are skipped -- see module docstring."""
    try:
        manifest = json.loads(text)
    except ValueError:
        return []
    if not isinstance(manifest, dict):
        return []

    blocks = [manifest.get("dependencies")]
    features = manifest.get("features")
    if isinstance(features, dict):
        blocks.extend(f.get("dependencies") for f in features.values() if isinstance(f, dict))
    elif isinstance(features, list):  # older array-of-objects form
        blocks.extend(f.get("dependencies") for f in features if isinstance(f, dict))

    names = []
    for block in blocks:
        if not isinstance(block, list):
            continue
        for entry in block:
            if isinstance(entry, str):
                name = entry
            elif isinstance(entry, dict):
                if entry.get("host"):  # build-time tooling, not a runtime dep
                    continue
                name = entry.get("name")
            else:
                continue
            if not name or name.startswith("vcpkg-"):
                continue
            names.append(name)
    return names


def parse_conanfile_txt(text):
    """Package names from a conanfile.txt [requires] section. Other
    sections ([build_requires], [tool_requires], [test_requires],
    [generators], [options]) are skipped."""
    names = []
    in_requires = False
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if stripped.startswith("["):
            in_requires = stripped.lower() == "[requires]"
            continue
        if in_requires:
            name = stripped.split("/", 1)[0].strip()
            if name:
                names.append(name)
    return names


def parse_conanfile_py(text):
    """Package names from a conanfile.py's requires. Both the
    `self.requires("fmt/9.1.0")` call form and the `requires = "fmt/9.1.0",
    ...` attribute form; build_requires/tool_requires/test_requires are not
    matched by either pattern."""
    names = []
    for m in re.finditer(r'self\.requires\(\s*["\']([A-Za-z0-9_.+-]+)/', text):
        names.append(m.group(1))
    for m in re.finditer(r'^\s*requires\s*=\s*(.+?)$', text, re.M):
        for coord in re.finditer(r'["\']([A-Za-z0-9_.+-]+)/', m.group(1)):
            names.append(coord.group(1))
    return names


# --------------------------------------------------------------------------
# Registry lookups, cached per coordinate
# --------------------------------------------------------------------------

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": REGISTRY_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    finally:
        time.sleep(REGISTRY_THROTTLE_S)


def cached_lookup(cache_dir, key, resolve):
    """owner/repo for a registry coordinate, or None. Cached per coordinate
    (empty file = a real "no GitHub homepage"), so a port depended on by 200
    repos costs exactly one request."""
    cache_path = cache_dir / f"{urllib.parse.quote(key, safe='')}.json"
    if cache_path.exists():
        text = cache_path.read_text()
        return json.loads(text) if text.strip() else None
    resolved = resolve(key)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resolved) if resolved else "")
    return resolved


def _resolve_vcpkg_port_live(port):
    text = http_get(VCPKG_PORT_URL.format(urllib.parse.quote(port, safe="")))
    if not text:
        return None
    try:
        manifest = json.loads(text)
    except ValueError:
        return None
    return github_owner_repo(manifest.get("homepage"))


def _resolve_conan_package_live(package):
    quoted = urllib.parse.quote(package, safe="")
    text = http_get(CONAN_RECIPE_URL.format(quoted, "all"))
    if text is None:
        config = http_get(CONAN_CONFIG_URL.format(quoted))
        folder = CONAN_FOLDER_RE.search(config) if config else None
        if not folder:
            return None
        text = http_get(CONAN_RECIPE_URL.format(quoted, urllib.parse.quote(folder.group(1), safe="")))
        if text is None:
            return None
    m = CONAN_HOMEPAGE_RE.search(text)
    return github_owner_repo(m.group(1)) if m else None


def load_node_ids():
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    return canonical_lookup(list(top50) + list(extra))  # duplicate slugs resolved; see scripts/lib/identity.py


def main(out_path="data/processed/repo_cpp_dependency_edges.json"):
    canon_lookup = load_node_ids()
    cohort = sorted(set(canon_lookup.values()))
    manifests = load_manifests("cpp", cohort)

    vcpkg_resolved = {}  # port -> owner/repo or None, shared across every repo
    conan_resolved = {}  # package -> owner/repo or None
    edge_coords = defaultdict(set)  # (source, target) -> {coordinate, ...}

    kind_repos = defaultdict(set)  # kind -> repos that declared >=1 dep via it
    kind_dep_counts = defaultdict(int)   # kind -> raw declared entries
    with_manifest = 0
    relative_total = 0

    for repo in cohort:
        files = manifests.get(repo)
        if not files:
            continue
        source = canon_lookup.get(repo.lower(), repo)
        with_manifest += 1

        # (coordinate, already-resolved owner/repo or None, registry) triples.
        # A repo can now hold several manifests of the same kind at different
        # paths -- a vcpkg.json per subproject, a .gitmodules in a nested
        # component -- so this walks (path, kind) pairs rather than the single
        # root file per kind the root-only cache could hold.
        entries = []
        for path, text in sorted(files.items()):
            kind = path.rpartition("/")[2]
            if kind not in MANIFEST_KINDS or not text.strip():
                continue
            if kind == ".gitmodules":
                deps, relative = parse_gitmodules(text, repo)
                relative_total += relative
                found = [(label, resolved, None) for label, resolved in deps]
            elif kind == "vcpkg.json":
                found = [(port, None, "vcpkg") for port in parse_vcpkg_dependencies(text)]
            elif kind == "conanfile.py":
                found = [(pkg, None, "conan") for pkg in parse_conanfile_py(text)]
            else:  # conanfile.txt
                found = [(pkg, None, "conan") for pkg in parse_conanfile_txt(text)]
            if found:
                kind_repos[kind].add(repo)  # a set, not a counter: one repo can hold
                kind_dep_counts[kind] += len(found)  # several manifests of the same kind now
            entries.extend(found)

        for coord, direct, registry in entries:
            if direct:
                resolved = direct
            elif registry == "vcpkg":
                if coord not in vcpkg_resolved:
                    vcpkg_resolved[coord] = cached_lookup(
                        VCPKG_PORT_CACHE, coord, _resolve_vcpkg_port_live)
                resolved = vcpkg_resolved[coord]
            else:
                if coord not in conan_resolved:
                    conan_resolved[coord] = cached_lookup(
                        CONAN_RECIPE_CACHE, coord, _resolve_conan_package_live)
                resolved = conan_resolved[coord]
            if not resolved:
                continue
            target = canon_lookup.get(resolved.lower())
            if not target or target == source:
                continue
            edge_coords[(source, target)].add(coord)

    edges = sorted(
        ([a, b, len(coords), sorted(coords)] for (a, b), coords in edge_coords.items()),
        key=lambda e: -e[2],
    )
    Path(out_path).write_text(json.dumps(edges, separators=(",", ":")))

    # Only the resolved mappings are worth persisting as a named lookup
    # (mirrors package_to_repo.json / go_module_to_repo.json /
    # js_package_to_repo.json / java_coord_to_repo.json / crate_to_repo.json).
    (ROOT / "data/processed/cpp_port_to_repo.json").write_text(json.dumps(
        {"vcpkg": {k: v for k, v in sorted(vcpkg_resolved.items()) if v},
         "conan": {k: v for k, v in sorted(conan_resolved.items()) if v}},
        indent=0, sort_keys=True))

    touched = {n for e in edges for n in (e[0], e[1])}
    sources = {e[0] for e in edges}
    targets = {e[1] for e in edges}
    per_kind = ", ".join(
        f"{kind_dep_counts[k]} via {k} ({len(kind_repos[k])} repos)"
        for k in MANIFEST_KINDS if kind_dep_counts[k])
    _repos_with, files_seen, nested = manifest_stats("cpp", cohort)
    vcpkg_ok = sum(1 for v in vcpkg_resolved.values() if v)
    conan_ok = sum(1 for v in conan_resolved.values() if v)
    print(
        f"{with_manifest}/{len(cohort)} cohort repos have at least one C/C++ manifest across "
        f"{files_seen} files ({nested} of them nested, unreachable by root-only fetching), "
        f"{sum(kind_dep_counts.values())} dependency entries [{per_kind}], "
        f"{relative_total} submodule URLs resolved relative to their superproject; "
        f"registry lookups: {len(vcpkg_resolved)} vcpkg ports ({vcpkg_ok} resolved), "
        f"{len(conan_resolved)} conan packages ({conan_ok} resolved) "
        f"-> {len(edges)} dependency edges ({len(sources)} source repos -> "
        f"{len(targets)} target repos, {len(touched)} nodes total) -> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
