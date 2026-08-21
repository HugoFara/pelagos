#!/usr/bin/env python3
"""Turn pom.xml/build.gradle(.kts) dependency declarations into real, directed
`repo --depends on--> repo` edges, the Java-ecosystem counterpart to
scripts/edges/dependency_edges.py's PyPI-based edges, scripts/edges/go_deps.py's
go.mod-based ones, and scripts/edges/js_deps.py's package.json-based ones
(see scripts/fetch/java_manifests.py for why Java needed a 3-way
pom.xml/build.gradle/build.gradle.kts probe instead of one fixed path).

Resolution, same "checked directly against real data first" discipline as the
other three ecosystems:

- Maven: a `groupId:artifactId` coordinate carries no repo-hosting
  information (same gap npm package names have), so each distinct coordinate
  needs one registry lookup: the artifact's maven-metadata.xml for its latest
  version, then a static fetch of that version's real .pom file, both from
  repo1.maven.org (Maven Central's file host), to read its `<scm><url>` (the
  scm connection URL, e.g. mockito/mockito, okhttp/okhttp resolve straight
  from this) or, when that's missing, its top-level `<url>` if that itself is
  a github.com link (needed for e.g. spring-core, whose own pom has no <scm>
  at all but a correct top-level <url>). Checked directly against 8 real
  coordinates before writing this and found a real, honest split: ordinary
  libraries (mockito-core, jackson-databind, okhttp, spring-core) resolve
  cleanly, while Apache-foundation libraries (commons-lang3, commons-io,
  httpclient) and some others (slf4j-api, gson) declare their real scm on
  the *foundation's own* git host (gitbox.apache.org, not GitHub) or via a
  parent POM this script doesn't chase -- correctly left unresolved rather
  than guessed, the direct Java-ecosystem analogue of go_deps.py's golang.org/x/*
  and gopkg.in/* gap.
- Only a POM's *own root-level* `<dependencies>` block counts, never the
  `<dependencyManagement>` block (version/scope pinning for children, not an
  actual usage -- the Maven analogue of excluding npm's devDependencies), and
  never `scope=test` entries. Coordinates containing an unresolved
  `${...}` property placeholder are skipped (no parent-POM property
  resolution attempted) rather than guessed.
- Gradle: no full Groovy/Kotlin parser (matching this project's existing
  "avoid heavy dependencies" bias, e.g. fastembed/ONNX over torch) -- a
  line-based scanner that tracks brace depth to stay inside the top-level
  `dependencies { ... }` block, and only honors real, non-test configurations
  (implementation/api/compile/runtimeOnly; testImplementation, compileOnly,
  annotationProcessor, kapt, etc. excluded, the Gradle analogue of Go's
  `// indirect` and npm's devDependencies filters). Recognizes the literal
  `"group:artifact:version"` / `"group:artifact"` string form and the
  `group: '...', name: '...'` map form (Groovy only).
  Known real coverage gap, not a bug: checked directly against
  square/okhttp and apache/kafka (both real, popular Gradle projects) and
  found neither is reachable by a literal-string scan alone. okhttp's real
  dependencies are declared via Gradle's version-catalog accessors
  (`libs.foo.bar`, resolved through gradle/libs.versions.toml) rather than
  literal coordinate strings; kafka's root build.gradle does declare real
  dependencies directly, but through its own bespoke `libs`-like extension,
  not the standard version catalog -- neither is a fixed, generically-
  resolvable convention the way go.mod/package.json are, so both are left as
  an honest gap rather than chased. Gradle repos that do write literal
  coordinates directly (older-style or smaller projects) resolve normally.
- Submodules count, not just the repo root. This is the fix for what was by
  far Java's largest gap: run over root manifests alone, only 192 of 811
  yielded a single dependency, because 185/196 zero-dependency POMs are
  aggregators carrying <modules> and 331/423 zero-dependency Gradle roots are
  multi-project roots carrying `allprojects` -- files whose job is to list and
  configure children, with every real dependency one directory down. Every
  non-root pom.xml/build.gradle[.kts] found by the manifest sweep is parsed
  with the same two parsers and unioned into the repo's coordinate set.
  (That sweep was Java-only when this was written --
  scripts/fetch/java_manifests.py -- and is now cohort-wide across all six
  ecosystems, scripts/fetch/repo_manifests.py; the Java behaviour described here
  is unchanged, it is just no longer a Java-only privilege.) Measured on a 25-repo random sample of the
  zero-dependency population before writing this: 24 of 25 go from zero
  dependencies to some.

  A repo's *own* submodules are the right scope here and the union is not
  double-counting: an edge's weight is the number of distinct coordinates
  linking the two repos, so a coordinate declared by six of a repo's modules
  contributes once, exactly as a coordinate declared twice in one file does.

  Modules in a multi-module build depend on *each other* by coordinate, and
  those are not repo-to-repo edges -- 21% of all (repo, coordinate) pairs turn
  out to be a repo naming an artifact it publishes itself. They are dropped by
  reading what each POM declares it publishes, before resolution; see
  pom_own_coordinate().

  Not every cached submodule manifest is one of the repo's own modules --
  build logic, test-fixture projects, vendored trees and Maven archetype
  templates all ship build files too. NON_PROJECT_DIRS lists what to drop and
  says how each category was found by inspection of the real sweep. The
  exclusion lives here rather than in 32 so it stays measurable and reversible
  without re-fetching anything; the run report prints what it removed.

Output shape mirrors dependency_edges.py/go_deps.py/js_deps.py's full-edge
tuples (source, target, weight, [coordinates]) so dependency_edges.py can fold
this file straight into its own combined top-K
prune pass alongside the PyPI/Go/JS tiers -- all the same semantic tier (a
real declared dependency), pruned together, not separately.

Usage: python3 scripts/edges/java_deps.py [out=data/processed/repo_java_dependency_edges.json]
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.identity import canonical_lookup  # noqa: E402
from lib.manifests import coverage, load_manifests, manifest_stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MAVEN_CACHE = ROOT / "data/raw/maven_central_cache"
MAVEN_FETCH_THROTTLE_S = 0.2  # same margin resolve_pypi_packages.py/js_deps.py use
MAVEN_RESOLVE_WORKERS = 4  # kept low on purpose -- see the resolution phase in main()

FAILED = object()  # "the lookup did not complete", as distinct from "it found nothing"

MANIFEST_KINDS = ["pom.xml", "build.gradle", "build.gradle.kts"]

# Submodule paths that are not one of the repo's own project modules. Each
# category was found by listing what the sweep actually cached, not guessed
# up front -- of 2465 module manifests, 94 fall in here:
#
#   61  test fixtures     jib-cli/src/integration-test/resources/.../build.gradle
#   20  build logic       buildSrc/build.gradle.kts
#    6  gradle dir        gradle/jacoco/build.gradle
#    5  vendored          third-party/aosp-dexutils/build.gradle
#    2  archetype template  .../src/main/resources/archetype-resources/pom.xml
#
# The test-fixture case is the same distinction the parsers already make when
# they drop scope=test and testImplementation: a throwaway project built to
# exercise the code is not a thing the code depends on. Build logic is the
# build's own plugin classpath, compiled before the project. Vendored trees
# and archetype templates describe someone else's project, not this one.
NON_PROJECT_DIRS = {"buildSrc", "build-logic", "node_modules",
                    "vendor", "third_party", "third-party", "archetype-resources"}
TEST_SOURCE_SET_RE = re.compile(r"(?:^|/)src/[^/]*[Tt]est[^/]*/")

GITHUB_URL_RE = re.compile(r'github\.com[:/]([^/\s]+)/([^/\s#?]+?)(?:\.git)?/?(?:[#?].*)?$')

GRADLE_REAL_CONFIGS = {"implementation", "api", "compile", "runtimeOnly", "runtime"}
GRADLE_STRING_DEP_RE = re.compile(
    r'^(?P<config>\w+)\s*[\(\s]\s*["\'](?P<group>[\w.\-]+):(?P<artifact>[\w.\-]+)(?::[^"\']*)?["\']'
)
GRADLE_MAP_DEP_RE = re.compile(
    r'^(?P<config>\w+)\s*\(?\s*group:\s*["\'](?P<group>[\w.\-]+)["\']\s*,\s*name:\s*["\'](?P<artifact>[\w.\-]+)["\']'
)


def github_owner_repo(value):
    if not value:
        return None
    m = GITHUB_URL_RE.search(value.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def local_tag(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_pom_dependencies(text):
    """Root-level <dependencies><dependency> entries only (never the nested
    <dependencyManagement><dependencies> block -- that's a distinct direct
    child of <project>, so a shallow, one-level scan naturally skips it),
    excluding scope=test and any ${...}-templated coordinate.

    <optional>true</optional> is deliberately NOT excluded, unlike npm's
    devDependencies/peerDependencies/optionalDependencies filter -- checked
    directly against mybatis-3's real pom, whose ognl/javassist/cglib/slf4j
    entries are almost all marked optional=true, and Maven's `optional` flag
    means "don't expose this transitively to *my* consumers", not "my own
    build doesn't really use this" -- mybatis genuinely compiles against all
    of them. Excluding scope=test is the real analogue of Go's `// indirect`
    and npm's devDependencies filter (this repo's own build vs. a testing
    concern); optional is an unrelated, transitivity-only annotation."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    deps = []
    for child in root:
        if local_tag(child.tag) != "dependencies":
            continue
        for dep in child:
            if local_tag(dep.tag) != "dependency":
                continue
            group = artifact = scope = None
            for f in dep:
                tag = local_tag(f.tag)
                if tag == "groupId":
                    group = (f.text or "").strip()
                elif tag == "artifactId":
                    artifact = (f.text or "").strip()
                elif tag == "scope":
                    scope = (f.text or "").strip()
            if not group or not artifact or scope == "test":
                continue
            if "${" in group or "${" in artifact:
                continue
            deps.append((group, artifact))
    return deps


def parse_gradle_dependencies(text):
    """Line-based scan of the top-level dependencies { ... } block, brace-
    depth tracked to know when we've left it. No full Groovy/Kotlin parser --
    see module docstring for the real, checked-directly coverage gap this
    implies for version-catalog-based and per-submodule-file projects."""
    deps = []
    in_block = False
    depth = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if not in_block:
            if re.match(r'^dependencies\s*\{', line):
                in_block = True
                depth = line.count("{") - line.count("}")
            continue
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            in_block = False
            continue
        for pattern in (GRADLE_STRING_DEP_RE, GRADLE_MAP_DEP_RE):
            m = pattern.match(line)
            if m and m.group("config") in GRADLE_REAL_CONFIGS:
                deps.append((m.group("group"), m.group("artifact")))
                break
    return deps


def maven_cache_path(group, artifact):
    return MAVEN_CACHE / f"{group}__{artifact}.json"


def fetch_maven_scm(group, artifact):
    """owner/repo, or None. Only *completed* lookups are cached.

    The distinction matters and was learned the hard way: the previous version
    cached every None, so a transport failure became a permanent "this
    coordinate has no GitHub repo". Running 7789 lookups in one pass got this
    client 403'd by search.maven.org, and the block was silently written into
    the cache as thousands of false negatives -- com.google.code.gson:gson,
    declared by 158 repos, cached as unresolvable. FAILED now propagates
    instead, so a failed lookup is simply retried on the next run."""
    cache_path = maven_cache_path(group, artifact)
    if cache_path.exists():
        text = cache_path.read_text()
        if not text.strip():
            return None  # cached "looked it up, no GitHub repo to be found"
        try:
            return json.loads(text)
        except ValueError:
            pass  # half-written by an interrupted run: fall through and redo it

    resolved = _resolve_maven_scm_live(group, artifact)
    if resolved is FAILED:
        return None
    MAVEN_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(resolved) if resolved else "")
    return resolved


def _maven_get(url):
    """(body, completed). completed is False only when the request itself
    failed; a genuine 404 counts as completed with a None body, because "Maven
    Central does not have this file" is a real answer worth caching."""
    req = urllib.request.Request(url, headers={"User-Agent": "pelagos-java-dependency-resolve"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        time.sleep(MAVEN_FETCH_THROTTLE_S)
        return data, True
    except urllib.error.HTTPError as err:
        time.sleep(MAVEN_FETCH_THROTTLE_S)
        return None, err.code == 404
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None, False


def _latest_version(metadata):
    """<release> if Maven Central names one, else <latest>, else the last
    listed <version>. Reading the artifact's own maven-metadata.xml replaced a
    search.maven.org/solrsearch query for the same fact: solrsearch is
    Sonatype's Solr service and it 403s a client that asks too often, while
    maven-metadata.xml is a static file on repo1.maven.org, the same CDN host
    the .pom below already comes from. One host, one failure mode."""
    try:
        root = ET.fromstring(metadata.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return None
    versioning = next((c for c in root if local_tag(c.tag) == "versioning"), None)
    if versioning is None:
        return None
    fallback = None
    for child in versioning:
        tag = local_tag(child.tag)
        if tag in ("release", "latest") and (child.text or "").strip():
            return child.text.strip()
        if tag == "versions":
            listed = [(v.text or "").strip() for v in child if (v.text or "").strip()]
            fallback = listed[-1] if listed else None
    return fallback


def _resolve_maven_scm_live(group, artifact):
    """owner/repo, None for "no GitHub repo", or FAILED if the lookup itself
    could not be completed (never cache that one)."""
    group_path = group.replace(".", "/")
    metadata, ok = _maven_get(f"https://repo1.maven.org/maven2/{group_path}/{artifact}/maven-metadata.xml")
    if not ok:
        return FAILED
    if not metadata:
        return None  # a real 404: not on Maven Central under this coordinate
    version = _latest_version(metadata)
    if not version:
        return None

    pom_data, ok = _maven_get(
        f"https://repo1.maven.org/maven2/{group_path}/{artifact}/{version}/{artifact}-{version}.pom"
    )
    if not ok:
        return FAILED
    if not pom_data:
        return None
    try:
        root = ET.fromstring(pom_data.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return None

    scm_url = top_url = None
    for child in root:
        tag = local_tag(child.tag)
        if tag == "scm":
            for f in child:
                if local_tag(f.tag) == "url":
                    scm_url = (f.text or "").strip()
        elif tag == "url":
            top_url = (child.text or "").strip()
    for candidate in (scm_url, top_url):
        resolved = github_owner_repo(candidate)
        if resolved:
            return resolved
    return None


def load_node_ids():
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    return canonical_lookup(list(top50) + list(extra))  # duplicate slugs resolved; see scripts/lib/identity.py


def find_root_manifest(files):
    """(text, kind) for the repo's root build file, in MANIFEST_KINDS priority
    order, or (None, None)."""
    for kind in MANIFEST_KINDS:
        if kind in files:
            return files[kind], kind
    return None, None


def parse_manifest(path_or_name, text):
    """Dispatch on the manifest's filename, for root and module files alike."""
    name = str(path_or_name).rsplit("/", 1)[-1]
    if name == "pom.xml":
        return parse_pom_dependencies(text)
    return parse_gradle_dependencies(text)


def pom_own_coordinate(text):
    """The (groupId, artifactId) this POM itself *publishes*, or None.

    A module POM routinely omits its own <groupId> and inherits the parent's,
    so the parent's is used as the fallback -- that is Maven's own rule, and
    without it most module coordinates come back half-empty.

    This exists to drop intra-repo references before resolution: in a
    multi-module build, modules depend on each other by coordinate, and 21% of
    all (repo, coordinate) pairs turn out to be a repo pointing at a module it
    publishes itself. Those are not repo-to-repo edges. Filtering them here
    rather than relying on the later `target == source` check is both cheaper
    (5810 Maven lookups instead of 10784, measured) and safer: an internal
    artifact whose Maven Central entry has since moved to a different repo
    would otherwise resolve into a real-looking edge that never existed."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    group = artifact = parent_group = None
    for child in root:
        tag = local_tag(child.tag)
        if tag == "groupId":
            group = (child.text or "").strip()
        elif tag == "artifactId":
            artifact = (child.text or "").strip()
        elif tag == "parent":
            for f in child:
                if local_tag(f.tag) == "groupId":
                    parent_group = (f.text or "").strip()
    group = group or parent_group
    return (group, artifact) if group and artifact else None


def is_project_module(path):
    """False for a cached manifest that isn't one of the repo's own modules."""
    dirs = path.split("/")[:-1]
    if any(d in NON_PROJECT_DIRS for d in dirs):
        return False
    if dirs and dirs[0] == "gradle":  # the wrapper/convention-plugin directory
        return False
    return not TEST_SOURCE_SET_RE.search(path)


def split_modules(files):
    """({nested path: text} for the repo's own project modules, count skipped).

    The nested half of what scripts/fetch/repo_manifests.py swept, minus the
    build-logic directories is_project_module() rejects. Java is the one
    ecosystem that already read nested manifests before that sweep existed
    (java_manifests.py's Java-only submodule pass); it now shares the
    cohort-wide one, which is the same data for Java repos and newly
    non-empty for every repo whose dominant language is something else."""
    nested = {p: t for p, t in files.items() if "/" in p}
    kept = {p: t for p, t in nested.items() if is_project_module(p)}
    return kept, len(nested) - len(kept)


def main(out_path="data/processed/repo_java_dependency_edges.json"):
    canon_lookup = load_node_ids()
    cohort = sorted(set(canon_lookup.values()))
    by_repo = load_manifests("java", cohort)

    parsed_pom, parsed_gradle, no_manifest = 0, 0, 0
    direct_dep_total = 0
    edge_coords = defaultdict(set)  # (source, target) -> {"group:artifact", ...}
    repo_coords = {}  # repo -> sorted ["group:artifact", ...]

    # Root-only vs. root+modules, tracked side by side so the run report can
    # say what the submodule sweep actually bought rather than asserting it.
    module_files, build_logic_skipped, intra_repo_refs = 0, 0, 0
    repos_with_deps, repos_with_deps_root_only, repos_module_only = 0, 0, 0

    for repo in cohort:
        files = by_repo.get(repo)
        if not files:
            no_manifest += 1
            continue
        root_text, kind = find_root_manifest(files)
        module_texts, skipped = split_modules(files)
        build_logic_skipped += skipped
        module_files += len(module_texts)

        manifests = dict(module_texts)
        root_deps = []
        if root_text is not None:
            text = root_text
            manifests[kind] = text
            root_deps = parse_manifest(kind, text)
            if kind == "pom.xml":
                parsed_pom += 1
            else:
                parsed_gradle += 1

        deps, published = set(root_deps), set()
        for path, text in manifests.items():
            deps.update(parse_manifest(path, text))
            if path.rsplit("/", 1)[-1] == "pom.xml":
                own = pom_own_coordinate(text)
                if own:
                    published.add(own)
        internal = deps & published
        intra_repo_refs += len(internal)
        deps -= internal
        root_deps = [d for d in root_deps if d not in internal]

        if deps:
            repos_with_deps += 1
            if root_deps:
                repos_with_deps_root_only += 1
            else:
                repos_module_only += 1
        if not deps:
            continue
        repo_coords[repo] = sorted(f"{g}:{a}" for g, a in deps)
        direct_dep_total += len(deps)

    # Resolution is its own phase now rather than inline in the loop above.
    # The submodule sweep multiplied the distinct-coordinate count by more than
    # an order of magnitude, and each unresolved coordinate costs two Maven
    # Central round trips (solrsearch for the latest version, then that
    # version's .pom for its <scm>), so serially this became the slowest part
    # of the whole pipeline. Same reasoning as java_manifests.py's threaded
    # sweep: the work is latency-bound and every coordinate writes only its own
    # cache file, so a small pool is safe. Deliberately smaller than that one's
    # -- Maven Central is a
    # volunteer-funded public mirror, not GitHub's API, and the throttle stays.
    all_coords = sorted({c for coords in repo_coords.values() for c in coords})
    uncached = [c for c in all_coords if not maven_cache_path(*c.split(":", 1)).exists()]
    if uncached:
        print(f"resolving {len(uncached)} of {len(all_coords)} coordinates against Maven Central "
              f"({len(all_coords) - len(uncached)} already cached)...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=MAVEN_RESOLVE_WORKERS) as pool:
        list(pool.map(lambda c: fetch_maven_scm(*c.split(":", 1)), uncached))
    coord_resolved = {c: fetch_maven_scm(*c.split(":", 1)) for c in all_coords}  # now all disk-cached
    resolved_count = sum(1 for v in coord_resolved.values() if v)
    unresolved_count = len(coord_resolved) - resolved_count

    for repo, coords in repo_coords.items():
        source = canon_lookup.get(repo.lower(), repo)
        for coord in coords:
            resolved = coord_resolved.get(coord)
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
    # (mirrors package_to_repo.json / go_module_to_repo.json / js_package_to_repo.json).
    coord_map = {k: v for k, v in coord_resolved.items() if v}
    (ROOT / "data/processed/java_coord_to_repo.json").write_text(
        json.dumps(coord_map, indent=0, sort_keys=True))

    repos_with, files_seen, nested = manifest_stats("java", cohort)
    scanned, legacy, unscanned = coverage("java", cohort)
    touched = {n for e in edges for n in (e[0], e[1])}
    sources = {e[0] for e in edges}
    targets = {e[1] for e in edges}
    print(
        f"{repos_with}/{len(cohort)} cohort repos have a Java manifest across {files_seen} files "
        f"({nested} nested); {parsed_pom} contributed a root pom.xml, {parsed_gradle} a root "
        f"build.gradle[.kts], {no_manifest} have none at all. {module_files} module manifests "
        f"parsed ({build_logic_skipped} skipped as build logic / test fixtures / vendored trees; "
        f"{scanned} repos from the tree sweep, {legacy} from the legacy root-only cache, "
        f"{unscanned} never fetched). "
        f"{intra_repo_refs} (repo, coordinate) pairs dropped as references to a module the repo "
        f"publishes itself. "
        f"{repos_with_deps} repos declare at least one dependency "
        f"({repos_with_deps_root_only} already did from the root file alone, "
        f"{repos_module_only} are reachable only through their submodules), "
        f"{direct_dep_total} distinct (repo, coordinate) pairs, {len(coord_resolved)} distinct group:artifact "
        f"coordinates ({resolved_count} resolved to a GitHub repo, {unresolved_count} left "
        f"unresolved/non-GitHub) -> {len(edges)} dependency edges ({len(sources)} source repos -> "
        f"{len(targets)} target repos, {len(touched)} nodes total) -> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
