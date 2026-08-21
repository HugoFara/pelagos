#!/usr/bin/env bash
# usedPackage edges (repo -> PyPI-style package name) for a given list of repos.
# This is real dependency data, but only covers a specific ~21k-repo cohort in the
# dataset (looks paper/Papers-with-Code linked -- see hasLpwcUrl/hasMlseaUrl) that
# does NOT overlap with the top-starred "famous repo" cohort. Check coverage with
# scripts/extract/repos_with_packages.sh before assuming a given repo has data here.
# Usage: SEMREPO_NT=/path/to/SemRepo.nt ./scripts/extract/used_packages.sh repo_list.txt > data/raw/repo_packages.nt
set -euo pipefail
: "${SEMREPO_NT:?Set SEMREPO_NT to the path of the SemRepo .nt file}"
REPO_LIST="${1:?Usage: $0 repo_list.txt}"

PATTERNS=$(mktemp)
trap 'rm -f "$PATTERNS"' EXIT
sed 's#^#<https://semrepo.org/repository/#; s#$#>#' "$REPO_LIST" > "$PATTERNS"

grep -a -F -f "$PATTERNS" "$SEMREPO_NT" | grep -a 'property/usedPackage>'
