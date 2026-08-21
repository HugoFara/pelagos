#!/usr/bin/env python3
"""Aggregate rows for the non-GitHub forge nodes debian_deps.py
corroborated.

Until now a node id had to be an `owner/name` GitHub slug, and that quietly
decided which repositories could exist at all. The projects it excluded are
not obscure ones: libX11, cairo, dbus, mesa, wayland, fontconfig and the X.Org
client libraries sit under most of a Linux desktop, and every one of them
publishes on gitlab.freedesktop.org, gitlab.gnome.org or sourceware with no
GitHub repository. The most load-bearing region of the graph was missing its
actual occupants because of a naming convention.

A forge node's id is `host/path` -- `gitlab.freedesktop.org/xorg/lib/libx11`,
`sourceware.org/elfutils`. A first segment containing a dot means a forge host
rather than a GitHub owner, which is unambiguous rather than conventional:
GitHub owner names are [A-Za-z0-9-]+ and cannot contain a dot, checked against
every node in the cohort.

## What can and cannot be filled in

GitLab instances expose a public REST API, so gitlab.* nodes get a real
description, star count, fork count and open-issue count -- the same fields
the GitHub path fills, from the same kind of source.

Everything else (sourceware's cgit, gitlab.inria.fr behind auth) gets nulls.
That is deliberate and is the convention this project already follows
everywhere: a repo with no data for a field is left unset rather than given a
zero, and the explorer renders that as an honest blank rather than as a node
of size zero. `stargazers` in particular is left null rather than 0, because 0
is a real value that would place the node at the bottom of a size ranking it
was never measured for.

**Star counts are not comparable across forges.** A GitLab star and a GitHub
star are both "someone bookmarked this", but the populations differ by orders
of magnitude -- cairo has tens of GitLab stars where a mid-tier GitHub library
has thousands. They share the node-size encoding here, so a freedesktop node
renders smaller than its importance. The dependency graph is what places these
nodes on the trophic axis, and that is measured identically for every node
regardless of forge; only the size encoding is affected.

Reads data/repo-lists/forge_extra_nodes.txt (node id + origin URL, written by
scripts/edges/debian_deps.py) and merges rows into
data/processed/dependency_repo_aggregates.json, carrying `forge` and `origin`
so downstream code can link to the right place and skip the GitHub API.

Usage: python3 scripts/cohort/forge_repo_stats.py
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORGE_LIST = ROOT / "data/repo-lists/forge_extra_nodes.txt"
AGGREGATES_PATH = ROOT / "data/processed/dependency_repo_aggregates.json"
COHORT_LIST = ROOT / "data/repo-lists/dependency_extra_repos.txt"
FORGE_CACHE = ROOT / "data/raw/forge_api_cache"

REQUEST_TIMEOUT_S = 30


def load_forge_nodes():
    """[(node id, origin url)] from debian_deps.py's output."""
    if not FORGE_LIST.exists():
        return []
    out = []
    for line in FORGE_LIST.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].strip():
            out.append((parts[0].strip(), parts[1].strip()))
    return out


def gitlab_project(origin):
    """The GitLab API's project object for an origin URL, or None.

    Every GitLab instance exposes /api/v4/projects/<url-encoded full path> for
    public projects with no authentication, which covers
    gitlab.freedesktop.org and gitlab.gnome.org. A non-GitLab host, or an
    instance that requires auth, simply returns None and the node keeps its
    nulls."""
    rest = origin.removeprefix("https://").removeprefix("http://").removesuffix(".git")
    host, _, path = rest.partition("/")
    if not path:
        return None
    FORGE_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = FORGE_CACHE / f"{host}__{path.replace('/', '__')}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
        except ValueError:
            cached = None
        if cached is not None:
            return None if cached.get("_missing") else cached

    url = f"https://{host}/api/v4/projects/{urllib.parse.quote(path, safe='')}"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            data = json.load(response)
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        cache_path.write_text(json.dumps({"_missing": True}))
        return None
    cache_path.write_text(json.dumps(data))
    return data


def aggregate_row(node, origin, project):
    """The same shape every other aggregate row has, plus where it lives.

    Fields with no measurement stay None rather than 0 -- see the module
    docstring for why that distinction matters for the size encoding."""
    row = {
        "title": node.rsplit("/", 1)[-1],
        "stargazers": None,
        "forks": None,
        "openIssues": None,
        "watchers": None,
        "contributors": None,
        "description": "",
        "forge": origin.removeprefix("https://").partition("/")[0],
        "origin": origin,
    }
    if project:
        row["title"] = project.get("name") or row["title"]
        row["description"] = project.get("description") or ""
        row["stargazers"] = project.get("star_count")
        row["forks"] = project.get("forks_count")
        row["openIssues"] = project.get("open_issues_count")
    return row


def main():
    nodes = load_forge_nodes()
    if not nodes:
        print("no forge_extra_nodes.txt -- run scripts/edges/debian_deps.py first",
              file=sys.stderr)
        return
    aggregates = json.loads(AGGREGATES_PATH.read_text())

    added, enriched, bare = 0, 0, 0
    for node, origin in nodes:
        project = gitlab_project(origin)
        row = aggregate_row(node, origin, project)
        if project:
            enriched += 1
        else:
            bare += 1
        if node not in aggregates:
            added += 1
        aggregates[node] = row

    AGGREGATES_PATH.write_text(json.dumps(aggregates, indent=0, sort_keys=True))
    COHORT_LIST.write_text("\n".join(sorted(aggregates)) + "\n")

    hosts = {}
    for node, _origin in nodes:
        hosts.setdefault(node.split("/", 1)[0], 0)
        hosts[node.split("/", 1)[0]] += 1
    print(f"{len(nodes)} non-GitHub forge nodes ({added} new), {enriched} enriched from a "
          f"GitLab API, {bare} left with honest nulls (no public API on that host)",
          file=sys.stderr)
    for host, count in sorted(hosts.items(), key=lambda kv: -kv[1]):
        print(f"  {count:3d}  {host}", file=sys.stderr)


if __name__ == "__main__":
    main()
