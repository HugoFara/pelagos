#!/usr/bin/env python3
"""Real `repo --depends on--> repo` edges from declared Python dependencies.

Reads every pyproject.toml / requirements.txt in the cohort, at any depth, via
scripts/manifests.py -- not one file at the root of the repos GitHub's
`language:` facet labelled Python. That facet reports a repo's *dominant*
language, one value per repo, so under the old scoping a repo could not
contribute Python dependencies unless Python happened to be its biggest
language by bytes. Vendored trees and byte-identical copies of other
projects' manifests are dropped by that module before anything is parsed
here; see its docstring for the measurement behind the change.

The pyproject-wins-unless-empty priority is resolved per *directory* now,
not once per repo: a nested package has its own pair of files and its own
answer to which of the two is real.

Fifth and last ecosystem folded into 11_dependency_edges.py's combined
dependency tier, after Go (29), JS/TS (31), Java (33) and Rust (36). Closes
the odd-looking gap where the 68 GitHub-search-sourced Python repos had zero
dependency edges in a project whose original signal is PyPI-based -- those
repos were never in the SemRepo dump the usedPackage triples come from.

Resolution reuses what 09_resolve_packages.py already built: the same
PyPI JSON API, the same project_urls/home_page walk, and crucially the same
data/raw/pypi_cache/ directory, so any package the original 186-package
resolve already fetched costs nothing here.

## What counts as a dependency

pyproject.toml, in priority order:
  * [project].dependencies -- PEP 621, the main case (home-assistant/core
    50, langflow 20, docling 8, whisper 7).
  * [project].optional-dependencies -- extras. Included, minus
    dev-flavoured group names (see DEV_GROUPS). Skipping these wholesale
    would be wrong: yt-dlp/yt-dlp declares ZERO [project].dependencies and
    keeps its real runtime set in the `default` extra. Same call already
    made for Cargo's `optional = true` and Maven's <optional>true</optional>
    -- a feature-gated dependency is still genuinely depended on.
  * [tool.poetry.dependencies] -- older poetry layout, `python` skipped
    (it pins the interpreter, not a package).

Excluded: [dependency-groups] (PEP 735) entirely. Unlike extras, these exist
specifically for local development and are never installed by a consumer --
yt-dlp's are build/static-analysis/test, docling's are typecheck/dev. The
direct analogue of npm devDependencies, Maven scope=test and Cargo
[dev-dependencies]. Poetry's [tool.poetry.group.*] is skipped for the same
reason.

requirements.txt: one PEP 508 requirement per line. Comments, pip flags
(-r/-e/--index-url/...) and bare URL lines are skipped -- gpt_academic's
first line is a raw .whl URL, which is a real install target but names no
resolvable PyPI project.

setup.py is not read at all; see 37's docstring for why (two repos, and the
alternatives are executing repo code or AST-guessing a non-literal).

Package names are normalized per PEP 503 (lowercase, runs of -_. collapsed
to a single -) before dedupe, so `PyYAML`/`pyyaml` and `typing_extensions`/
`typing-extensions` resolve once rather than three times. The un-normalized
cache path is still checked first so 09's existing entries stay warm.

Output shape matches 29/31/33/36: [source, target, weight, [package names]],
weight = how many distinct packages of that target this repo depends on.

Usage: python3 scripts/38_python_dependency_edges.py [out=data/processed/repo_python_dependency_edges.json]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import canonical_lookup  # noqa: E402
from manifests import load_manifests, manifest_stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PYPI_CACHE = ROOT / "data/raw/pypi_cache"  # shared with 09_resolve_packages.py
PYPI_FETCH_THROTTLE_S = 0.1  # same margin 09_resolve_packages.py uses

MANIFEST_KINDS = ["pyproject.toml", "requirements.txt"]

GITHUB_RE = re.compile(r'https?://github\.com/([^/\s#]+)/([^/\s#?]+?)(?:\.git)?/?(?:[#?].*)?$')
URL_KEYS = ("Repository", "Source", "Source Code", "Code", "GitHub", "Homepage", "Home")

# Extra/group names that mean "for working on this project", not "for using
# it". Matched case-insensitively against the whole extra name.
DEV_GROUPS = {
    "dev", "devel", "develop", "development", "test", "tests", "testing",
    "doc", "docs", "documentation", "lint", "linting", "typecheck", "typing",
    "build", "ci", "style", "format", "benchmark", "bench", "coverage",
    "release", "publish", "check", "checks", "static-analysis",
}

# A PEP 508 requirement starts with the project name; everything from the
# first extras bracket / version specifier / marker / URL separator on is
# metadata, not part of the name.
REQ_NAME_RE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)')


def github_owner_repo(url):
    if not url:
        return None
    m = GITHUB_RE.match(url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if owner.lower() in ("orgs", "sponsors", "apps", "marketplace"):
        return None
    return f"{owner}/{repo}"


def normalize(name):
    """PEP 503 normalized project name."""
    return re.sub(r'[-_.]+', '-', name).lower()


def requirement_name(spec):
    """Project name out of one PEP 508 requirement string, or None."""
    spec = spec.split("#", 1)[0].strip()
    if not spec or spec.startswith("-"):  # pip flag: -r, -e, --index-url, ...
        return None
    if "://" in spec.split("@", 1)[0]:  # bare URL line (a .whl/.tar.gz target)
        return None
    m = REQ_NAME_RE.match(spec)
    return m.group(1) if m else None


def parse_pyproject_dependencies(text):
    """Distinct project names this pyproject.toml really depends on."""
    try:
        manifest = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []

    specs = []
    project = manifest.get("project")
    if isinstance(project, dict):
        deps = project.get("dependencies")
        if isinstance(deps, list):
            specs.extend(d for d in deps if isinstance(d, str))
        extras = project.get("optional-dependencies")
        if isinstance(extras, dict):
            for group, group_deps in extras.items():
                if str(group).strip().lower() in DEV_GROUPS:
                    continue
                if isinstance(group_deps, list):
                    specs.extend(d for d in group_deps if isinstance(d, str))

    poetry = (manifest.get("tool") or {}).get("poetry")
    if isinstance(poetry, dict):
        poetry_deps = poetry.get("dependencies")
        if isinstance(poetry_deps, dict):
            # Keys are names here, not PEP 508 strings; `python` pins the
            # interpreter and is not a package. [tool.poetry.group.*] is
            # deliberately not read (dev-tier, see docstring).
            specs.extend(k for k in poetry_deps if k.lower() != "python")

    names = []
    for spec in specs:
        name = requirement_name(spec)
        if name:
            names.append(name)
    return names


def parse_requirements_dependencies(text):
    names = []
    for line in text.splitlines():
        name = requirement_name(line)
        if name:
            names.append(name)
    return names


def pypi_cache_path(package):
    return PYPI_CACHE / f"{package}.json"


def fetch_pypi_repo(package):
    """owner/repo for a PyPI project, or None. Reuses 09_resolve_packages.py's
    cache directory -- the un-normalized name is checked first so entries that
    script already wrote stay warm."""
    normalized = normalize(package)
    for candidate in (package, normalized):
        cache_path = pypi_cache_path(candidate)
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text())
            except ValueError:
                break
            return None if "_error" in data else resolve_from_pypi_json(data)

    url = f"https://pypi.org/pypi/{urllib.parse.quote(normalized, safe='')}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "pelagos-package-resolve"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        data = {"_error": f"{exc.code} {exc.reason}"}
    except (urllib.error.URLError, TimeoutError, ValueError):
        data = {"_error": "fetch failed"}
    finally:
        time.sleep(PYPI_FETCH_THROTTLE_S)

    PYPI_CACHE.mkdir(parents=True, exist_ok=True)
    pypi_cache_path(normalized).write_text(json.dumps(data))
    return None if "_error" in data else resolve_from_pypi_json(data)


def resolve_from_pypi_json(data):
    """Same walk 09_resolve_packages.py uses: preferred project_urls keys
    first, then any project_urls value, then home_page/download_url."""
    info = data.get("info", {})
    project_urls = info.get("project_urls") or {}
    for key in URL_KEYS:
        for actual_key, url in project_urls.items():
            if actual_key.strip().lower() == key.lower():
                resolved = github_owner_repo(url)
                if resolved:
                    return resolved
    for url in project_urls.values():
        resolved = github_owner_repo(url)
        if resolved:
            return resolved
    return github_owner_repo(info.get("home_page")) or github_owner_repo(info.get("download_url"))


def load_node_ids():
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    return canonical_lookup(list(top50) + list(extra))  # duplicate slugs resolved; see scripts/identity.py


def repo_dependencies(files):
    """(package names, kinds used) for one repo, or ([], set()) if none of its
    manifests declares anything.

    pyproject.toml wins when it actually declares something. When it parses to
    nothing -- `dynamic = ["dependencies"]`, or a pyproject that is purely
    ruff/black config -- the requirements.txt beside it is the real list, so
    fall back to it rather than reporting the repo as dependency-free
    (3b1b/manim, AUTOMATIC1111/stable-diffusion-webui, Comfy-Org/ComfyUI,
    ansible/ansible, vllm-project/vllm, hacksider/Deep-Live-Cam all hit this).

    That priority is resolved **per directory**, now that a repo can have more
    than one manifest. A nested package has its own pyproject/requirements
    pair and its own answer to which of the two is real; deciding once for the
    whole repo would let one directory's populated pyproject suppress
    another's requirements.txt."""
    parsers = {
        "pyproject.toml": parse_pyproject_dependencies,
        "requirements.txt": parse_requirements_dependencies,
    }
    by_dir = defaultdict(dict)
    for path, text in files.items():
        directory, _, filename = path.rpartition("/")
        by_dir[directory][filename] = text

    deps, kinds = [], set()
    for directory in sorted(by_dir):
        here = by_dir[directory]
        for kind in MANIFEST_KINDS:  # priority order
            if kind not in here:
                continue
            found = parsers[kind](here[kind])
            if found:
                deps.extend(found)
                kinds.add(kind)
                break
    return deps, kinds


def main(out_path="data/processed/repo_python_dependency_edges.json"):
    canon_lookup = load_node_ids()
    cohort = sorted(set(canon_lookup.values()))
    manifests = load_manifests("python", cohort)

    pkg_resolved = {}  # normalized name -> owner/repo or None, shared across repos
    resolved_count, unresolved_count = 0, 0
    parsed_pyproject, parsed_requirements, no_manifest = 0, 0, 0
    direct_dep_total = 0
    edge_packages = defaultdict(set)  # (source, target) -> {package name, ...}

    for repo in cohort:
        files = manifests.get(repo)
        if not files:
            no_manifest += 1
            continue
        deps, kinds = repo_dependencies(files)
        if "pyproject.toml" in kinds:
            parsed_pyproject += 1
        if "requirements.txt" in kinds:
            parsed_requirements += 1
        if not deps:
            continue
        source = canon_lookup.get(repo.lower(), repo)

        for package in deps:
            direct_dep_total += 1
            key = normalize(package)
            if key not in pkg_resolved:
                found = fetch_pypi_repo(package)
                if found:
                    resolved_count += 1
                else:
                    unresolved_count += 1
                pkg_resolved[key] = found
            resolved = pkg_resolved[key]
            if not resolved:
                continue
            target = canon_lookup.get(resolved.lower())
            if not target or target == source:
                continue
            edge_packages[(source, target)].add(key)

    edges = sorted(
        ([a, b, len(pkgs), sorted(pkgs)] for (a, b), pkgs in edge_packages.items()),
        key=lambda e: -e[2],
    )
    Path(out_path).write_text(json.dumps(edges, separators=(",", ":")))

    # Only the resolved mappings are worth persisting as a named lookup
    # (mirrors package_to_repo.json / go_module_to_repo.json /
    # js_package_to_repo.json / java_coord_to_repo.json / crate_to_repo.json).
    pkg_map = {k: v for k, v in pkg_resolved.items() if v}
    (ROOT / "data/processed/python_package_to_repo.json").write_text(
        json.dumps(pkg_map, indent=0, sort_keys=True))

    repos_with, files_seen, nested = manifest_stats("python", cohort)
    touched = {n for e in edges for n in (e[0], e[1])}
    sources = {e[0] for e in edges}
    targets = {e[1] for e in edges}
    print(
        f"{repos_with}/{len(cohort)} cohort repos have a real Python manifest across "
        f"{files_seen} files ({nested} of them nested, unreachable by root-only fetching; "
        f"{parsed_pyproject} repos contributed a pyproject.toml, {parsed_requirements} a "
        f"requirements.txt, {no_manifest} have neither), {direct_dep_total} dependency entries, "
        f"{len(pkg_resolved)} distinct packages ({resolved_count} resolved to a GitHub repo, "
        f"{unresolved_count} left unresolved/non-GitHub) -> {len(edges)} dependency edges "
        f"({len(sources)} source repos -> {len(targets)} target repos, {len(touched)} nodes total) "
        f"-> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
