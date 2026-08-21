#!/usr/bin/env python3
"""Turn go.mod `require` directives into real, directed `repo --depends
on--> repo` edges, the Go-ecosystem counterpart to 11_dependency_edges.py's

Reads every go.mod in the cohort, at any depth, via
scripts/manifests.py -- not one file at the root of the repos GitHub's
`language:` facet labelled Go. That facet reports a repo's *dominant*
language, one value per repo, so under the old scoping a repo could not
contribute Go dependencies unless Go happened to be its biggest
language by bytes. Vendored trees and byte-identical copies of other
projects' manifests are dropped by that module before anything is parsed
here; see its docstring for the measurement behind the change.

Resolution, checked directly against real go.mod files and the real Go
module-discovery protocol before writing this (kubernetes/kubernetes,
hashicorp/consul, gin-gonic/gin, spf13/cobra, rclone/rclone):

- `github.com/owner/repo/...` module paths (the large majority) resolve for
  free: owner/repo is just the first two path segments -- any `/vN`
  major-version suffix or subpackage path collapses away at the repo level
  (e.g. `github.com/aws/aws-sdk-go-v2/config` -> `aws/aws-sdk-go-v2`).
- Everything else is a vanity import path. Resolved via the real `go-import`
  HTML meta-tag discovery protocol (`https://{module}?go-get=1`, same
  mechanism `go get` itself uses), cached per distinct module path since the
  same vanity path (e.g. `golang.org/x/net`) recurs across hundreds of
  repos. Kept only if the resolved repo-root is a real `github.com` URL --
  checked directly that this is a genuine split, not a formality:
  `google.golang.org/grpc` and `go.uber.org/zap` resolve straight to GitHub,
  while `golang.org/x/net` resolves to `go.googlesource.com` and
  `gopkg.in/yaml.v3` resolves to its own gopkg.in git host -- both correctly
  left unresolved (edge-less) rather than force-mapped to an unofficial
  GitHub mirror or a hardcoded lookup table, same "real signal or none,
  never guessed" rule this codebase already applies elsewhere (e.g. the
  74/319 issue-data coverage gap, theta=None).
- Only non-`// indirect` require lines count: those are this repo's own
  real, direct dependency, not a transitive one pulled in by something else
  it depends on.
- `replace`/`exclude`/`retract` directives need no special handling: the
  same target==source self-loop filter 11_dependency_edges.py already uses
  drops monorepo self-references (e.g. consul's
  `github.com/hashicorp/consul/api => ./api`) for free, since that target
  collapses to the source repo itself.

Output shape mirrors 11_dependency_edges.py's full-edge tuples (source,
target, weight, [module paths]) so 11 can fold this file straight into its
own combined top-K prune pass -- PyPI and Go edges are the same semantic
tier (a real declared dependency), so they need one shared prune, not two
separate ones concatenated.

Usage: python3 scripts/29_go_dependency_edges.py [out=data/processed/repo_go_dependency_edges.json]
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import canonical_lookup  # noqa: E402
from manifests import iter_manifests, manifest_stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VANITY_CACHE = ROOT / "data/raw/go_vanity_cache"
VANITY_FETCH_THROTTLE_S = 0.2  # polite pacing for fresh vanity-domain fetches (not GitHub's API, no shared budget to protect, but still hundreds of distinct hosts)

REQUIRE_BLOCK_START_RE = re.compile(r'^require\s*\(\s*$')
REQUIRE_SINGLE_RE = re.compile(r'^require\s+(\S+)\s+\S+(.*)$')
BLOCK_ENTRY_RE = re.compile(r'^(\S+)\s+\S+(.*)$')
GO_IMPORT_META_RE = re.compile(r'<meta\s+name="go-import"\s+content="([^"]*)"', re.IGNORECASE)
GITHUB_URL_RE = re.compile(r'https?://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$')


def parse_go_mod(text):
    """Direct-only (non-`// indirect`) required module paths, handling both
    the single-line (`require path v1.0.0`) and parenthesized block forms."""
    direct = []
    in_block = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if in_block:
            if line == ")":
                in_block = False
                continue
            if line.startswith("//"):
                continue
            m = BLOCK_ENTRY_RE.match(line)
            if m and "indirect" not in m.group(2).lower():
                direct.append(m.group(1))
            continue
        if REQUIRE_BLOCK_START_RE.match(line):
            in_block = True
            continue
        m = REQUIRE_SINGLE_RE.match(line)
        if m and "indirect" not in m.group(2).lower():
            direct.append(m.group(1))
    return direct


def resolve_github_direct(module_path):
    parts = module_path.split("/")
    if len(parts) < 3:
        return None
    return f"{parts[1]}/{parts[2]}"


def fetch_go_import(module_path):
    url = f"https://{module_path}?go-get=1"
    req = urllib.request.Request(url, headers={"User-Agent": "pelagos-go-vanity-resolve"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    m = GO_IMPORT_META_RE.search(re.sub(r'\s+', ' ', html))
    if not m:
        return None
    parts = m.group(1).split()
    if len(parts) < 3:
        return None
    gh = GITHUB_URL_RE.match(parts[2])
    return f"{gh.group(1)}/{gh.group(2)}" if gh else None


def resolve_vanity(module_path):
    cache_path = VANITY_CACHE / (module_path.replace("/", "__") + ".txt")
    if cache_path.exists():
        return cache_path.read_text().strip() or None
    resolved = fetch_go_import(module_path)
    VANITY_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(resolved or "")
    time.sleep(VANITY_FETCH_THROTTLE_S)
    return resolved


def load_node_ids():
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    return canonical_lookup(list(top50) + list(extra))  # duplicate slugs resolved; see scripts/identity.py


def main(out_path="data/processed/repo_go_dependency_edges.json"):
    canon_lookup = load_node_ids()
    cohort = sorted(set(canon_lookup.values()))

    module_resolved = {}  # module path -> owner/repo or None, shared across every repo's requires
    github_direct, vanity_resolved, vanity_unresolved = 0, 0, 0
    parsed, empty = 0, 0
    direct_req_total = 0
    edge_modules = defaultdict(set)  # (source, target) -> {module paths}

    for repo, _path, text in iter_manifests("go", cohort):
        if not text.strip():
            empty += 1
            continue
        parsed += 1
        source = canon_lookup.get(repo.lower(), repo)

        for module_path in parse_go_mod(text):
            direct_req_total += 1
            if module_path not in module_resolved:
                if module_path.startswith("github.com/"):
                    resolved = resolve_github_direct(module_path)
                    if resolved:
                        github_direct += 1
                else:
                    resolved = resolve_vanity(module_path)
                    if resolved:
                        vanity_resolved += 1
                    else:
                        vanity_unresolved += 1
                module_resolved[module_path] = resolved
            resolved = module_resolved[module_path]
            if not resolved:
                continue
            target = canon_lookup.get(resolved.lower())
            if not target or target == source:
                continue
            edge_modules[(source, target)].add(module_path)

    edges = sorted(
        ([a, b, len(mods), sorted(mods)] for (a, b), mods in edge_modules.items()),
        key=lambda e: -e[2],
    )
    Path(out_path).write_text(json.dumps(edges, separators=(",", ":")))

    # Only the vanity-path resolutions are worth persisting as a named mapping
    # (github.com/* ones are free/re-derivable from the module path string
    # itself, same reasoning 09_resolve_packages.py's package_to_repo.json
    # only bothers mapping the ambiguous PyPI-name case).
    vanity_map = {k: v for k, v in module_resolved.items() if v and not k.startswith("github.com/")}
    (ROOT / "data/processed/go_module_to_repo.json").write_text(
        json.dumps(vanity_map, indent=0, sort_keys=True))

    repos_with, files_seen, nested = manifest_stats("go", cohort)
    touched = {n for e in edges for n in (e[0], e[1])}
    sources = {e[0] for e in edges}
    targets = {e[1] for e in edges}
    print(
        f"{parsed} go.mod files parsed across {repos_with} cohort repos "
        f"({nested} of the {files_seen} files are nested, unreachable by root-only fetching; "
        f"{empty} empty), "
        f"{direct_req_total} direct (non-indirect) require lines, {len(module_resolved)} distinct "
        f"module paths ({github_direct} resolved free via github.com/*, {vanity_resolved} resolved "
        f"via go-import, {vanity_unresolved} left unresolved/non-GitHub) -> {len(edges)} dependency "
        f"edges ({len(sources)} source repos -> {len(targets)} target repos, {len(touched)} nodes total) "
        f"-> {out_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
