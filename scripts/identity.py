#!/usr/bin/env python3
"""Shared read side of repo identity: turn any slug this cohort has ever
collected into the repository it actually names.

44_repo_identity.py works out which slugs are the same repository (renames,
forks, mirrors) and writes data/processed/repo_identity.json. This module is
the one-line lookup every other script uses, so that de-duplication happens in
exactly one place rather than being re-derived, differently, six times.

The pipeline's node key stays a readable `owner/name` slug -- the canonical
one for each repository. What changes is that it is now *guaranteed* to name a
repository rather than an origin: `hwchase17/langchain` and
`langchain-ai/langchain` both resolve to the latter, and every edge, weight
and coordinate computed downstream lands on one node instead of being split
across two.

Degrades to the identity function when repo_identity.json does not exist, so
every caller keeps working on a checkout where 43/44 have never been run --
the same convention 11_dependency_edges.py uses for the per-ecosystem edge
files.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDENTITY_PATH = ROOT / "data/processed/repo_identity.json"

_cache = {}


def _identity():
    if "data" not in _cache:
        if IDENTITY_PATH.exists():
            _cache["data"] = json.loads(IDENTITY_PATH.read_text())
        else:
            _cache["data"] = {"repos": {}, "alias": {}}
    return _cache["data"]


def alias_map():
    """{duplicate slug: canonical slug}, lowercased keys."""
    if "alias_lower" not in _cache:
        _cache["alias_lower"] = {k.lower(): v for k, v in _identity()["alias"].items()}
    return _cache["alias_lower"]


def repositories():
    """{canonical slug: identity record}."""
    return _identity()["repos"]


def canonical(repo):
    """The repository a slug names. Returns `repo` unchanged when it is
    already canonical or unknown."""
    return alias_map().get(repo.lower(), repo)


def canonical_lookup(node_ids):
    """{lowercased slug -> canonical node id} over a node set.

    Replaces the `{rid.lower(): rid for rid in all_ids}` map every edge script
    builds by hand. Two differences, both the point of this module: an alias
    resolves to its canonical node, and a canonical node that is itself absent
    from `node_ids` is not invented -- the alias then resolves to nothing, the
    same way an unresolvable package does."""
    present = {rid.lower(): rid for rid in node_ids}
    lookup = dict(present)
    for dup, canon in alias_map().items():
        if canon.lower() in present:
            lookup[dup] = present[canon.lower()]
        elif dup in present:
            lookup[dup] = present[dup]
    return lookup


def canonical_node_ids(node_ids):
    """`node_ids` with every duplicate slug collapsed onto its canonical one."""
    present = {rid.lower(): rid for rid in node_ids}
    out = set()
    for rid in node_ids:
        canon = canonical(rid)
        out.add(present.get(canon.lower(), rid))
    return sorted(out)
