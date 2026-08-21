#!/usr/bin/env python3
"""Collapse the cohort's GitHub slugs onto real repositories: one node per
repository, carrying the set of origins that serve it.

Every node id in this pipeline has so far been an `owner/name` GitHub slug,
and the pipeline has quietly assumed one slug is one repository. It is not.
Measured against the shipped 7,051-repo cohort, from data already cached:

    5 rename pairs where BOTH slugs are separate nodes
        hwchase17/langchain              + langchain-ai/langchain
        microsoft/guidance               + guidance-ai/guidance
        tensorflow/magenta               + magenta/magenta
        thudm/chatglm-6b                 + zai-org/ChatGLM-6B
        rwightman/pytorch-image-models   + huggingface/pytorch-image-models

    62 nodes GitHub itself marks as forks, 18 of them shadowing an upstream
    that is also a cohort node (5 separate forks of facebookresearch/fairseq,
    3 of OpenNMT/OpenNMT-py, 2 of huggingface/transformers)

That last rename pair is worth naming, because the project has been reading
it the other way round: ROADMAP.md Phase 19 records
`rwightman/pytorch-image-models` <-> `huggingface/pytorch-image-models` as the
shared-issue-poster tier's strongest real edge, 106 shared posters, "the real
`timm` library rename, not a coincidence". It is not an edge between two
repositories. It is one repository drawn twice, and 106 people who filed
issues in it counted as evidence that its two halves are related.

## Three layers, because three different things break slug identity

Each layer is separately measured in the summary, and each uses the cheapest
evidence that can actually settle its case:

  1. **Renames** -- GitHub's own `full_name`, already sitting in
     data/raw/github_cache/. A renamed repo answers to both names forever, so
     two collection passes months apart pick up both.
  2. **Forks** -- GitHub's `fork` flag and `source`/`parent`. A fork is a
     copy; where the upstream is also in the cohort, the fork is a duplicate
     node, and where it is not, the fork is a stand-in for a repository the
     cohort does not otherwise contain.
  3. **Mirrors and unlinked copies** -- intrinsic git object ids, from
     43_repo_refs.py. Nothing on GitHub links a mirror to its upstream, and
     no name or URL heuristic can: `torvalds/linux` and the kernel.org origin
     it mirrors share no owner, no host, and no metadata field. They share
     940 byte-identical object ids, which is not a similarity score but an
     identity proof -- an object id is a hash of the object's content.

## The merge rule for layer 3, and why containment rather than Jaccard

Two origins merge when their shared object ids cover at least
MERGE_CONTAINMENT of the *smaller* origin's refs. Jaccard would be wrong
here and the Linux pair shows why: 940 shared out of 2259 and 941 refs is
Jaccard 0.42 and containment 0.999. A mirror legitimately carries fewer refs
than what it mirrors -- it lags, or it mirrors only branches, or only tags.
Being a strict subset is the *expected* shape of a mirror, and Jaccard
punishes exactly that.

The threshold is deliberately high, because sharing *some* history is common
and does not make two repositories one: a hard fork that became its own
project keeps developing, so its own refs quickly outnumber the ones it
inherited, and its containment falls away from 1. Every group this rule forms
is printed with its containment so the borderline cases can be read rather
than trusted, and MIN_SHARED_REFS keeps a repository with a single branch
from merging into anything it happens to descend from.

## Cross-forge origins

`git ls-remote` verifies an origin; it cannot discover one. Enumerating every
origin that serves a given revision is exactly what Software Heritage's graph
is for, but its public REST API does not expose that query -- only a
substring search over origin URLs, which does not surface
`git.kernel.org/.../linux.git` for the query "linux" at all (checked). Doing
this properly means the SWH graph dataset on AWS Open Data, which is its own
ingestion project.

So candidate upstreams come from data/repo-lists/upstream_origins.txt, a
curated list of known non-GitHub homes, and every one of them is *verified*
by the same ref-overlap rule before it is recorded. A wrong guess in that
file cannot produce a wrong merge; it produces a printed rejection. The list
is a candidate generator, never evidence.

## What the id is

The record key stays the canonical origin's `owner/name`. That is a
deliberate choice, not an oversight: the identity is decided intrinsically
(by object ids), but the *label* has to stay readable and permalink-stable,
and every downstream file, avatar and shared URL in this project is keyed by
slug. Each record additionally carries `anchor` -- the repository's root
commit, fetched for merged groups only -- which is the genuinely
forge-independent, time-stable identifier: it is identical across every
mirror, fork and rename of a repository, and unlike a ref-set digest it does
not change when someone pushes a tag.

Writes data/processed/repo_identity.json:

    {"repos":  {canonical: {"origins": [...], "anchor": ..., "aliases": [...],
                            "merged_by": [...]}},
     "alias":  {duplicate slug: canonical slug}}

`alias` is the join table every other script needs: one lookup turns any slug
this cohort has ever collected into the repository it actually names.

## Why this file accumulates rather than being rebuilt

Once 45_apply_identity.py has collapsed a duplicate out of the cohort, that
slug is gone from every input this script reads -- so a plain rebuild would
"discover" only the duplicates that have appeared since, and silently drop
the record of every one already merged. Re-running after the Debian expansion
did exactly that in development: 25 aliases became 2, and
`hwchase17/langchain` stopped resolving to anything.

That is a real loss, not bookkeeping. The alias table is the only thing that
still connects an old slug to the repository it named -- for a permalink
somebody shared, for a package whose registry metadata still points at the old
name, and for the explorer's "also known as" row. So this merges into whatever
data/processed/repo_identity.json already holds: new findings win on conflict,
previously-recorded aliases and verified origins survive.

Usage: python3 scripts/44_repo_identity.py
"""
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB_CACHE = ROOT / "data/raw/github_cache"
REFS_CACHE = ROOT / "data/raw/repo_refs_cache"
UPSTREAM_LIST = ROOT / "data/repo-lists/upstream_origins.txt"
OUT_PATH = ROOT / "data/processed/repo_identity.json"

GH_API_THROTTLE_S = 0.4  # see scripts/10_fetch_new_repo_stats.py

MERGE_CONTAINMENT = 0.90  # shared ids as a fraction of the smaller origin's refs
MIN_SHARED_REFS = 2  # a single shared commit is descent, not identity
SKETCH_K = 96  # bottom-k object ids per origin, for candidate generation only

# An object id held by more origins than this is ancestry, not identity -- a
# base commit a great many repos descend from. Indexing it would make every
# pair of its holders a candidate and buy nothing, since the exact check below
# would reject them all anyway.
MAX_ORIGINS_PER_ID = 40


def load_cohort():
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    return sorted(set(top50) | set(extra))


def load_github(repos):
    """{repo: cached GitHub API object} for every repo that has one."""
    out = {}
    for repo in repos:
        owner, name = repo.split("/", 1)
        path = GITHUB_CACHE / f"{owner}__{name}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        if not data.get("_404"):
            out[repo] = data
    return out


def load_refs(repos):
    """{repo: frozenset(object ids)} for every origin with readable refs."""
    out = {}
    for repo in repos:
        owner, name = repo.split("/", 1)
        path = REFS_CACHE / f"{owner}__{name}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        if data.get("error") or not data.get("ids"):
            continue
        out[repo] = frozenset(data["ids"])
    return out


class Union:
    """Plain union-find. Every merge records why, so the output can be audited
    layer by layer rather than taken on faith."""

    def __init__(self):
        self.parent = {}
        self.reasons = defaultdict(list)

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b, reason):
        ra, rb = self.find(a), self.find(b)
        self.reasons[a].append(reason)
        if ra == rb:
            return False
        self.parent[rb] = ra
        return True

    def groups(self):
        out = defaultdict(set)
        for node in self.parent:
            out[self.find(node)].add(node)
        return out


def containment(a, b):
    """(shared count, shared / smaller side). The right metric for a mirror:
    a mirror is expected to be a subset of what it mirrors."""
    shared = len(a & b)
    smaller = min(len(a), len(b))
    return shared, (shared / smaller if smaller else 0.0)


def candidate_pairs(refs):
    """Origin pairs worth an exact comparison, from a bottom-k sketch.

    An exact all-pairs pass is 24M comparisons over sets averaging ~800 ids.
    Bottom-k is the standard way out and is sound for what layer 3 has to
    catch: two origins serving the same repository agree on nearly all of
    their ids, so they agree on the smallest ones too. Origins with no more
    than SKETCH_K refs are indexed in full, so a small mirror is not missed
    for being small."""
    by_id = defaultdict(list)
    for repo, ids in refs.items():
        sketch = ids if len(ids) <= SKETCH_K else sorted(ids)[:SKETCH_K]
        for object_id in sketch:
            by_id[object_id].append(repo)
    pairs = set()
    for holders in by_id.values():
        if len(holders) < 2 or len(holders) > MAX_ORIGINS_PER_ID:
            continue
        for i, a in enumerate(holders):
            for b in holders[i + 1:]:
                pairs.add((a, b) if a < b else (b, a))
    return pairs


def parse_upstream_list():
    """[(github slug, upstream url)] from the curated candidate file."""
    if not UPSTREAM_LIST.exists():
        return []
    out = []
    for line in UPSTREAM_LIST.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) == 2:
            out.append((parts[0], parts[1]))
    return out


def ls_remote_ids(url):
    try:
        out = subprocess.run(
            ["git", "ls-remote", url], capture_output=True, text=True, timeout=180,
            env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true", "PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    return frozenset(
        parts[0] for parts in (line.split("\t", 1) for line in out.stdout.splitlines())
        if len(parts) == 2 and len(parts[0]) == 40
    )


def root_commit(repo):
    """The repository's initial commit -- the one intrinsic identifier that
    survives every rename, fork and mirror and never changes afterwards.

    Two requests: page 1 of the commit list with per_page=1 answers with a
    Link header naming the last page, and that page holds the root commit.
    Only ever called for merged groups, so this costs a few dozen requests,
    not two per cohort repo."""
    out = subprocess.run(
        ["gh", "api", "--include", f"repos/{repo}/commits?per_page=1"],
        capture_output=True, text=True)
    time.sleep(GH_API_THROTTLE_S)
    if out.returncode != 0:
        return None
    last_page = None
    for line in out.stdout.splitlines():
        if line.lower().startswith("link:") and 'rel="last"' in line:
            for part in line.split(","):
                if 'rel="last"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    last_page = url.rsplit("page=", 1)[-1]
    if last_page is None:
        return None
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}/commits?per_page=1&page={last_page}"],
        capture_output=True, text=True)
    time.sleep(GH_API_THROTTLE_S)
    if out.returncode != 0:
        return None
    try:
        commits = json.loads(out.stdout)
    except ValueError:
        return None
    return commits[0]["sha"] if commits else None


def pick_canonical(members, github):
    """Which slug in a group names the repository.

    Order matters and each step is evidence, not preference: GitHub's own
    current name for a repo settles a rename outright, and a fork's declared
    source settles a fork family outright. Only when neither applies does this
    fall back to ranked signals, ending in a lexicographic tiebreak so the
    choice is deterministic across runs."""
    lower = {m.lower(): m for m in members}

    # A member that every other member's `full_name` points at is the current name.
    current = {github[m]["full_name"].lower() for m in members
               if m in github and github[m].get("full_name")}
    named = [lower[c] for c in current if c in lower]
    non_fork_named = [m for m in named if not (github.get(m) or {}).get("fork")]
    if len(non_fork_named) == 1:
        return non_fork_named[0], "github full_name"

    # A declared source that is itself in the group settles a fork family.
    sources = set()
    for m in members:
        data = github.get(m) or {}
        if data.get("fork"):
            src = (data.get("source") or data.get("parent") or {}).get("full_name")
            if src and src.lower() in lower:
                sources.add(lower[src.lower()])
    if len(sources) == 1:
        return sources.pop(), "fork source"

    def rank(m):
        data = github.get(m) or {}
        return (
            bool(data.get("fork")),          # a fork never wins over a non-fork
            bool(data.get("archived")),      # nor an archived repo over a live one
            -(data.get("stargazers_count") or 0),
            data.get("created_at") or "9999",  # older repo wins
            m,
        )
    return min(members, key=rank), "ranked (fork/archived/stars/age)"


def main():
    repos = load_cohort()
    github = load_github(repos)
    refs = load_refs(repos)
    print(f"{len(repos)} cohort slugs, {len(github)} with cached GitHub metadata, "
          f"{len(refs)} with readable intrinsic refs", file=sys.stderr)

    union = Union()
    for repo in repos:
        union.find(repo)
    stats = Counter()

    # Layer 1 -- renames.
    lower = {r.lower(): r for r in repos}
    for repo, data in github.items():
        full_name = data.get("full_name")
        if full_name and full_name.lower() != repo.lower() and full_name.lower() in lower:
            if union.union(lower[full_name.lower()], repo, "rename"):
                stats["rename"] += 1

    # Layer 2 -- forks whose upstream is also a cohort node.
    for repo, data in github.items():
        if not data.get("fork"):
            continue
        stats["fork_nodes"] += 1
        src = (data.get("source") or data.get("parent") or {}).get("full_name")
        if src and src.lower() in lower:
            if union.union(lower[src.lower()], repo, "fork of cohort node"):
                stats["fork"] += 1

    # Layer 3 -- intrinsic ref overlap.
    pairs = candidate_pairs(refs)
    stats["candidate_pairs"] = len(pairs)
    overlaps = {}
    for a, b in pairs:
        shared, cover = containment(refs[a], refs[b])
        if shared >= MIN_SHARED_REFS and cover >= MERGE_CONTAINMENT:
            overlaps[(a, b)] = (shared, cover)
            if union.union(a, b, f"ref overlap {shared} ids, {cover:.3f} containment"):
                stats["ref_overlap"] += 1

    # Cross-forge origins: curated candidates, verified against the same rule.
    extra_origins = defaultdict(list)
    rejected = []
    for slug, url in parse_upstream_list():
        if slug not in refs:
            rejected.append((slug, url, "slug not a cohort origin with readable refs"))
            continue
        ids = ls_remote_ids(url)
        if ids is None:
            rejected.append((slug, url, "origin unreadable"))
            continue
        shared, cover = containment(refs[slug], ids)
        if shared >= MIN_SHARED_REFS and cover >= MERGE_CONTAINMENT:
            extra_origins[slug].append({"url": url, "refs": len(ids), "shared": shared,
                                        "containment": round(cover, 4)})
            stats["cross_forge_origin"] += 1
        else:
            rejected.append((slug, url, f"only {shared} shared ids, {cover:.3f} containment"))

    # Assemble.
    groups = union.groups()
    identity = {}
    alias = {}
    merged_groups = []
    for members in groups.values():
        canonical, why = pick_canonical(members, github)
        origins = []
        for m in sorted(members):
            data = github.get(m) or {}
            origins.append({
                "url": f"https://github.com/{data.get('full_name', m)}",
                "forge": "github",
                "slug": m,
                "role": "canonical" if m == canonical else (
                    "fork" if data.get("fork") else "duplicate"),
            })
        for cross in extra_origins.get(canonical, []) + [
                o for m in members if m != canonical for o in extra_origins.get(m, [])]:
            origins.append({"url": cross["url"], "forge": "other", "role": "mirrored-from",
                            "shared_refs": cross["shared"], "containment": cross["containment"]})
        record = {
            "origins": origins,
            "aliases": sorted(m for m in members if m != canonical),
            "canonical_by": why,
            "merged_by": sorted(set(r for m in members for r in union.reasons.get(m, []))),
        }
        identity[canonical] = record
        for m in members:
            if m != canonical:
                alias[m] = canonical
        if len(members) > 1:
            merged_groups.append((canonical, sorted(members)))

    # Root commit, for merged groups only -- the forge-independent anchor.
    for canonical, members in merged_groups:
        anchor = root_commit(canonical)
        if anchor:
            identity[canonical]["anchor"] = anchor
    for canonical in identity:
        identity[canonical].setdefault("anchor", None)

    # Merge with what is already recorded -- see the module docstring.
    carried_aliases, carried_origins = 0, 0
    if OUT_PATH.exists():
        previous = json.loads(OUT_PATH.read_text())
        for slug, canon in previous.get("alias", {}).items():
            # Follow the chain: a slug merged before may since have been
            # merged again into a further canonical repo.
            target = canon
            while target in alias:
                target = alias[target]
            if slug not in alias and slug not in identity:
                alias[slug] = target
                carried_aliases += 1
        for repo, record in previous.get("repos", {}).items():
            current = identity.get(repo)
            if current is None:
                continue
            known = {o.get("url") for o in current["origins"]}
            for origin in record.get("origins", []):
                if origin.get("url") not in known:
                    current["origins"].append(origin)
                    carried_origins += 1 if origin.get("forge") != "github" else 0
            merged = set(current["aliases"]) | {
                a for a in record.get("aliases", []) if a not in identity}
            current["aliases"] = sorted(merged)
            if current.get("anchor") is None and record.get("anchor"):
                current["anchor"] = record["anchor"]
    for slug, canon in alias.items():
        record = identity.get(canon)
        if record is not None and slug not in record["aliases"]:
            record["aliases"] = sorted(set(record["aliases"]) | {slug})

    OUT_PATH.write_text(json.dumps(
        {"repos": identity, "alias": alias}, indent=0, sort_keys=True))
    if carried_aliases or carried_origins:
        print(f"carried forward {carried_aliases} aliases and {carried_origins} verified "
              f"non-GitHub origins from the previous run (already collapsed out of the "
              f"cohort, so this run could not rediscover them)", file=sys.stderr)

    print(f"\n{len(repos)} slugs -> {len(identity)} repositories "
          f"({len(alias)} slugs are duplicates of another)", file=sys.stderr)
    print(f"  layer 1 renames merged:      {stats['rename']}", file=sys.stderr)
    print(f"  layer 2 forks merged:        {stats['fork']} "
          f"(of {stats['fork_nodes']} fork nodes; the rest have no cohort upstream)",
          file=sys.stderr)
    print(f"  layer 3 ref-overlap merged:  {stats['ref_overlap']} "
          f"(from {stats['candidate_pairs']} sketch candidates)", file=sys.stderr)
    print(f"  cross-forge origins verified: {stats['cross_forge_origin']}", file=sys.stderr)

    print(f"\n{len(merged_groups)} merged groups:", file=sys.stderr)
    for canonical, members in sorted(merged_groups, key=lambda g: -len(g[1]))[:40]:
        others = [m for m in members if m != canonical]
        detail = ""
        for m in others:
            key = (min(canonical, m), max(canonical, m))
            if key in overlaps:
                shared, cover = overlaps[key]
                detail = f"  [{shared} shared ids, {cover:.3f}]"
        print(f"  {canonical}  <-  {', '.join(others)}{detail}", file=sys.stderr)
    if len(merged_groups) > 40:
        print(f"  ... and {len(merged_groups) - 40} more", file=sys.stderr)

    if rejected:
        print(f"\n{len(rejected)} curated upstream candidates rejected "
              f"(a candidate is never evidence):", file=sys.stderr)
        for slug, url, why in rejected:
            print(f"  {slug} !~ {url}: {why}", file=sys.stderr)
    print(f"\n-> {OUT_PATH.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
