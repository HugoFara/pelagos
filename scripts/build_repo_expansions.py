#!/usr/bin/env python3
"""Parse data/raw/repo_expansions.nt (from 04_repo_expansions.sh) into
data/processed/repo_expansions.json: {repo: [[predicate, objectType, objectLabel], ...]}.

## Person objects are dropped

hasStargazer/hasWatcher name a real individual by GitHub login, and the
explorer rendered each as a clickable node that live-fetched and displayed
that person's GitHub bio. data/processed/ is committed and inlined into
web/index.html, so shipping them published a browsable roster of named
people together with their profiles -- the most personal data anywhere in
this pipeline, and inconsistent with the shared-stargazer edge tier, which
has always shipped counts with no member names at all.

They're filtered out here rather than in the frontend so no login reaches
data/processed/ in the first place. The raw .nt keeps them (data/raw/ is
gitignored) so any internal analysis that needs person-level detail can
still read it directly.

hasContributorReference survives: its labels are opaque ids
("contributorRef #234"), not logins. The shared-contributor and
shared-issue-author tiers keep per-edge members, but pseudonymized -- see
compute_shared_edges.py.

Usage: python3 build_repo_expansions.py raw/repo_expansions.nt processed/repo_expansions.json
"""
import json
import sys
from collections import Counter
from ntparse import parse_line, short_predicate, repo_name, object_type_label

DROPPED_OBJECT_TYPES = {"person"}


def main(raw_nt_path, out_path):
    edges = {}
    dropped = Counter()
    with open(raw_nt_path) as f:
        for line in f:
            parsed = parse_line(line)
            if not parsed:
                continue
            subj, pred, obj, _kind = parsed
            repo = repo_name(subj)
            otype, olabel = object_type_label(obj)
            if otype in DROPPED_OBJECT_TYPES:
                dropped[short_predicate(pred)] += 1
                continue
            edges.setdefault(repo, []).append([short_predicate(pred), otype, olabel])

    with open(out_path, "w") as f:
        json.dump(edges, f, indent=0, sort_keys=True)

    kept = sum(len(v) for v in edges.values())
    detail = ", ".join(f"{n} {p}" for p, n in sorted(dropped.items())) or "none"
    print(f"wrote expansions for {len(edges)} repos to {out_path}: {kept} entries kept, "
          f"{sum(dropped.values())} person-object entries dropped ({detail}) -- see module "
          f"docstring; the raw .nt still has them", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} raw_nt out_json")
    main(*sys.argv[1:])
