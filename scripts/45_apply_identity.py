#!/usr/bin/env python3
"""Collapse duplicate slugs out of data/processed/, using the identity
44_repo_identity.py worked out.

44 decides *which* slugs name the same repository. This applies that decision
to the files already on disk, so the node set the rest of the pipeline reads
contains one entry per repository rather than one per origin. Idempotent: a
second run finds no aliases left and changes nothing.

Two shapes need different handling, and conflating them would corrupt real
numbers:

  - **Keyed by repo** (aggregates, descriptions, topics, embeddings). The
    canonical entry wins; an alias contributes only fields the canonical is
    missing. Both entries describe the same repository, and the canonical
    slug is the one GitHub still answers under, so its values are the current
    ones.

    "Wins" is decided against the whole node set, not against one file, and
    that distinction was a real bug before it was a design note. The cohort
    lives in two files -- the 51-repo SemRepo cohort and the dependency
    expansion -- and build_web_explorer.py merges them with the expansion
    last. Renaming an alias entry to its canonical id inside the expansion,
    because the canonical happened to live in the *other* file, therefore
    overwrote the real repo with the duplicate's stats at build time:
    `lllyasviel/controlnet` came out with the 101 stars of
    `clintonjwang/ControlNet`, and `openbmb/chatdev` with the 4 stars and the
    title "simulation" of `sumedhrasal/simulation`. An alias is now dropped
    whenever its canonical exists anywhere in the node set, and only carried
    over when the canonical exists nowhere at all.

  - **Edge lists** `[a, b, weight, [members]]`. Endpoints are remapped, edges
    that become self-loops are dropped, and edges that collide are merged by
    taking the **maximum** weight and the union of members -- never the sum.
    That matters: these weights are overlap counts over sets of people, and
    two slugs of one repository share very nearly the same stargazers, so
    adding their overlaps with some third repo would roughly double a real
    number. Max is the closest correct answer available without recomputing
    the overlap from the raw dump.

The self-loops this drops are not a rounding detail. Sorted by weight, the
single strongest shared-stargazer edge in the shipped dataset is

    ["huggingface/pytorch-image-models", "rwightman/pytorch-image-models", 9999]

which is one repository linked to itself with the cohort's largest possible
overlap count, and the same pair leads the semantic tier at 20 shared topics.
ROADMAP.md Phase 19 records the pair a third time, as the shared-issue-poster
tier's headline finding. All three were reading a repository's own rename as
its strongest relationship.

Files that a later pipeline step regenerates from scratch are still processed
here, so that the tree is consistent at every point rather than only after a
full re-run.

Usage: python3 scripts/45_apply_identity.py [--dry-run]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import alias_map, canonical  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data/processed"
REPO_LISTS = ROOT / "data/repo-lists"

# The cohort's node set, in the order build_web_explorer.py merges them: the
# later file's record wins a conflict. Handled as one unit rather than as two
# independent dicts, because a repository's two slugs routinely sit one in
# each -- see merge_aggregates.
AGGREGATE_FILES = ["repo_aggregates.json", "dependency_repo_aggregates.json"]

# {repo id: value}
DICTS_BY_REPO = [
    "repo_descriptions.json",
    "repo_topics.json",
    "repo_issue_titles.json",
    "repo_trophic_levels.json",
    "repo_topic_circular.json",
    "repo_costar_circular.json",
    "repo_text_embeddings.json",
    "repo_expansions.json",
]

# [[source, target, weight, [members]], ...] -- members optional
EDGE_LISTS = [
    "repo_shared_edges.json",
    "repo_shared_edges_pruned.json",
    "repo_shared_contributor_edges.json",
    "repo_shared_contributor_edges_pruned.json",
    "repo_shared_issue_author_edges.json",
    "repo_shared_issue_author_edges_pruned.json",
    "repo_semantic_edges.json",
    "repo_semantic_edges_pruned.json",
    "repo_dependency_edges.json",
    "repo_dependency_edges_pruned.json",
    "repo_go_dependency_edges.json",
    "repo_js_dependency_edges.json",
    "repo_java_dependency_edges.json",
    "repo_rust_dependency_edges.json",
    "repo_python_dependency_edges.json",
    "repo_cpp_dependency_edges.json",
]

# {key: repo id} -- the package/module/coordinate -> repo resolution tables
VALUE_MAPS = [
    "package_to_repo.json",
    "js_package_to_repo.json",
    "go_module_to_repo.json",
    "java_coord_to_repo.json",
    "crate_to_repo.json",
    "python_package_to_repo.json",
    "cpp_port_to_repo.json",
    "dependency_source_id_map.json",
]

REPO_LIST_FILES = [
    "top50_repos.txt", "dependency_extra_repos.txt", "expand15_repos.txt",
    "js_ecosystem_repos.txt", "java_ecosystem_repos.txt", "python_ecosystem_repos.txt",
    "go_ecosystem_repos.txt", "rust_ecosystem_repos.txt", "cpp_ecosystem_repos.txt",
]


def node_set():
    """Every canonical repo id the cohort knows, across both aggregate files.

    merge_dict needs this rather than one file's own keys -- see the module
    docstring for the stats-overwrite that taught the difference."""
    ids = set()
    for name in ("repo_aggregates.json", "dependency_repo_aggregates.json"):
        path = PROCESSED / name
        if path.exists():
            ids |= {canonical(r) for r in json.loads(path.read_text())}
    return ids


def is_empty(value):
    return value in (None, "", [], {})


def merge_aggregates(dry_run):
    """Collapse the two aggregate files together, then write each back.

    A rename usually leaves the old slug in the 51-repo SemRepo cohort and the
    new one in the dependency expansion, and the two files carry genuinely
    different real data about the same repository: the SemRepo side has a true
    `contributors` count and a stargazer figure capped at 10000, the expansion
    side has live uncapped GitHub stats but no contributor count at all. Taking
    either file's record wholesale would throw away something real, so the
    later file's record wins field by field and the earlier one fills only the
    gaps -- `langchain-ai/langchain` keeps its 141,857 stars *and* recovers the
    481 contributors that were sitting under `hwchase17/langchain`.

    The merged record is then written under the canonical id into every file
    that held the canonical or any of its aliases, so the two files agree and
    build_web_explorer.py's `update()` order stops mattering."""
    loaded = [(name, json.loads((PROCESSED / name).read_text()))
              for name in AGGREGATE_FILES if (PROCESSED / name).exists()]

    records = {}
    for _name, data in loaded:  # later file wins on the canonical entry itself
        for repo, value in data.items():
            if canonical(repo) == repo:
                records[repo] = dict(value) if isinstance(value, dict) else value
    for _name, data in loaded:  # every entry, alias included, fills gaps only
        for repo, value in data.items():
            canon = canonical(repo)
            if canon not in records:
                records[canon] = dict(value) if isinstance(value, dict) else value
            elif isinstance(value, dict) and isinstance(records[canon], dict):
                for field, field_value in value.items():
                    if is_empty(records[canon].get(field)) and not is_empty(field_value):
                        records[canon][field] = field_value

    dropped = 0
    for name, data in loaded:
        owned = {canonical(repo) for repo in data}
        dropped += len(data) - len(owned)
        out = {repo: records[repo] for repo in sorted(owned)}
        if len(out) != len(data):
            print(f"  {name}: {len(data)} -> {len(out)} entries", file=sys.stderr)
        if not dry_run:
            (PROCESSED / name).write_text(json.dumps(out, indent=0, sort_keys=True))
    print(f"  node set: {len(records)} repositories", file=sys.stderr)
    return dropped


def merge_dict(data, known):
    """Canonical entry wins; an alias only fills in fields it is missing.

    An alias is dropped outright when its canonical exists anywhere in the
    node set, whether or not it is in this same file. It is carried over
    under the canonical id only when the canonical exists nowhere, so a
    repository the cohort knows by its old name alone is not lost."""
    out = {}
    dropped = 0
    for repo, value in data.items():
        canon = canonical(repo)
        if canon == repo:
            out[canon] = value
    for repo, value in data.items():
        canon = canonical(repo)
        if canon == repo:
            continue
        dropped += 1
        if canon in out:
            if isinstance(value, dict) and isinstance(out[canon], dict):
                for field, field_value in value.items():
                    if out[canon].get(field) in (None, "", [], {}):
                        out[canon][field] = field_value
        elif canon not in known:
            out[canon] = value  # the cohort knows this repo only by its old name
    return out, dropped


def merge_edges(rows):
    """Remap endpoints, drop self-loops, merge collisions by max weight."""
    merged = {}
    self_loops = 0
    collisions = 0
    for row in rows:
        source, target, weight = canonical(row[0]), canonical(row[1]), row[2]
        members = row[3] if len(row) > 3 else None
        if source == target:
            self_loops += 1
            continue
        key = (source, target)
        if key in merged:
            collisions += 1
            previous = merged[key]
            previous[2] = max(previous[2], weight)  # overlap counts, never summed
            if members is not None and len(previous) > 3:
                previous[3] = sorted(set(previous[3]) | set(members))
        else:
            merged[key] = [source, target, weight] + ([sorted(set(members))] if members is not None else [])
    rows_out = sorted(merged.values(), key=lambda e: -e[2])
    return rows_out, self_loops, collisions


def merge_values(data):
    """Repoint a `key -> repo id` table at canonical repos.

    cpp_port_to_repo.json nests one such table per registry (`vcpkg`,
    `conan`), so a one-level-deep dict of dicts is handled rather than
    special-cased away."""
    out = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out[key] = {inner: canonical(repo) for inner, repo in value.items()}
        else:
            out[key] = canonical(value)
    return out


def main(*args):
    dry_run = "--dry-run" in args
    aliases = alias_map()
    if not aliases:
        print("no repo_identity.json (or no aliases in it) -- nothing to collapse", file=sys.stderr)
        return
    print(f"{len(aliases)} duplicate slugs to collapse"
          f"{' (dry run)' if dry_run else ''}\n", file=sys.stderr)

    known = node_set()
    total_dropped = merge_aggregates(dry_run)
    for name in DICTS_BY_REPO:
        path = PROCESSED / name
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        out, dropped = merge_dict(data, known)
        total_dropped += dropped
        if dropped:
            print(f"  {name}: {len(data)} -> {len(out)} entries ({dropped} merged)",
                  file=sys.stderr)
        if not dry_run:
            path.write_text(json.dumps(out, indent=0, sort_keys=True))

    for name in EDGE_LISTS:
        path = PROCESSED / name
        if not path.exists():
            continue
        rows = json.loads(path.read_text())
        out, self_loops, collisions = merge_edges(rows)
        if self_loops or collisions:
            print(f"  {name}: {len(rows)} -> {len(out)} edges "
                  f"({self_loops} self-loops dropped, {collisions} merged)", file=sys.stderr)
        if not dry_run:
            path.write_text(json.dumps(out, separators=(",", ":")))

    for name in VALUE_MAPS:
        path = PROCESSED / name
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        out = merge_values(data)
        changed = sum(1 for k in data if data[k] != out[k]) if not any(
            isinstance(v, dict) for v in data.values()) else sum(
            1 for k, v in data.items() if isinstance(v, dict)
            for inner in v if v[inner] != out[k][inner])
        if changed:
            print(f"  {name}: {changed} resolutions repointed to the canonical repo",
                  file=sys.stderr)
        if not dry_run:
            path.write_text(json.dumps(out, indent=0, sort_keys=True))

    for name in REPO_LIST_FILES:
        path = REPO_LISTS / name
        if not path.exists():
            continue
        entries = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        # Input order is preserved deliberately. Re-sorting would rewrite these
        # files wholesale and bury the handful of lines identity actually
        # changed under hundreds of reordered ones -- and these lists are read
        # as sets everywhere, so the order carries no meaning to defend.
        seen, out = set(), []
        for entry in entries:
            canon = canonical(entry)
            if canon not in seen:
                seen.add(canon)
                out.append(canon)
        renamed = sum(1 for e in entries if canonical(e) != e)
        if renamed or len(out) != len(entries):
            print(f"  repo-lists/{name}: {len(entries)} -> {len(out)} repos "
                  f"({renamed} renamed to their canonical slug)", file=sys.stderr)
        if not dry_run:
            path.write_text("\n".join(out) + "\n")

    # The cluster hierarchy is regenerated downstream from the collapsed node
    # set, so it is deliberately not rewritten in place here -- rewriting a
    # membership list without re-running Leiden would leave cluster sizes and
    # the stabilization snapshot disagreeing with the data they describe.
    print(f"\ncollapsed {total_dropped} duplicate entries across data/processed/"
          f"{' (dry run: nothing written)' if dry_run else ''}", file=sys.stderr)
    print("re-run 14/21/22 (clusters), 15 (trophic) and 16 (topic angle) to rebuild what "
          "depends on the node set", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
