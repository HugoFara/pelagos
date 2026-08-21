#!/usr/bin/env bash
# Full list of distinct repos that have at least one usedPackage triple -- the
# "dependency-graph cohort" (~21k repos, mostly small/academic, as of the
# 2025-05-11 dump). Full-file scan; slow, run once and cache the output.
# Usage: SEMREPO_NT=/path/to/SemRepo.nt ./scripts/extract/repos_with_packages.sh > data/raw/pkg_repos.txt
set -euo pipefail
: "${SEMREPO_NT:?Set SEMREPO_NT to the path of the SemRepo .nt file}"

grep -a -oP '^<https://semrepo\.org/repository/\K[^>]+(?=> <https://semrepo\.org/property/usedPackage>)' "$SEMREPO_NT" | sort -u
