#!/usr/bin/env bash
# hasTotal* + dc:title triples for a given list of repos (single pass over the file).
# Usage: SEMREPO_NT=/path/to/SemRepo.nt ./03_repo_aggregates.sh repo_list.txt > data/raw/repo_aggregates.nt
#   repo_list.txt: one "owner/name" per line (no scheme, no angle brackets)
set -euo pipefail
: "${SEMREPO_NT:?Set SEMREPO_NT to the path of the SemRepo .nt file}"
REPO_LIST="${1:?Usage: $0 repo_list.txt}"

PATTERNS=$(mktemp)
trap 'rm -f "$PATTERNS"' EXIT
sed 's#^#<https://semrepo.org/repository/#; s#$#>#' "$REPO_LIST" > "$PATTERNS"

grep -a -F -f "$PATTERNS" "$SEMREPO_NT" | grep -a -E 'property/hasTotal|dc/terms/title'
