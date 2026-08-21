#!/usr/bin/env python3
"""Shared manifest inventory: which dependency manifests a repo actually has,
across every ecosystem, at any depth.

Until now each ecosystem's fetcher (28/30/32/35/37/40) read exactly one
repo-list -- `data/repo-lists/{lang}_ecosystem_repos.txt` -- and probed one
fixed path at repo root. That wires two independent facts together that have
no reason to be the same:

    "GitHub's `language:` facet calls this repo Rust"
    "this repo declares its dependencies in Cargo.toml, at the root"

The facet reports a repo's *dominant* language by bytes, one value per repo.
Real repos are routinely several ecosystems at once -- a Rust crate with a
`package.json` for its docs site or wasm bindings, a C++ library with a
`pyproject.toml` for its Python wheel, a Go service with a JS frontend. Under
the old scheme every one of those declared, real dependency lists was
unreachable: no script ever looked for them, because the repo was "a Rust
repo" and Rust's fetcher only ever asks for Cargo.toml.

Measured on a random 240-repo sample of the cohort (40 per ecosystem list),
reading each repo's full git tree:

    49/240 (20%)  have a ROOT manifest for an ecosystem other than their own
    99/240 (41%)  have one at any depth
    27/240 (11%)  have their OWN ecosystem's manifest only nested, never at root
    25/240 (10%)  have NO root manifest at all but do have nested ones
                  -- today those repos are silently edge-less despite
                     shipping a real, declared dependency list

Biggest single miss is Rust repos carrying a package.json (12 of 40 sampled).

So the unit of discovery here is the repo's git tree, not its language label.
`42_scan_repo_manifests.py` reads one tree per cohort repo and caches every
manifest-named blob it finds; this module is the read side, shared by all six
`*_dependency_edges.py` scripts.

## Where the filtering happens, and why here rather than at fetch time

A manifest sitting in `node_modules/` or `third_party/` is *another project's*
dependency list. Attributing it to the repo that vendored it would invent
edges that nobody declared -- the one thing this pipeline refuses to do
everywhere else. So vendored paths are excluded.

They are excluded at **read** time, not fetch time, following the convention
32_fetch_java_manifests.py already set for `buildSrc/`: the cache records
every manifest path the tree contained, so the exclusion list below can be
measured, argued with and changed without re-fetching a single file. What the
scanner declines to *download* is a narrower question (it will not pull down a
committed node_modules), and it records that separately -- see the scanner's
docstring.

## The copies a directory name cannot catch

A directory-name rule only catches vendoring that admits what it is. Plenty
does not: `0-KaiKai-0/SH2` carries a whole checked-in copy of
huggingface/transformers under a plain `transformers/` directory, contributing
68 requirements.txt files that declare huggingface's dependencies, not SH2's.
No exclusion list of directory names can find that, and guessing at project
names would be exactly the kind of heuristic this pipeline avoids.

Content hashes settle it without guessing. If a manifest's exact bytes appear
in more than one repository *and* it is nested rather than at the repo's own
root, it is a copy -- two independent projects do not write byte-identical
dependency lists deep inside their trees by coincidence. Measured across the
cohort's scanned manifests, this is 4.3% of the non-vendored files, and it
catches the `transformers/` case, a fairseq tree copied into 13 repos, and a
`TeViT-main/` tree copied into 31.

Root manifests are deliberately exempt. Ten research repos really do share one
byte-identical root `requirements.txt`, and that file is each of their own
declaration of what their project needs -- copied from a common ancestor,
but theirs.

Falls back to the six legacy per-language root caches for any repo the
scanner has not covered yet, so every edge script keeps working unchanged on a
checkout where 42 has never been run -- the same "degrades gracefully" idiom
11_dependency_edges.py uses for the per-ecosystem edge files.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_CACHE = ROOT / "data/raw/repo_manifest_cache"
INVENTORY_PATH = ROOT / "data/processed/repo_manifest_inventory.json"

# The manifest filenames each ecosystem's *_dependency_edges.py can parse.
# Deliberately identical to the MANIFEST_KINDS each fetcher already used, so
# widening the *search* does not silently widen what gets parsed as well --
# the two changes stay separable.
ECOSYSTEM_KINDS = {
    "go": ["go.mod"],
    "js": ["package.json"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "rust": ["Cargo.toml"],
    "python": ["pyproject.toml", "requirements.txt"],
    "cpp": [".gitmodules", "vcpkg.json", "conanfile.py", "conanfile.txt"],
}

KIND_TO_ECOSYSTEM = {kind: eco for eco, kinds in ECOSYSTEM_KINDS.items() for kind in kinds}
ALL_KINDS = set(KIND_TO_ECOSYSTEM)

# A path component equal to any of these means the manifest below it belongs
# to a vendored copy of some other project, a build output tree, or an
# interpreter's installed-packages directory -- never to the repo itself.
#
# `deps`, `external`, `extern` and `subprojects` are on the list for the C/C++
# case specifically: a git submodule contributes no blobs to the tree, so a
# manifest actually present at one of those paths is a checked-in copy of the
# dependency's source, not a reference to it.
VENDORED_COMPONENTS = frozenset({
    "node_modules", "bower_components", "jspm_packages",
    "vendor", "vendored", "third_party", "thirdparty", "third-party", "3rdparty",
    "external", "externals", "extern", "deps", "subprojects", "Godeps",
    "site-packages", "dist-packages", "venv", ".venv", "virtualenv", ".tox", ".nox",
    "build", "_build", "dist", "out", "target", "cmake-build-debug", "cmake-build-release",
    ".git", ".gradle", ".idea", ".cache",
})

# Directories holding the project's own peripheral code rather than the
# project itself. These are NOT excluded -- an example's manifest is still
# something the repo declares -- but they sort last when a monorepo has more
# manifests than 42_scan_repo_manifests.py's fetch budget, so the budget is
# spent on the packages that describe the project.
PERIPHERAL_COMPONENTS = frozenset({
    "example", "examples", "sample", "samples", "demo", "demos", "showcase",
    "test", "tests", "testdata", "test-data", "fixtures", "__tests__", "e2e", "spec",
    "benchmark", "benchmarks", "bench", "docs", "doc", "website", "www", "site",
})

# The legacy per-language root caches, still read for any repo the scanner has
# not reached. {ecosystem: (cache dir, {cache file suffix: manifest filename})}.
# A zero-byte file in any of them is a real cached absence, not a miss.
LEGACY_CACHES = {
    "go": ("go_mod_cache", {".mod": "go.mod"}),
    "js": ("package_json_cache", {".json": "package.json"}),
    "rust": ("cargo_toml_cache", {".toml": "Cargo.toml"}),
    "java": ("java_manifest_cache", {
        ".pom.xml": "pom.xml",
        ".build.gradle": "build.gradle",
        ".build.gradle.kts": "build.gradle.kts",
    }),
    "python": ("python_manifest_cache", {
        ".pyproject.toml": "pyproject.toml",
        ".requirements.txt": "requirements.txt",
    }),
    "cpp": ("cpp_manifest_cache", {
        ".gitmodules": ".gitmodules",
        ".vcpkg.json": "vcpkg.json",
        ".conanfile.py": "conanfile.py",
        ".conanfile.txt": "conanfile.txt",
    }),
}


def is_vendored(path):
    """True if any directory component of `path` marks it as another
    project's manifest rather than this repo's own."""
    return any(part in VENDORED_COMPONENTS for part in path.split("/")[:-1])


def content_hash(text):
    """Short content hash, the identity of a manifest's bytes. Same idea as
    the intrinsic git object ids scripts/43_repo_refs.py reads, applied one
    file at a time: equal bytes are the same file, whoever is holding it."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def copied_hashes(inventory):
    """Content hashes that appear in more than one repository.

    A nested manifest with one of these hashes is a checked-in copy of another
    project rather than the holding repo's own declaration -- see the module
    docstring for why a directory-name rule cannot catch these."""
    holders = Counter()
    for repo, by_eco in inventory.items():
        seen = set()
        for paths in by_eco.values():
            # An inventory written before hashes were recorded stores a plain
            # list of paths. Nothing can be deduplicated from that, so it
            # contributes nothing rather than raising -- re-run
            # 42_scan_repo_manifests.py to regenerate it (no re-fetching: the
            # inventory is rebuilt from the raw cache already on disk).
            if isinstance(paths, dict):
                seen.update(paths.values())
        for h in seen:
            holders[h] += 1
    return {h for h, n in holders.items() if n > 1}


_COPIED_CACHE = {}


def copied_hash_set():
    """`copied_hashes` over the committed inventory, computed once.

    Empty when the inventory does not exist yet, which is the honest answer
    for a checkout where 42_scan_repo_manifests.py has never run: with no
    inventory there is nothing nested to drop either, since every manifest
    then comes from a legacy root-only cache."""
    if "set" not in _COPIED_CACHE:
        if INVENTORY_PATH.exists():
            _COPIED_CACHE["set"] = copied_hashes(json.loads(INVENTORY_PATH.read_text()))
        else:
            _COPIED_CACHE["set"] = set()
    return _COPIED_CACHE["set"]


def manifest_kind(path):
    """The manifest filename at the end of `path`, if it is one we parse."""
    filename = path.rsplit("/", 1)[-1]
    return filename if filename in ALL_KINDS else None


def cache_path(repo):
    owner, name = repo.split("/", 1)
    return MANIFEST_CACHE / f"{owner}__{name}.json"


def scanned_repos():
    """Every repo the scanner has cached a tree read for."""
    if not MANIFEST_CACHE.exists():
        return set()
    return {p.name[: -len(".json")].replace("__", "/", 1) for p in MANIFEST_CACHE.glob("*.json")}


def _scanned_manifests(repo, ecosystem):
    """{path: text} from the scanner's cache: this ecosystem's manifests, with
    vendored paths and nested copies of other projects' files dropped."""
    path = cache_path(repo)
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text())
    except ValueError:
        return None  # half-written; treated as unscanned so it is re-swept
    wanted = set(ECOSYSTEM_KINDS[ecosystem])
    copied = copied_hash_set()
    return {
        p: text for p, text in (cached.get("texts") or {}).items()
        if manifest_kind(p) in wanted
        and not is_vendored(p)
        and not ("/" in p and content_hash(text) in copied)
    }


def _legacy_manifests(repo, ecosystem):
    """{path: text} from the pre-scanner root-only cache for this ecosystem.

    Returns {} for a repo whose cache says "no manifest at root" (a zero-byte
    marker, or C++'s `.none`), and None when this repo was never fetched at
    all -- an absence the caller must not read as an empty manifest list."""
    dirname, suffixes = LEGACY_CACHES[ecosystem]
    owner, name = repo.split("/", 1)
    base = ROOT / "data/raw" / dirname / f"{owner}__{name}"
    found = {}
    any_cached = False
    for suffix, filename in suffixes.items():
        candidate = Path(str(base) + suffix)
        if not candidate.exists():
            continue
        any_cached = True
        text = candidate.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            found[filename] = text
    if Path(str(base) + ".none").exists():
        any_cached = True
    return found if any_cached else None


def load_manifests(ecosystem, repos):
    """{repo: {path: text}} for every repo that has at least one manifest of
    this ecosystem, preferring the scanner's cache and falling back to the
    legacy root-only cache for repos it has not covered.

    `repos` is the cohort to consider -- callers pass the full node set now
    rather than one language list, since that list is exactly the assumption
    this module exists to remove.
    """
    out = {}
    for repo in repos:
        found = _scanned_manifests(repo, ecosystem)
        if found is None:
            found = _legacy_manifests(repo, ecosystem) or {}
        if found:
            out[repo] = found
    return out


def iter_manifests(ecosystem, repos):
    """(repo, path, text) for every manifest of this ecosystem in the cohort.

    Flat rather than grouped by repo on purpose: it is the shape each
    *_dependency_edges.py loop already had when it read one file per repo, so
    those six scripts pick up multi-manifest, multi-ecosystem coverage without
    re-indenting the parsing they already do. Deterministic order, so a re-run
    resolves packages in the same sequence and the registry caches stay warm."""
    for repo, files in sorted(load_manifests(ecosystem, repos).items()):
        for path, text in sorted(files.items()):
            yield repo, path, text


def manifest_stats(ecosystem, repos):
    """(repos with a manifest, manifest files, files that are nested).

    The third number is the one worth printing: it is exactly the coverage no
    root-only probe could ever have reached."""
    loaded = load_manifests(ecosystem, repos)
    files = sum(len(v) for v in loaded.values())
    nested = sum(1 for v in loaded.values() for p in v if "/" in p)
    return len(loaded), files, nested


def coverage(ecosystem, repos):
    """(scanned, legacy, unknown) repo counts, for a script's summary line."""
    scanned = legacy = unknown = 0
    for repo in repos:
        if cache_path(repo).exists():
            scanned += 1
        elif _legacy_manifests(repo, ecosystem) is not None:
            legacy += 1
        else:
            unknown += 1
    return scanned, legacy, unknown
