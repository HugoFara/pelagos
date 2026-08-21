#!/usr/bin/env bash
# hasContributor edges for a given list of repos, rewritten to the same
# <repository/X> <predicate> <person/Y> shape scripts/lib/shared_edges.py expects --
# needed to compute real pairwise shared-contributor overlap between repos, a
# genuine technical/organizational coupling signal (unlike shared-stargazer
# audience overlap -- see NOTES.md).
#
# Contributors hang off a per-repo contributorreference/{owner}/{repo}/{n} node
# (hasContributorReference from the repo, hasContributor -> person/{username}
# from the reference node), rather than directly off the repo like
# hasStargazer. The reference node's own URI already embeds the owning repo,
# so a grep + subject rewrite is enough -- no join against
# hasContributorReference required.
#
# Usage: SEMREPO_NT=/path/to/SemRepo.nt ./scripts/extract/shared_contributors.sh repo_list.txt > data/raw/repo_contributors_full.nt
set -euo pipefail
: "${SEMREPO_NT:?Set SEMREPO_NT to the path of the SemRepo .nt file}"
REPO_LIST="${1:?Usage: $0 repo_list.txt}"

PATTERNS=$(mktemp)
trap 'rm -f "$PATTERNS"' EXIT
sed 's#^#<https://semrepo.org/contributorreference/#; s#$#/#' "$REPO_LIST" > "$PATTERNS"

grep -a -F -f "$PATTERNS" "$SEMREPO_NT" | grep -a 'property/hasContributor>' \
  | sed -E 's#<https://semrepo\.org/contributorreference/([^>]+)/[0-9]+>#<https://semrepo.org/repository/\1>#'
