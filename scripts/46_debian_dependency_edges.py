#!/usr/bin/env python3
"""Cross-language dependency edges, from Debian's package graph.

Every dependency source in this pipeline until now stops at the edge of its
own language. npm knows `sharp` needs `libvips` only as an opaque string; PyPI
knows `Pillow` needs libjpeg not at all. No language registry crosses the C
boundary, because none of them package C libraries. That is a structural gap,
not a coverage gap, and it is why the bottom of the trophic axis was wrong:
`python-pillow/Pillow` had a dependency in-degree of 1,239 while
`madler/zlib`, which it genuinely depends on, had effectively none. The most
depended-on repos in the cohort were all language-level libraries -- pytorch,
tqdm, requests, serde -- with nothing underneath them, because nothing in the
data could express what is underneath them.

Distributions are the only place those edges exist, because a distribution is
exactly the thing that has to resolve them. Debian's binary `Depends` field is
a real, declared, machine-readable statement that this shipped artifact needs
that one, across every language at once.

## Why Debian rather than Nixpkgs

Nixpkgs is the cleaner graph -- derivations evaluate to an exact dependency
closure with no parsing -- and it is the better long-term source. It also
requires `nix` on the machine to evaluate, which is a heavy dependency for a
pipeline whose other inputs are all plain HTTP. Debian publishes the same
relation as two static files behind no auth:

    dists/stable/main/binary-amd64/Packages.xz   68,750 binary packages, `Depends`
    dists/stable/main/source/Sources.xz          37,588 source packages, homepage/Vcs

Runtime `Depends` is used, never `Build-Depends`. Build dependencies are the
toolchain -- compilers, autotools, test harnesses -- which is a different
relation from "this program needs this library to run", and including it would
put gcc above everything rather than beneath it. That is the same call this
codebase already makes for npm `devDependencies`, Maven `scope=test` and Go's
`// indirect`.

## Resolving a package to a repository

A Debian source package is not a repo, so each one has to be resolved, and the
distribution of difficulty here is the whole problem:

    42.3% of source packages carry a github.com URL in Homepage or Vcs-*
    ...but those cover only 11.2% of all reverse-dependency mass

Auto-resolution finds the leaves and misses the roots. glibc (23,246
reverse-deps), gcc (16,285), zlib (2,787), openssl (1,108) and ncurses (982)
publish on gnu.org, zlib.net and invisible-island.net. So the top ~150
unresolved packages by reverse-dependency count are curated in
data/repo-lists/distro_upstreams.txt, which adds ~47% of reverse-dep mass on
top of the automatic 11.2%; past that the curve flattens and this stops.

## Corroboration

The curated file is a candidate generator, never evidence -- the same rule
data/repo-lists/upstream_origins.txt follows. Each mapping is corroborated by
**version-tag match**: the upstream version Debian is shipping has to appear
among the repository's own git tags. `glibc` 2.41 against a `glibc-2.41` tag
in bminor/glibc is a real check that the repo publishes the software Debian is
packaging, and it is one `git ls-remote --tags` per candidate. A mapping that
cannot be corroborated is dropped and printed with its reason.

## Name matching against the cohort, corroborated the same way

Auto-resolution and curation between them still missed something large and
obvious: 565 Debian source packages are repos *already in this cohort*, and
resolving them is what actually produces the cross-language edges this whole
script exists for. `pillow` is the motivating example -- its Debian Homepage
is `python-pillow.github.io`, a GitHub Pages URL rather than a repository
one, so the URL regex skips it and `python3-pil` never links to
`libjpeg62-turbo` even though Debian states the dependency outright.

So a third layer proposes a cohort repo whose name matches the source package
(after stripping Debian's `python3-`, `node-`, `golang-`, `lib` and
trailing-version conventions), and corroborates it with the same version-tag
check the curated file gets. That is a candidate generator plus verification,
not a name heuristic trusted on its own, and the verification earns its keep:
Debian's `glance` is OpenStack's image service while the cohort's
`glanceapp/glance` is an unrelated dashboard, and the version check is what
separates them. Ambiguous names -- two cohort repos ending in the same name --
are refused rather than picked between.

## Which repos become nodes

Not all of them. 14,714 auto-resolved repos are outside the current cohort,
and adding all of them would be exactly the "200,000 nodes of which 80% is
noise" failure this project's rendering budget cannot absorb and its thesis
does not need. A resolved repo becomes a node only if it is already in the
cohort, or if it carries at least MIN_REVERSE_DEPS reverse-dependencies --
1,184 repos at the default of 5, which is the load-bearing part of the
distribution and nothing else.

Writes data/processed/repo_debian_dependency_edges.json in the same
(source, target, weight, [packages]) shape every other ecosystem emits, so
11_dependency_edges.py folds it into the one shared prune pass; plus
data/processed/debian_source_to_repo.json (the resolution table) and
data/repo-lists/distro_extra_repos.txt (the nodes this wants added, for
47_fetch_distro_repo_stats.py to fetch stats for).

Usage: python3 scripts/46_debian_dependency_edges.py [min_reverse_deps=5]
"""
import json
import lzma
import re
import subprocess
import sys
import threading
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import canonical_lookup  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEBIAN_CACHE = ROOT / "data/raw/debian_cache"
TAG_CACHE = ROOT / "data/raw/debian_tag_cache"
CURATED_PATH = ROOT / "data/repo-lists/distro_upstreams.txt"
FORGE_PATH = ROOT / "data/repo-lists/nongithub_origins.txt"
OUT_FORGE = ROOT / "data/repo-lists/forge_extra_nodes.txt"
OUT_EDGES = ROOT / "data/processed/repo_debian_dependency_edges.json"
OUT_MAP = ROOT / "data/processed/debian_source_to_repo.json"
OUT_EXTRA = ROOT / "data/repo-lists/distro_extra_repos.txt"

MIRROR = "https://deb.debian.org/debian/dists/stable/main"
INDEXES = {
    "Packages.xz": f"{MIRROR}/binary-amd64/Packages.xz",
    "Sources.xz": f"{MIRROR}/source/Sources.xz",
}

MIN_REVERSE_DEPS = 5  # for admitting a repo that is not already a cohort node
LS_REMOTE_WORKERS = 8
LS_REMOTE_TIMEOUT_S = 120

GITHUB_URL_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s#?]+?)(?:\.git)?/?$")
DEP_NAME_RE = re.compile(r"^([a-z0-9][a-z0-9+.-]*)")
# Upstreams do not agree on how to write a version into a tag name, so a tag
# is scanned with several patterns and every candidate is tried. The dotted
# and dashed forms have to stay separate rather than merge into one character
# class: a single pattern allowing both matches "2-10" inside `pcre2-10.46`
# (the project name's own digit, glued to the release) and never reaches the
# real "10.46". Run separately, the dotted pattern finds 10.46 in pcre2-10.46
# and the dashed one finds 2-13-3 in freetype's VER-2-13-3.
VERSION_PATTERNS = (
    re.compile(r"(\d+(?:[._]\d+)+)"),   # 1.2.3, curl-8_14_1, glibc-2.41
    re.compile(r"(\d+(?:-\d+)+)"),      # freetype VER-2-13-3
    re.compile(r"(?<!\d)(\d{6,8})(?!\d)"),  # date releases, e.g. re2 20240702
)


def version_candidates(text):
    """Every version a tag or version string could be encoding, normalised to
    dot separators."""
    out = set()
    for pattern in VERSION_PATTERNS:
        for found in pattern.findall(text or ""):
            out.add(found.replace("_", ".").replace("-", ".").strip("."))
    # Date releases are written both ways: Debian ships re2 as 20240702 while
    # the repo tags it 2024-07-02. Add the compact form of any YYYY.MM.DD
    # candidate so the two meet.
    for candidate in list(out):
        parts = candidate.split(".")
        if len(parts) == 3 and [len(p) for p in parts] == [4, 2, 2]:
            out.add("".join(parts))
    return {c for c in out if c}


def version_core(version):
    """The comparable numeric core of a version string. Longest candidate, so
    `21.0.11+10` compares on 21.0.11 rather than on a leading fragment."""
    candidates = version_candidates(version)
    return max(candidates, key=len) if candidates else ""

_lock = threading.Lock()


def fetch_indexes():
    DEBIAN_CACHE.mkdir(parents=True, exist_ok=True)
    for name, url in INDEXES.items():
        path = DEBIAN_CACHE / name
        if path.exists():
            continue
        print(f"  downloading {name}...", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=300) as response:
            path.write_bytes(response.read())


def paragraphs(path):
    """RFC822-ish stanzas, the format both Debian indexes use."""
    with lzma.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        current, key = {}, None
        for line in handle:
            if not line.strip():
                if current:
                    yield current
                current, key = {}, None
                continue
            if line[0] in " \t":
                if key:
                    current[key] += " " + line.strip()
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                current[key] = value.strip()
        if current:
            yield current


def parse_dependencies(field):
    """Package names from a Depends field, taking the first of each
    alternative group. `libfoo (>= 1.2) | libfoo-alt, libbar` -> [libfoo, libbar].

    Only the first alternative counts: it is the one Debian's resolver prefers
    and the one actually installed in the default case, so it is the real
    edge rather than a menu of possible ones."""
    out = []
    for alternative in (field or "").split(","):
        match = DEP_NAME_RE.match(alternative.split("|")[0].strip())
        if match:
            out.append(match.group(1))
    return out


def upstream_version(debian_version):
    """The upstream part of a Debian version: strip the epoch, the Debian
    revision, and Debian's own repackaging suffixes."""
    version = (debian_version or "").split(":", 1)[-1]
    version = re.split(r"-[^-]*$", version)[0]
    version = re.split(r"[+~](?:really|dfsg|ds|git|repack|orig)", version)[0]
    return version.strip()


def load_debian():
    binaries, sources = {}, {}
    for stanza in paragraphs(DEBIAN_CACHE / "Packages.xz"):
        name = stanza.get("Package")
        if not name:
            continue
        binaries[name] = {
            "source": (stanza.get("Source") or name).split(" ")[0],
            "depends": parse_dependencies(stanza.get("Depends")),
        }
    for stanza in paragraphs(DEBIAN_CACHE / "Sources.xz"):
        name = stanza.get("Package")
        if not name:
            continue
        sources[name] = {
            "homepage": stanza.get("Homepage", ""),
            "vcs": stanza.get("Vcs-Git", "") or stanza.get("Vcs-Browser", ""),
            "version": upstream_version(stanza.get("Version", "")),
        }
    return binaries, sources


def auto_resolve(source):
    """owner/repo from a source package's own Homepage/Vcs-*, or None."""
    for field in (source.get("homepage", ""), source.get("vcs", "")):
        match = GITHUB_URL_RE.search(field.strip())
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return None


def github_url(repo):
    return f"https://github.com/{repo}.git"


def forge_node_id(url):
    """The node id for a non-GitHub origin: `host/path`.

    A first segment containing a dot means a forge host rather than a GitHub
    owner, which is unambiguous rather than a convention -- GitHub owner names
    are [A-Za-z0-9-]+ and cannot contain a dot (checked against every node in
    the cohort). The full path is kept rather than the last segment alone, so
    `xorg/lib/libx11` and `mesa/drm` stay distinguishable from anything else
    on the same host.

    A leading `git/` path segment is dropped: sourceware serves
    sourceware.org/git/elfutils.git, where `git` is the cgit mount point and
    not part of the project's name."""
    rest = url.removeprefix("https://").removeprefix("http://").removesuffix(".git")
    host, _, path = rest.partition("/")
    path = re.sub(r"^git/", "", path).strip("/")
    return f"{host}/{path}" if path else host


def load_forge_origins():
    """{debian source package: origin URL} for projects with no GitHub repo."""
    if not FORGE_PATH.exists():
        return {}
    out = {}
    for line in FORGE_PATH.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def cohort_name_index(canon):
    """{bare repo name: canonical id} for unambiguous names only.

    A name owned by two different cohort repos is dropped rather than
    arbitrated: `foo/parser` and `bar/parser` give no reason to prefer either,
    and picking one would be exactly the guess this layer is designed to avoid."""
    owners = defaultdict(set)
    for node in set(canon.values()):
        owners[node.split("/", 1)[1].lower()].add(node)
    return {name: next(iter(nodes)) for name, nodes in owners.items() if len(nodes) == 1}


DEBIAN_PREFIX_RE = re.compile(r"^(?:python3?-|node-|golang-|ruby-|r-cran-|rust-|lib)")
DEBIAN_SUFFIX_RE = re.compile(r"(?:[0-9._]+|-dev|-bin|-utils|-tools)$")


def name_candidate(package, names):
    """A cohort repo whose bare name matches this Debian source package."""
    seen = set()
    for key in (package, DEBIAN_PREFIX_RE.sub("", package)):
        for variant in (key, DEBIAN_SUFFIX_RE.sub("", key)):
            variant = variant.strip("-.").lower()
            if variant and variant not in seen:
                seen.add(variant)
                if variant in names:
                    return names[variant]
    return None


def load_curated():
    if not CURATED_PATH.exists():
        return {}
    out = {}
    for line in CURATED_PATH.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def origin_tags(url):
    """Every tag name an origin publishes, cached.

    Takes a URL rather than a GitHub slug, which is the whole reason
    non-GitHub nodes are possible at all: `git ls-remote` is forge-independent
    and unauthenticated, so gitlab.freedesktop.org, gitlab.gnome.org and
    sourceware answer it exactly as github.com does. The corroboration rule
    below therefore needs no special case for where a project lives."""
    TAG_CACHE.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]+", "_", url.removeprefix("https://").removesuffix(".git"))
    cache_path = TAG_CACHE / f"{key}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except ValueError:
            pass
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--tags", url],
            capture_output=True, text=True, timeout=LS_REMOTE_TIMEOUT_S,
            env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true", "PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None  # not cached: a transient failure must not become a permanent answer
    tags = sorted({
        line.split("refs/tags/", 1)[1].removesuffix("^{}")
        for line in out.stdout.splitlines() if "refs/tags/" in line
    })
    cache_path.write_text(json.dumps(tags))
    return tags


def corroborate(package, url, version):
    """(ok, reason). The upstream version Debian ships must appear in one of
    the origin's own tags.

    Takes an origin URL rather than a GitHub slug, which is what lets one rule
    cover github.com, gitlab.freedesktop.org, gitlab.gnome.org and sourceware
    alike: `git ls-remote` does not care which forge answers it."""
    if not version:
        return False, "no upstream version in the Debian index"
    tags = origin_tags(url)
    if tags is None:
        return False, "could not read the origin's tags"
    if not tags:
        return False, "origin publishes no tags"
    # Debian may ship an older release than HEAD, so any tag may match, not
    # just the newest.
    #
    # Version *extraction* rather than character-stripping, which was a real
    # bug: stripping non-digits from `pcre2-10.46` leaves the project name's
    # own "2" glued to the front ("210.46") and the tag never matches, and
    # `curl-8_14_1` loses its separators entirely. Both are correct mappings
    # that were being rejected. Tags are scanned for a dotted or
    # underscored numeric run instead, and underscores normalised to dots, so
    # curl-8_14_1, pcre2-10.46, v1.2.3, glibc-2.41 and V_9_9_P1 all yield the
    # version they actually encode.
    core = version_core(version)
    if not core:
        return False, f"version {version!r} has no numeric core"
    for tag in tags:
        for candidate in version_candidates(tag):
            if candidate == core or candidate.startswith(core + ".") \
                    or core.startswith(candidate + "."):
                return True, ""
    return False, f"version {version} matches none of the origin's {len(tags)} tags"


def main(min_reverse_deps=MIN_REVERSE_DEPS):
    min_reverse_deps = int(min_reverse_deps)
    fetch_indexes()
    binaries, sources = load_debian()
    print(f"{len(binaries)} binary packages, {len(sources)} source packages", file=sys.stderr)

    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    canon = canonical_lookup(list(top50) + list(extra))

    # Reverse-dependency count per source package, the ranking this uses
    # everywhere: for what to curate, and for what deserves to be a node.
    binary_rdeps = Counter()
    for entry in binaries.values():
        for dependency in entry["depends"]:
            binary_rdeps[dependency] += 1
    source_rdeps = Counter()
    for binary, count in binary_rdeps.items():
        source_rdeps[binaries.get(binary, {}).get("source", binary)] += count

    # Resolve. Curated entries are corroborated; automatic ones come from the
    # package's own declared URL and need no corroboration -- the package is
    # naming its own homepage, which is not a guess made here.
    curated = load_curated()
    resolved, rejected = {}, []
    candidates = [(pkg, repo) for pkg, repo in sorted(curated.items()) if pkg in sources]
    missing = [pkg for pkg in curated if pkg not in sources]

    print(f"corroborating {len(candidates)} curated mappings against upstream tags...",
          file=sys.stderr)

    def check(item):
        package, repo = item
        ok, reason = corroborate(package, github_url(repo), sources[package]["version"])
        return package, repo, ok, reason

    with ThreadPoolExecutor(max_workers=LS_REMOTE_WORKERS) as pool:
        for package, repo, ok, reason in pool.map(check, candidates):
            if ok:
                resolved[package] = repo
            else:
                rejected.append((package, repo, reason))

    curated_count = len(resolved)
    auto_count = 0
    for package, source in sources.items():
        if package in resolved:
            continue
        found = auto_resolve(source)
        if found:
            resolved[package] = found
            auto_count += 1

    # Third layer: cohort repos Debian packages under a name the URL regex
    # could not reach. Corroborated exactly like the curated file.
    names = cohort_name_index(canon)
    name_candidates = [
        (package, name_candidate(package, names))
        for package in sorted(sources) if package not in resolved
    ]
    name_candidates = [(p, r) for p, r in name_candidates if r]
    print(f"corroborating {len(name_candidates)} name matches against the cohort...",
          file=sys.stderr)

    def check_name(item):
        package, repo = item
        ok, reason = corroborate(package, github_url(repo), sources[package]["version"])
        return package, repo, ok, reason

    name_count = 0
    name_rejected = 0
    with ThreadPoolExecutor(max_workers=LS_REMOTE_WORKERS) as pool:
        for package, repo, ok, _reason in pool.map(check_name, name_candidates):
            if ok:
                resolved[package] = repo
                name_count += 1
            else:
                name_rejected += 1

    # Fourth layer: projects with no GitHub repository at all. These get a
    # forge-host node id rather than an owner/name slug -- see forge_node_id.
    forge_candidates = [(pkg, url) for pkg, url in sorted(load_forge_origins().items())
                        if pkg in sources and pkg not in resolved]
    print(f"corroborating {len(forge_candidates)} non-GitHub origins...", file=sys.stderr)

    def check_forge(item):
        package, url = item
        ok, reason = corroborate(package, url, sources[package]["version"])
        return package, url, ok, reason

    forge_origins = {}  # node id -> origin url
    forge_count = 0
    with ThreadPoolExecutor(max_workers=LS_REMOTE_WORKERS) as pool:
        for package, url, ok, reason in pool.map(check_forge, forge_candidates):
            if ok:
                node = forge_node_id(url)
                resolved[package] = node
                forge_origins[node] = url
                forge_count += 1
            else:
                rejected.append((package, url, reason))

    print(f"resolved {len(resolved)} source packages "
          f"({curated_count} curated + corroborated, {auto_count} from the package's own "
          f"Homepage/Vcs, {name_count} by corroborated name match against the cohort -- "
          f"{name_rejected} name matches refused by the version check, "
          f"{forge_count} on non-GitHub forges), "
          f"{len(rejected)} candidates rejected", file=sys.stderr)

    # Which resolved repos are allowed to be nodes.
    repo_rdeps = Counter()
    for package, repo in resolved.items():
        repo_rdeps[repo] += source_rdeps.get(package, 0)
    # An edge endpoint must be a repo that is actually a node *now*. A repo
    # that merely deserves to be one is written to OUT_EXTRA for
    # 47_fetch_distro_repo_stats.py to add, and only becomes an endpoint on
    # the next run, once it exists.
    #
    # Admitting a proposed repo directly was a real bug: 47 cannot add every
    # repo it is handed (some 404, some are renamed and land under a different
    # name), so 723 edge endpoints named repos that were never in the
    # aggregates. build_web_explorer.py caught it -- those endpoints could not
    # be interned and it said so -- but they would have rendered as edges
    # pointing at nothing.
    admitted, added, forge_added = {}, [], []
    for repo, count in repo_rdeps.items():
        node = canon.get(repo.lower())
        if node:
            admitted[repo] = node
        elif repo in forge_origins:
            # A corroborated non-GitHub project. Nothing else in the dataset
            # can stand in for it -- there is no mirror to fall back to, which
            # is exactly why it was missing -- so it is admitted on the same
            # reverse-dependency threshold and carries edges immediately.
            if count >= min_reverse_deps:
                admitted[repo] = repo
                forge_added.append(repo)
        elif count >= min_reverse_deps:
            added.append(repo)
    print(f"{len(admitted)} resolved repos are cohort nodes and can carry edges; "
          f"{len(added)} more deserve to be (>= {min_reverse_deps} reverse-deps) and are "
          f"written out for 47 to add", file=sys.stderr)

    def node_of(binary):
        source = binaries.get(binary, {}).get("source", binary)
        repo = resolved.get(source)
        return admitted.get(repo) if repo else None

    edge_packages = defaultdict(set)
    for binary, entry in binaries.items():
        source_node = node_of(binary)
        if not source_node:
            continue
        for dependency in entry["depends"]:
            target = node_of(dependency)
            if target and target != source_node:
                edge_packages[(source_node, target)].add(dependency)

    edges = sorted(
        ([a, b, len(pkgs), sorted(pkgs)] for (a, b), pkgs in edge_packages.items()),
        key=lambda e: -e[2],
    )
    OUT_EDGES.write_text(json.dumps(edges, separators=(",", ":")))
    OUT_MAP.write_text(json.dumps(
        {p: r for p, r in sorted(resolved.items()) if r in admitted}, indent=0, sort_keys=True))
    OUT_EXTRA.write_text("\n".join(sorted(added)) + "\n")
    OUT_FORGE.write_text("".join(
        f"{node}\t{forge_origins[node]}\n" for node in sorted(forge_added)))

    touched = {n for e in edges for n in (e[0], e[1])}
    print(f"\n{len(edges)} Debian-derived dependency edges over {len(touched)} nodes "
          f"-> {OUT_EDGES.relative_to(ROOT)}", file=sys.stderr)
    print(f"{len(added)} repos to add to the cohort -> {OUT_EXTRA.relative_to(ROOT)}",
          file=sys.stderr)
    print(f"{len(forge_added)} non-GitHub forge nodes -> {OUT_FORGE.relative_to(ROOT)}",
          file=sys.stderr)

    in_degree = Counter(e[1] for e in edges)
    print("\nmost depended-on nodes in the Debian graph (the bottom of the stack):",
          file=sys.stderr)
    for repo, count in in_degree.most_common(15):
        print(f"  {count:5d}  {repo}", file=sys.stderr)

    if rejected:
        print(f"\n{len(rejected)} curated candidates rejected "
              f"(a candidate is never evidence):", file=sys.stderr)
        for package, repo, reason in sorted(rejected):
            print(f"  {package} !~ {repo}: {reason}", file=sys.stderr)
    if missing:
        print(f"\n{len(missing)} curated packages are not in this Debian release: "
              f"{', '.join(sorted(missing))}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
