#!/usr/bin/env python3
"""A fifth "linked by a common person" repo-repo signal (ROADMAP.md Phase
19): two repos whose issues were filed by the same people. Same family as
hasStargazer/hasContributor (05/07) -- hasIssueAuthor is exactly the same
issue/repo->person triple shape -- but resolved via a two-hop join instead
of a direct grep, since hasIssueAuthor's subject is the *issue*, not the
repo (`<repository/{owner}/{repo}/issue/{n}> hasIssueAuthor person/{who}`).

Sampling design (the real reason this phase sat flagged): hasIssue alone is
2.6M triples dump-wide, two orders of magnitude past anything else this
pipeline fetches per-repo. Capped per-repo, not exhaustive -- same "sample,
not full neighborhood" idiom 04_repo_expansions.sh already uses for
individual-level issue nodes, just a higher cap (this is a signal-detection
input, not a handful of display nodes) picked after actually measuring the
real distribution: only 74/319 of the current cohort have *any* hasIssue
data in this dump at all (a coverage gap, not a bug -- same shape as the
co-star/topic gaps elsewhere in this pipeline), and of those 74, issue
counts range from single digits into the tens of thousands (pytorch/pytorch
alone has 20000, the dump's own per-repo cap). A cap of 40 (first-
encountered in file order, deterministic, same convention as 04's MAX_ISSUE)
keeps the two-hop join's working set small (well under 3000 sampled issues
total) while still giving every covered repo a real, if partial, sample.

Two-pass because the join key (issue id) sits on the *object* side of
hasIssue but the *subject* side of hasIssueAuthor/dc:title: pass 1 walks
hasIssue lines to decide which issues got sampled (applying the cap), pass 2
filters hasIssueAuthor/title lines to just that sampled set. Both passes
grep the raw dump directly (same two-stage filter-then-parse shape as
09_resolve_packages.py) rather than shelling out via a separate .sh script,
since the cross-predicate join/cap logic doesn't reduce to a single grep|awk
one-liner the way 04/05/07's simpler single-predicate extractions do.

Usage: SEMREPO_NT=/path/to/SemRepo.nt python3 23_shared_issue_authors.py \
    repo_list.txt [out_authors_nt=data/raw/repo_issue_authors_full.nt] \
    [out_titles_json=data/processed/repo_issue_titles.json] [max_issues_per_repo=40]
"""
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ntparse import parse_line, short_predicate, repo_name  # noqa: E402


def grep(nt_path, patterns):
    return subprocess.run(
        ["grep", "-a", "-F", "-f", "/dev/stdin", nt_path],
        input="\n".join(patterns), capture_output=True, text=True, check=False,
    ).stdout


def main(repo_list_path, out_authors_nt="data/raw/repo_issue_authors_full.nt",
         out_titles_json="data/processed/repo_issue_titles.json", max_issues_per_repo=40):
    nt_path = os.environ.get("SEMREPO_NT")
    if not nt_path:
        sys.exit("Set SEMREPO_NT to the path of the SemRepo .nt file")
    max_issues_per_repo = int(max_issues_per_repo)

    repos = [l.strip() for l in Path(repo_list_path).read_text().splitlines() if l.strip()]
    repo_set = set(repos)

    # Pass 1: hasIssue lines for the cohort (repo as exact subject, trailing
    # '>' -- same PATTERNS shape 05/07 use -- so this can't also pick up
    # pass 2's issue-subject lines below).
    patterns = [f"<https://semrepo.org/repository/{r}>" for r in repos]
    raw1 = grep(nt_path, patterns)
    per_repo_count = defaultdict(int)
    sampled_issues = {}  # issue_id ("owner/repo/issue/N") -> repo
    for line in raw1.splitlines():
        parsed = parse_line(line)
        if not parsed:
            continue
        subj, pred, obj, kind = parsed
        if kind != "uri" or short_predicate(pred) != "hasIssue":
            continue
        repo = repo_name(subj)
        if repo not in repo_set:
            continue
        if per_repo_count[repo] >= max_issues_per_repo:
            continue
        per_repo_count[repo] += 1
        sampled_issues[repo_name(obj)] = repo

    covered = len(per_repo_count)
    print(f"{len(sampled_issues)} issues sampled (cap {max_issues_per_repo}/repo) across "
          f"{covered}/{len(repos)} cohort repos with any hasIssue data", file=sys.stderr)

    author_lines = []
    titles = defaultdict(list)
    if sampled_issues:
        # Pass 2: exact issue-subject patterns for just the sampled set --
        # bounded (<= len(repos) * cap), so this stays a precise, cheap grep
        # instead of a repo-prefix substring match that could also pull in
        # issues that didn't make the cap.
        issue_patterns = [f"<https://semrepo.org/repository/{iid}>" for iid in sampled_issues]
        raw2 = grep(nt_path, issue_patterns)
        for line in raw2.splitlines():
            parsed = parse_line(line)
            if not parsed:
                continue
            subj, pred, obj, kind = parsed
            issue_id = repo_name(subj)
            repo = sampled_issues.get(issue_id)
            if not repo:
                continue
            pred_short = short_predicate(pred)
            if pred_short == "hasIssueAuthor" and kind == "uri":
                person = obj.replace("https://semrepo.org/person/", "")
                # Two real quirks in this predicate specifically (not seen in
                # hasStargazer/hasContributor): "ghost" is GitHub's shared
                # placeholder for a deleted account -- every repo with any
                # issue from a since-deleted user gets the same "ghost"
                # value, which would fabricate a shared-person edge between
                # otherwise-unrelated repos. And some bot authors are
                # recorded as a full nested URI under person/
                # (<person/https://semrepo.org/bot/{name}[bot]>) rather than
                # a plain username -- automated, not a real person-to-person
                # overlap either way. Both skipped rather than counted.
                if person == "ghost" or "[bot]" in person or "/" in person:
                    continue
                author_lines.append(
                    f"<https://semrepo.org/repository/{repo}> "
                    f"<https://semrepo.org/property/hasIssueAuthor> "
                    f"<https://semrepo.org/person/{person}> .\n"
                )
            elif pred == "http://purl.org/dc/terms/title" and kind == "literal":
                titles[repo].append(obj)

    Path(out_authors_nt).parent.mkdir(parents=True, exist_ok=True)
    Path(out_authors_nt).write_text("".join(author_lines))
    Path(out_titles_json).write_text(json.dumps(titles, separators=(",", ":"), sort_keys=True))

    distinct_authors = {ln.rsplit("<https://semrepo.org/person/", 1)[1][:-3] for ln in author_lines}
    print(f"{len(author_lines)} issue-author triples ({len(distinct_authors)} distinct people) over "
          f"{len(set(ln.split('>', 1)[0][1:] for ln in author_lines))} repos -> {out_authors_nt}", file=sys.stderr)
    print(f"issue titles for {len(titles)} repos ({sum(len(v) for v in titles.values())} titles total) "
          f"-> {out_titles_json}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(f"usage: {sys.argv[0]} repo_list.txt [out_authors_nt] [out_titles_json] [max_issues_per_repo=40]")
    main(*sys.argv[1:])
