#!/usr/bin/env bash
# Capped per-repo neighbor sample (issues/stargazers/watchers/fork/contributor-ref)
# for a given list of repos, in a single pass over the file. Caps keep the output
# small even for repos with thousands of real edges -- this is a *sample*, not the
# full neighborhood.
# Usage: SEMREPO_NT=/path/to/SemRepo.nt ./04_repo_expansions.sh repo_list.txt > data/raw/repo_expansions.nt
set -euo pipefail
: "${SEMREPO_NT:?Set SEMREPO_NT to the path of the SemRepo .nt file}"
REPO_LIST="${1:?Usage: $0 repo_list.txt}"
MAX_ISSUE="${MAX_ISSUE:-12}"
MAX_STAR="${MAX_STAR:-12}"
MAX_WATCH="${MAX_WATCH:-3}"
MAX_FORK="${MAX_FORK:-1}"
MAX_CONTRIB="${MAX_CONTRIB:-1}"

PATTERNS=$(mktemp)
trap 'rm -f "$PATTERNS"' EXIT
sed 's#^#<https://semrepo.org/repository/#; s#$#>#' "$REPO_LIST" > "$PATTERNS"

grep -a -F -f "$PATTERNS" "$SEMREPO_NT" | \
awk -v maxIssue="$MAX_ISSUE" -v maxStar="$MAX_STAR" -v maxWatch="$MAX_WATCH" \
    -v maxFork="$MAX_FORK" -v maxContrib="$MAX_CONTRIB" '
  match($0, /^<https:\/\/semrepo\.org\/repository\/([^>]+)> <https:\/\/semrepo\.org\/property\/(hasIssue|hasStargazer|hasWatcher|forkedAs|hasContributorReference)> /, a) {
    repo = a[1]; pred = a[2];
    limit = (pred=="hasIssue") ? maxIssue : (pred=="hasStargazer") ? maxStar : (pred=="hasWatcher") ? maxWatch : (pred=="forkedAs") ? maxFork : maxContrib;
    key = repo SUBSEP pred;
    count[key]++;
    if (count[key] <= limit) print;
  }
'
