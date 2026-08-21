#!/usr/bin/env bash
# Exact per-predicate triple counts over the full .nt file.
# Usage: SEMREPO_NT=/path/to/SemRepo.nt ./scripts/extract/predicate_counts.sh > data/raw/predicate_counts.txt
set -euo pipefail
: "${SEMREPO_NT:?Set SEMREPO_NT to the path of the SemRepo .nt file}"

grep -a -oP '^<[^>]+> <\K[^>]+(?=> )' "$SEMREPO_NT" | sort | uniq -c | sort -rn
