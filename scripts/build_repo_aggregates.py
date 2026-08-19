#!/usr/bin/env python3
"""Parse data/raw/repo_aggregates.nt (from 03_repo_aggregates.sh) into
data/processed/repo_aggregates.json: {repo: {title, stargazers, forks,
openIssues, watchers, contributors}}.

Usage: python3 build_repo_aggregates.py repo_list.txt raw/repo_aggregates.nt processed/repo_aggregates.json
"""
import json
import sys
from ntparse import parse_line, short_predicate, repo_name


def main(repo_list_path, raw_nt_path, out_path):
    with open(repo_list_path) as f:
        repos = [l.strip() for l in f if l.strip()]

    agg = {}
    with open(raw_nt_path) as f:
        for line in f:
            parsed = parse_line(line)
            if not parsed:
                continue
            subj, pred, obj, _kind = parsed
            agg.setdefault(repo_name(subj), {})[short_predicate(pred)] = obj

    out = {}
    for repo in repos:
        a = agg.get(repo, {})
        out[repo] = {
            "title": a.get("title", repo.split("/")[-1]),
            "stargazers": int(a["hasTotalStargazers"]) if "hasTotalStargazers" in a else None,
            "forks": int(a["hasTotalForks"]) if "hasTotalForks" in a else None,
            "openIssues": int(a["hasTotalOpenIssues"]) if "hasTotalOpenIssues" in a else None,
            "watchers": int(a["hasTotalWatchers"]) if "hasTotalWatchers" in a else None,
            "contributors": int(a["hasTotalContributor"]) if "hasTotalContributor" in a else None,
        }

    with open(out_path, "w") as f:
        json.dump(out, f, indent=0, sort_keys=True)

    missing = [r for r in repos if not agg.get(r)]
    print(f"wrote {len(out)} repos to {out_path}", file=sys.stderr)
    if missing:
        print(f"no aggregate data found for: {missing}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(f"usage: {sys.argv[0]} repo_list.txt raw_nt out_json")
    main(*sys.argv[1:])
