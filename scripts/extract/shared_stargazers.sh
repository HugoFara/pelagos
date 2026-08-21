#!/usr/bin/env bash
# Full hasStargazer edges for a given list of repos (no per-repo cap) -- needed to
# compute real pairwise shared-stargazer overlap between repos. For repos capped at
# 10000 stargazers this is ~10000 lines/repo, so keep the input list short (tens,
# not hundreds, of repos) or this gets big fast.
# Usage: SEMREPO_NT=/path/to/SemRepo.nt ./scripts/extract/shared_stargazers.sh repo_list.txt > data/raw/repo_stargazers_full.nt
set -euo pipefail
: "${SEMREPO_NT:?Set SEMREPO_NT to the path of the SemRepo .nt file}"
REPO_LIST="${1:?Usage: $0 repo_list.txt}"

PATTERNS=$(mktemp)
trap 'rm -f "$PATTERNS"' EXIT
sed 's#^#<https://semrepo.org/repository/#; s#$#>#' "$REPO_LIST" > "$PATTERNS"

grep -a -F -f "$PATTERNS" "$SEMREPO_NT" | grep -a 'property/hasStargazer>'
