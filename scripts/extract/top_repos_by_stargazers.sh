#!/usr/bin/env bash
# Repos ranked by their real hasTotalStargazers literal (repository/* subjects only,
# not forkedRepo/*). Note: this dataset appears to cap the field at 10000 for very
# popular repos (a scrape/pagination ceiling, not a live GitHub count) -- expect a
# large tied block at the top rather than a clean ranking.
# Usage: SEMREPO_NT=/path/to/SemRepo.nt ./scripts/extract/top_repos_by_stargazers.sh [N] > data/raw/top_repos_by_stargazers.txt
set -euo pipefail
: "${SEMREPO_NT:?Set SEMREPO_NT to the path of the SemRepo .nt file}"
N="${1:-200}"

grep -a -P '^<https://semrepo\.org/repository/[^>]+> <https://semrepo\.org/property/hasTotalStargazers> "[0-9]+"' "$SEMREPO_NT" | \
  sed -E 's#^<https://semrepo\.org/repository/([^>]+)> <[^>]+> "([0-9]+)".*#\2\t\1#' | \
  sort -t $'\t' -k1,1nr | head -n "$N"
