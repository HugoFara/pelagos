# Building the data

This is the full reference: where the data comes from, how every derived file
in `data/processed/` is produced, and the reasoning behind each choice. It was
the project's original README, kept intact rather than trimmed — [the README
](../README.md) is now the short introduction, and [`NOTES.md`](../NOTES.md)
is the running log of what was measured and what turned out wrong.

You do **not** need any of this to run the explorer: `data/processed/` is
committed, so `web/index.html` builds and runs from a plain clone.

## Why

Tools like [anvaka's Map of GitHub](https://github.com/anvaka/map-of-github)
already cluster hundreds of thousands of repos by shared-stargazer similarity.
That's a real signal, but it's an *audience* signal, not a *technical* one:
repos that genuinely depend on each other can have very different stargazer
populations (a low-level library vs. the end-user tool built on it), so
shared-stargazer graphs end up clustering mostly by language/ecosystem rather
than by actual coupling. This project starts from the same kind of data but
aims to be explicit about which edges are real structural relationships
(dependencies, forks, shared contributors) vs. which are similarity proxies
(shared stargazers/watchers) — see [`NOTES.md`](./NOTES.md) for the live
design discussion.

Nor is this redundant with [deps.dev](https://deps.dev) (Google's Open Source
Insights), the closest prior art to this project's backend. `deps.dev`
reconstructs the transitive *package* dependency graph straight from
registries — its node is a package/version, and its one relation is "depends
on." This project's node is a *repo*, and the explorer's path finder searches
a real four-tier multigraph (dependency, shared-contributor, shared-stargazer,
shared-topic) as a single graph, so it can answer "how are these two repos
connected at all" in a way a single-relation graph structurally can't:
`dmlc/xgboost` and `psf/requests` — a gradient-boosting library and the most-
used HTTP client, nothing in common on the surface — have no direct edge in
this dataset across *any* of the four tiers, only a real 2-hop bridge through
`dmlc/dgl` (723 shared stargazers, and `dgl` genuinely depends on the
`requests` package). Try it in the path finder's seeded examples. This
also means the package→repo resolution `deps.dev`/Libraries.io do at the
registry level has to be solved here too, just aimed at repos instead of
packages — `scripts/09_resolve_packages.py` (PyPI-only today) is that piece.

## Data source

The source file is a local 12 GB N-Triples dump, `SemRepo_2025-05-11.nt`
(82,078,636 triples, 53 distinct predicates), currently living at
`data/raw/SemRepo_2025-05-11.nt`. It's too large to commit (`data/raw/` is
gitignored), so every script in `scripts/` reads its path from the
`SEMREPO_NT` environment variable:

```bash
export SEMREPO_NT=data/raw/SemRepo_2025-05-11.nt
```

SemRepo publishes its data under [CC0](https://semrepo.org/index.php/open-science/)
(public domain dedication) and its own pipeline under MIT, so the derived
files committed in `data/processed/` here are freely redistributable. It is
still a substantial part of what this project shows: 31% of the drawn edges
are SemRepo-derived, and it is the *only* source for three of the five edge
tiers (shared-stargazer, shared-contributor, shared-issue-author) -- those
need per-person stargazer/contributor lists across the whole cohort, which is
tens of millions of calls via the GitHub API but a single grep pass here.
Everything else -- the ~7000-repo cohort itself, descriptions, topics,
READMEs, avatars, and five of the six dependency-manifest ecosystems -- comes
from the GitHub API. Repo *identity* (which slugs name the same repository,
and which other forges serve it) comes from neither: it is read straight off
the origins themselves with `git ls-remote`, since a git object id is a hash
of the object's content and therefore agrees across forges by construction.
See "One repository, many origins" below.

### People in this dataset

Two edge tiers are built from *people*: shared-contributor and
shared-issue-author. Their per-edge member lists are
**pseudonymized before anything is written to `data/processed/`** -- each
person becomes a stable `person-<12 hex>` label derived from a salt that lives
in gitignored `data/raw/pseudonym_salt.txt` and never ships. See
`scripts/compute_shared_edges.py` for why the salt has to stay out of the repo
(GitHub usernames are an enumerable public set, so an unsalted or
salt-alongside-data hash would be trivially reversible) and for the honest
limits of this -- it is pseudonymization, not anonymization.

Every weight, degree, cluster and layout value is computed from overlap
*counts*, so none of them depend on the labels; the identities were only ever
displayed in edge tooltips and Compare mode.

## Layout

```
scripts/    grep/awk extraction passes over the .nt file + Python parsers
            that turn the raw triples into small JSON/CSV in data/processed/
data/
  repo-lists/   the curated repo cohorts these scripts operate on
  processed/    small, committed, derived data (JSON, tables) -- the actual
                output of the pipeline
  raw/          gitignored; holds the source SemRepo_2025-05-11.nt dump itself
                plus cached grep/awk extraction output -- regenerate the
                latter via scripts/ + SEMREPO_NT rather than committing it.
                Two of its caches are the fetch halves of the multi-ecosystem
                and repo-identity work: repo_manifest_cache/ (one JSON per
                repo, every manifest path its git tree holds plus the text of
                the ones downloaded) and repo_refs_cache/ (one JSON per
                origin, its intrinsic git object ids). Both are large and both
                are resumable -- an interrupted sweep just continues.
web/        the interactive graph explorer -- web/index.html renders a real
            3D scene (three.js/WebGL for node fills/avatars/edges, a Canvas-2D
            overlay for rings/badges/labels -- see ROADMAP.md Phase 18) plus
            all its sidebar panels as one page with no backend. Nothing to
            build to *use* it: just open web/index.html, it and its sibling
            web/scene3d.bundle.js are both committed, generated artifacts.
            web/index.html itself is generated from web/template.html +
            data/processed/*.json by scripts/build_web_explorer.py, which
            inlines the JSON as JS literals (file:// pages can't fetch() JSON
            due to CORS). Re-run that script after regenerating
            data/processed/ and commit the resulting web/index.html.
            web/scene3d.bundle.js is separately built from web/src/scene3d.js
            via esbuild (`cd web && npm install && npm run build`, see
            web/package.json) -- only needs re-running if that source itself
            changes, not on every data refresh. Clicking a repo or person node
            does make one live, unauthenticated request to the public GitHub
            REST API (api.github.com/repos or /users) to show its current star
            count/bio/language -- that data isn't in the SemRepo dump and
            scraping it for every node up front isn't feasible, so it's
            fetched on demand instead. Cohort repos' *descriptions*
            specifically are the exception: scripts/12_cache_repo_descriptions.py
            pre-fetches those once and ships them inline, so hovering a repo
            node shows its description immediately with no network request
            at all -- only clicking (for the fuller, genuinely-live stats)
            still hits the API. Needs network access for that click-through
            data and for any repo outside the cohort; GitHub caps
            unauthenticated requests at 60/hour per IP, surfaced in the panel
            as an error with a retry link if hit.
  web/logos/  one small PNG per repo owner (their GitHub avatar), downloaded
            once via scripts/08_download_repo_logos.py and committed --
            drawn clipped into each repository node's WebGL sprite. Every
            cohort owner has one today; the one that didn't turned out to be
            a renamed org, which 08 now recovers via the API (a *deleted*
            account still can't be). An owner with no logo file falls back to
            the flat node color, and build_web_explorer.py ships that list so
            the renderer skips the request instead of 404ing for it.
```

## One repo, many ecosystems

Six of the pipeline's dependency sources were each built the same way: take
`data/repo-lists/{lang}_ecosystem_repos.txt`, probe one fixed manifest path at
each repo's root, parse whatever comes back. That works, and it quietly
encodes an assumption that is simply false — that a repo belongs to one
ecosystem, the one GitHub's `language:` search facet named when the cohort was
collected.

That facet reports a repo's *dominant* language by bytes. One value per repo.
Real repos are routinely several ecosystems at once: a Rust crate with a
`package.json` for its wasm bindings or docs site, a C++ library with a
`pyproject.toml` for its Python wheel, a Go service with a JS frontend. Under
the old scheme every one of those dependency lists was unreachable, not
because resolution failed but because nothing ever looked.

Measured on a random 240-repo sample of the cohort (40 from each ecosystem
list), by reading each repo's full git tree:

| | repos | share |
|---|---|---|
| have a **root** manifest for an ecosystem other than their own | 49 | 20% |
| have one at **any depth** | 99 | 41% |
| have **no root manifest at all**, but do have nested ones | 25 | 10% |
| own ecosystem's manifest exists only nested, never at root | 27 | 11% |

The largest single miss was Rust repos carrying a `package.json` — 12 of the
40 sampled.

`scripts/42_scan_repo_manifests.py` replaces the six root probes with one
`git/trees/HEAD?recursive=1` read per repo, which returns every manifest path
in the repo at once, and then fetches the blobs through aliased GraphQL.
`scripts/manifests.py` is the read side that the six `*_dependency_edges.py`
scripts now share.

**Two things a wider net catches that you do not want.** A manifest under
`node_modules/` or `third_party/` is another project's dependency list, and
attributing it to whoever vendored it would invent edges nobody declared.
Directory names catch most of that. They cannot catch the rest: `0-KaiKai-0/SH2`
carries a whole checked-in copy of huggingface/transformers under a plain
`transformers/` directory, contributing 68 `requirements.txt` files that
declare huggingface's dependencies. Content hashes settle that without
guessing — a manifest whose exact bytes appear in more than one repository,
nested rather than at that repo's own root, is a copy. Around 4% of
non-vendored manifests, and it catches the `transformers/` tree, a fairseq
tree copied into 13 repos, and a `TeViT-main/` tree copied into 31. Root
manifests are exempt: ten research repos really do share one byte-identical
root `requirements.txt`, and it is each of their own declaration.

Both exclusions happen at **read** time, following the convention
`32_fetch_java_manifests.py` set for `buildSrc/`: the cache records every path
the tree contained, so either rule can be re-measured and changed without
re-fetching anything.

## One repository, many origins

Every node id in this pipeline was an `owner/name` GitHub slug, and the
pipeline assumed one slug was one repository. It is not, and the shipped
7,051-slug cohort proved it from data already cached:

- **5 rename pairs where both slugs were separate nodes** —
  `hwchase17/langchain` + `langchain-ai/langchain`, `microsoft/guidance` +
  `guidance-ai/guidance`, `tensorflow/magenta` + `magenta/magenta`,
  `thudm/chatglm-6b` + `zai-org/ChatGLM-6B`, and `rwightman/pytorch-image-models`
  + `huggingface/pytorch-image-models`.
- **62 nodes GitHub itself marks as forks**, 18 shadowing an upstream that was
  also a node (5 separate forks of `facebookresearch/fairseq`, 3 of
  `OpenNMT/OpenNMT-py`, 2 of `huggingface/transformers`).

The cost of that was not cosmetic. Sorted by weight, the single strongest
shared-stargazer edge in the dataset was `huggingface/pytorch-image-models` ↔
`rwightman/pytorch-image-models` at 9999 — one repository linked to itself
with the largest overlap count the cohort can express. The same pair led the
semantic tier at 20 shared topics, and `ROADMAP.md` Phase 19 recorded it a
third time as the shared-issue-poster tier's headline finding, "the real
`timm` library rename, not a coincidence". It was a rename, and it was being
read as a relationship.

**What settles it.** A git object id is a hash of the object's content, so two
origins serving the same repository publish byte-identical ids for every ref
they have in common — no name, URL or heuristic involved. That is the property
Software Heritage's SWHIDs are built on, and it is exact rather than
approximate.

It is also why Software Heritage is not in this path. SWH's `swh:1:rev:` /
`swh:1:rel:` ids *are* the git object ids — verified directly against the
kernel.org Linux origin, where all 940 of its `ls-remote` tag ids matched
SWH's snapshot targets exactly. So `git ls-remote` yields the same intrinsic
identifiers for any forge, with no crawl-coverage dependency and no rate limit
(SWH's anonymous quota is 700 requests/hour, roughly 20 hours for this
cohort). `scripts/43_repo_refs.py` reads them; `scripts/44_repo_identity.py`
groups on them.

Merging uses **containment**, not Jaccard, and the Linux pair shows why:

```
github.com/torvalds/linux              2259 intrinsic ref ids
git.kernel.org .../torvalds/linux.git  1881 shared, containment 1.000
```

Jaccard on that is 0.42. A mirror legitimately carries fewer refs than what it
mirrors — it lags, or mirrors only branches, or only tags — so being a strict
subset is the *expected* shape of a mirror, and Jaccard punishes exactly that.
The threshold is high on purpose: a hard fork that became its own project
keeps developing, so its own refs quickly outnumber the inherited ones and its
containment falls away from 1.

`torvalds/linux` is a mirror. Under slug identity it was a node in its own
right, at the bottom of the trophic axis, standing in for a repository whose
real origin this dataset does not contain.

**Cross-forge origins are verified, never trusted.** `ls-remote` can verify an
origin but cannot discover one, and Software Heritage's public REST API — the
natural place to ask "what else serves this revision" — does not expose that
query, only a substring search over origin URLs that does not surface
`git.kernel.org/.../linux.git` for the query "linux" at all. Doing discovery
properly means the SWH graph dataset on AWS Open Data, which is its own
ingestion project. So candidates come from `data/repo-lists/upstream_origins.txt`,
a curated list, and each is checked by the same ref-overlap rule before it is
recorded. A wrong line there produces a printed rejection, not a wrong merge —
and 8 of 84 candidates were rejected, including `golang/go` against
go.googlesource.com (0.087 containment: a genuinely partial mirror) and three
origins that turned out not to be git at all.

**What the id is.** The record key stays the canonical origin's `owner/name`.
That is deliberate: identity is decided intrinsically, but the *label* has to
stay readable and permalink-stable, and every downstream file, avatar and
shared URL here is keyed by slug. Each record also carries `anchor` — the
repository's root commit, fetched for merged groups only — which is the
genuinely forge-independent, time-stable identifier: identical across every
mirror, fork and rename, and unlike a ref-set digest it does not change when
someone pushes a tag.

## Pipeline (reproducing `data/processed/`)

```bash
export SEMREPO_NT=data/raw/SemRepo_2025-05-11.nt

# Export it before running anything below, and re-export it in any new shell.
# The dump-scanning scripts guard with `: "${SEMREPO_NT:?}"`, but that fires
# only after the shell has already truncated the target of a `>` redirect --
# so `scripts/05_shared_stargazers.sh "$COHORT" > data/raw/repo_stargazers_full.nt`
# with SEMREPO_NT unset destroys the existing file and writes nothing. Redirect
# to a .tmp and `mv` it into place after checking it's non-empty if you're
# re-running these against an already-populated data/raw/.

# schema overview (53 predicates, exact counts over all 82M triples)
scripts/01_predicate_counts.sh > data/processed/predicate_counts.txt

# candidate repo cohort: ranked by real hasTotalStargazers (capped at 10000
# in this dump -- see NOTES.md)
scripts/02_top_repos_by_stargazers.sh 300 > data/raw/top_repos_by_stargazers_capped.txt

# aggregate stats (real hasTotalForks/hasTotalOpenIssues/hasTotalWatchers/
# hasTotalContributor/title) for the curated cohort in data/repo-lists/top50_repos.txt
scripts/03_repo_aggregates.sh data/repo-lists/top50_repos.txt > data/raw/repo_aggregates.nt
python3 scripts/build_repo_aggregates.py data/repo-lists/top50_repos.txt \
  data/raw/repo_aggregates.nt data/processed/repo_aggregates.json

# capped individual-level neighbor sample (issues/stargazers/watchers/fork/
# contributor-ref) for the subset in data/repo-lists/expand15_repos.txt
scripts/04_repo_expansions.sh data/repo-lists/expand15_repos.txt > data/raw/repo_expansions.nt
python3 scripts/build_repo_expansions.py data/raw/repo_expansions.nt data/processed/repo_expansions.json

# real repo-repo edges from full (uncapped) shared-stargazer overlap --
# top50 only at this point in the pipeline (the dependency-expansion cohort
# below doesn't exist yet on a from-scratch run); re-run against the full
# cohort once it does, see the backfill step near the end of this list
scripts/05_shared_stargazers.sh data/repo-lists/top50_repos.txt > data/raw/repo_stargazers_full.nt
python3 scripts/compute_shared_edges.py data/raw/repo_stargazers_full.nt \
  data/processed/repo_shared_edges.json data/processed/repo_shared_edges_pruned.json

# which repos have real usedPackage dependency data (a disjoint cohort --
# see NOTES.md)
scripts/find_repos_with_packages.sh > data/processed/dependency_cohort_repos.txt

# real repo-repo edges from full shared-contributor overlap (technical/
# organizational coupling, not an audience proxy -- see NOTES.md); reuses
# compute_shared_edges.py since both are repo<->person bipartite graphs.
# The trailing "1" embeds the actual shared usernames per edge (contributor
# overlaps top out around a few dozen, unlike shared-stargazers, so this is
# small enough to ship to the browser) -- shown on edge hover in the explorer.
scripts/07_shared_contributors.sh data/repo-lists/top50_repos.txt > data/raw/repo_contributors_full.nt
python3 scripts/compute_shared_edges.py data/raw/repo_contributors_full.nt \
  data/processed/repo_shared_contributor_edges.json \
  data/processed/repo_shared_contributor_edges_pruned.json 2 4 1 1

# one avatar PNG per repo owner, for the explorer to draw on repo nodes
# (see web/logos/ above)
python3 scripts/08_download_repo_logos.py data/repo-lists/top50_repos.txt web/logos

# real repo-repo dependency edges: resolve each of the 186 distinct
# usedPackage package names to the GitHub repo that publishes it (PyPI's
# JSON API, cached to data/raw/pypi_cache/)
python3 scripts/09_resolve_packages.py

# grow the cohort with the resolved "library" repos plus a top-N slice of
# the dependency cohort itself (ranked by distinct-dependency count, capped
# at 50% per language bucket so this stream can't just keep taking the
# most-Python(/notebook) candidates -- see LANGUAGE_QUOTA_CAP in the script);
# tries the dump first, falls back to `gh api` for the rest, caching every
# response to data/raw/github_cache/
python3 scripts/10_fetch_new_repo_stats.py

# grow the cohort per language, then give each language real dependency
# edges from its own manifest. The SemRepo dump's usedPackage predicate is
# PyPI-only (186 distinct packages back all 95,505 triples), so every
# non-Python repo above arrives edge-less; each pair below closes that for
# one ecosystem by fetching the real manifest and resolving each declared
# coordinate to a GitHub repo through that language's registry. All of them
# feed 11_dependency_edges.py's *single* combined prune (see its docstring)
# rather than pruning separately.
#
# Each fetch script is idempotent and cached, so re-running is cheap; each
# edge script leaves genuinely unresolvable coordinates edge-less rather
# than guessing a repo for them.
python3 scripts/24_fetch_js_ecosystem_repos.py       # JS/TS repos
python3 scripts/25_fetch_java_ecosystem_repos.py     # Java repos
python3 scripts/26_fetch_python_ecosystem_repos.py   # Python repos
python3 scripts/27_fetch_go_ecosystem_repos.py       # Go repos
python3 scripts/34_fetch_rust_ecosystem_repos.py     # Rust repos
python3 scripts/39_fetch_cpp_ecosystem_repos.py      # C/C++ repos

python3 scripts/28_fetch_go_mod.py         && python3 scripts/29_go_dependency_edges.py
python3 scripts/30_fetch_package_json.py   && python3 scripts/31_js_dependency_edges.py
python3 scripts/32_fetch_java_manifests.py && python3 scripts/33_java_dependency_edges.py
  # ^ Java also sweeps every repo's *submodules*: a Maven/Gradle root file
  #   usually lists children rather than dependencies, so root-only reading
  #   left 619 of 811 Java repos with a manifest and no edges. ~2k GitHub
  #   requests (threaded, ~15min) + ~8.8k Maven Central lookups (~30min),
  #   both fully cached -- see NOTES.md
python3 scripts/35_fetch_cargo_toml.py     && python3 scripts/36_rust_dependency_edges.py
python3 scripts/37_fetch_python_manifests.py && python3 scripts/38_python_dependency_edges.py
python3 scripts/40_fetch_cpp_manifests.py  && python3 scripts/41_cpp_dependency_edges.py

# One repo, several ecosystems. Everything above pairs one language list with
# one fixed manifest path at repo root, which ties two facts together that
# have no reason to agree: "GitHub's `language:` facet calls this repo Rust"
# and "this repo declares its dependencies in Cargo.toml, at the root". The
# facet reports a repo's dominant language by bytes, one value per repo, and
# real repos are routinely several ecosystems at once.
#
# This sweeps every cohort repo's git tree for every ecosystem's manifests at
# any depth -- one `git/trees/HEAD?recursive=1` read per repo, then aliased
# GraphQL for the blobs (50 paths per query, 1 rate-limit point, the same
# mechanics 32's Java-only submodule pass already used). Measured on a random
# 240-repo sample before it was written: 20% of repos carry a ROOT manifest
# for an ecosystem other than their own, 41% carry one at some depth, 11%
# have their own ecosystem's manifest only nested, and 10% have no root
# manifest at all while shipping real nested ones -- silently edge-less until
# now. Idempotent and resumable; a full cohort sweep is ~1h.
#
# After this, each *_dependency_edges.py script above reads
# scripts/manifests.py instead of its own language list, which also drops
# vendored trees (node_modules/, third_party/) and manifests that are
# byte-identical copies of another repo's file -- someone else's dependency
# list, not this repo's own declaration. Re-run the six edge scripts (they
# need no fetch step of their own any more) and then 11 to recombine.
python3 scripts/42_scan_repo_manifests.py
for s in 29_go 31_js 33_java 36_rust 38_python 41_cpp; do
  python3 scripts/${s}_dependency_edges.py
done

# A node is a repository, not a GitHub slug. 43 reads every cohort origin's
# intrinsic git object ids (`git ls-remote`; an object id is a hash of the
# object's content, so two origins serving the same repository publish
# byte-identical ids for every ref they share), and 44 turns that plus
# GitHub's own rename/fork metadata into one record per repository with a set
# of origins. 45 then collapses the duplicates out of data/processed/.
#
# This is not hypothetical cleanup: the shipped 7,051-slug cohort contained 5
# rename pairs where both slugs were separate nodes and 18 fork nodes
# shadowing an upstream that was also a node. The strongest shared-stargazer
# edge in the dataset -- and the shared-issue-poster tier's headline finding
# in ROADMAP.md Phase 19 -- was `huggingface/pytorch-image-models` linked to
# `rwightman/pytorch-image-models`, which is one repository linked to itself.
#
# 44 also verifies candidate non-GitHub origins from
# data/repo-lists/upstream_origins.txt by the same ref-overlap rule, so
# `torvalds/linux` is recorded as a mirror of git.kernel.org rather than as
# the origin of the kernel. That file is a candidate generator, never
# evidence: a wrong line in it produces a printed rejection, not a wrong
# merge. Software Heritage is deliberately not in this path -- its SWHIDs for
# git origins *are* these object ids (verified: 940/940 of the kernel.org
# tags), so ls-remote gets the same identifiers with no crawl-coverage
# dependency and no 700-request/hour quota. What SWH could add is
# *discovery* of sibling origins, which its public REST API does not expose;
# that needs the SWH graph dataset on AWS Open Data.
python3 scripts/43_repo_refs.py
python3 scripts/44_repo_identity.py
python3 scripts/45_apply_identity.py

# backfill: now that the full cohort (top50 + dependency-expansion repos)
# is known, re-run the shared-stargazer/shared-contributor extraction above
# against all 319, not just the original top50 -- the dump's hasStargazer/
# hasContributor coverage turned out not to be limited to the original
# cohort (88/319 and 89/319 respectively once queried against the full
# list; see NOTES.md), so restricting those two steps to top50 was
# silently leaving real data on the table. Re-running is cheap (~15s each
# over the 12GB dump) so just redo both from scratch rather than trying to
# diff/append.
FULL_COHORT=$(mktemp)
cat data/repo-lists/top50_repos.txt data/repo-lists/dependency_extra_repos.txt | sort -u > "$FULL_COHORT"
scripts/05_shared_stargazers.sh "$FULL_COHORT" > data/raw/repo_stargazers_full.nt
python3 scripts/compute_shared_edges.py data/raw/repo_stargazers_full.nt \
  data/processed/repo_shared_edges.json data/processed/repo_shared_edges_pruned.json
scripts/07_shared_contributors.sh "$FULL_COHORT" > data/raw/repo_contributors_full.nt
python3 scripts/compute_shared_edges.py data/raw/repo_contributors_full.nt \
  data/processed/repo_shared_contributor_edges.json \
  data/processed/repo_shared_contributor_edges_pruned.json 2 4 1 1
rm -f "$FULL_COHORT"

# build the directed edges themselves + prune (top-4-per-node by degree,
# since every edge here has weight 1 -- see NOTES.md), then fetch owner
# avatars for the newly-added repos
python3 scripts/11_dependency_edges.py
python3 scripts/08_download_repo_logos.py data/repo-lists/dependency_extra_repos.txt web/logos

# pre-fetch each cohort repo's live GitHub description once, cached to
# data/raw/github_cache/ like scripts 10/11 above, so hovering a repo node
# in the explorer doesn't have to spend part of the unauthenticated
# 60-req/hour GitHub API budget on every node the mouse passes over
python3 scripts/12_cache_repo_descriptions.py

# a fourth repo-repo edge type: two repos sharing GitHub topic tags (a
# first, crude cut at a semantic/subject-matter relationship). Reuses the
# `topics` field already sitting in the github_cache/ responses fetched
# above -- no new network calls -- and compute_shared_edges.py's overlap +
# top-K-pruning core (min 2 shared, top-4 per node)
python3 scripts/13_semantic_edges.py

# Phase 19: a fifth repo-repo edge tier -- shared issue posters, same
# "linked by a common person" family as shared-stargazer/shared-contributor
# above. hasIssueAuthor's subject is the *issue*, not the repo, so this is a
# two-hop join (hasIssue: repo->issue, then hasIssueAuthor: issue->person),
# capped at 300 issues/repo (first-encountered in file order -- picked by
# measuring, not guessing; see ROADMAP.md) so the join's working set stays
# small even though hasIssue is 2.6M triples dump-wide. Also writes each
# repo's sampled issue titles to repo_issue_titles.json, folded into the
# text-embedding signal by scripts/18 below.
FULL_COHORT=$(mktemp)
cat data/repo-lists/top50_repos.txt data/repo-lists/dependency_extra_repos.txt | sort -u > "$FULL_COHORT"
python3 scripts/23_shared_issue_authors.py "$FULL_COHORT" data/raw/repo_issue_authors_full.nt \
  data/processed/repo_issue_titles.json 300
rm -f "$FULL_COHORT"
python3 scripts/compute_shared_edges.py data/raw/repo_issue_authors_full.nt \
  data/processed/repo_shared_issue_author_edges.json \
  data/processed/repo_shared_issue_author_edges_pruned.json 2 4 1 1

# Phase 14: real prose per repo for the text-embedding similarity signal
# below. README fetched fresh via authenticated `gh api` (5000 req/hour, so
# 319 sequential requests is a non-issue -- contrast the unauthenticated
# 60/hour cap the live frontend has to work around), cached raw markdown to
# data/raw/readme_cache/
python3 scripts/17_fetch_readmes.py

# clean each README down to its first real prose paragraph (badges/HTML/
# code-blocks/RST-directives/language-switcher-lines stripped -- see
# NOTES.md for the two real failure modes found doing this) and embed
# `description + topics + that paragraph + a repo's sampled issue titles if
# any` (Phase 19) with BAAI/bge-small-en-v1.5 via fastembed (pip install
# fastembed; ONNX runtime, no PyTorch/CUDA needed -- this project's third
# tracked Python dependency; see NOTES.md)
python3 scripts/18_text_embeddings.py

# precompute a multi-level cluster hierarchy, used by web/index.html to
# render/simulate a bounded number of cluster meta-nodes instead of every
# repo at once as the cohort grows. Clusters on a co-star PMI + topic PMI +
# text-embedding-cosine similarity graph (dependency edges deliberately
# excluded -- they drive the y-axis below instead, see NOTES.md),
# sparsified with mutual-kNN, then partitioned with Leiden -- this
# project's second tracked Python dependency (pip install leidenalg
# python-igraph; see NOTES.md for why Leiden over the earlier hand-rolled
# Louvain). Needs scripts/18's output to exist first for the text signal --
# degrades gracefully to the two-signal substrate if it doesn't.
python3 scripts/14_cluster_hierarchy.py

# Phase 15, part one: cross-run cluster id stability. scripts/14 mints
# cluster ids positionally, which reshuffles on any data refresh that
# changes Leiden's community ordering -- silently breaking any permalink
# that names a cluster. Matches this run's clusters against the previous
# stabilized snapshot (data/processed/repo_cluster_hierarchy_prev.json) by
# Jaccard similarity on membership, solved as an assignment problem
# (scipy.optimize.linear_sum_assignment -- already installed as a
# leidenalg/fastembed transitive dependency, no new pip install needed).
# A confident match keeps the old id; an unmatched cluster mints a fresh
# one. First run ever has nothing to match against and just seeds the
# baseline untouched.
python3 scripts/21_stabilize_cluster_ids.py

# Phase 15, part two: readable cluster labels, replacing the hub-repo-name
# placeholder scripts/14 sets. Per cluster: pool members' real
# description/topics/README text (scripts/18's build_embedding_text,
# reused as-is), reduce to the cluster's most distinctive terms via
# c-TF-IDF (Grootendorst 2022 -- terms common across every cluster score
# low automatically, no hand-maintained stopword list needed beyond
# ordinary English function words), then one `claude -p` call turning
# those already-real terms into a short readable label -- this project's
# first non-Python-library dependency, the `claude` CLI itself, invoked
# offline/no-tools. Falls back to a plain terms-only heuristic label if
# the CLI call fails/isn't available, so the pipeline never hard-fails on
# it. Labels are cached permanently by cluster id + a membership
# signature (data/processed/cluster_labels.json, committed) so a rerun
# with unchanged clusters costs zero LLM calls -- needs part one's stable
# ids to make that cache key meaningful at all.
python3 scripts/22_label_clusters.py

# Phase 12 coordinate system -- first scripts here to need numpy (pip
# install numpy; see NOTES.md for why this is the first tracked Python
# dependency). Trophic height (the graph's y-axis): solves Lh=v over the
# dependency graph's Laplacian, robust to cycles unlike longest-path depth
python3 scripts/15_trophic_levels.py

# circular topic embedding (the graph's theta-axis + free coherence
# radius): PMI-weighted topic co-occurrence graph, spectral-embedded in 2D,
# so a repo's angle is the TF-IDF-weighted circular mean of its topics'
# angles instead of an arbitrarily-ordered sector
python3 scripts/16_topic_circular_embedding.py

# Phase 14's contrarian-claim test: does a co-star-PMI-driven theta beat
# the topic-driven one above? Spectral-embeds the co-star repo-repo graph
# directly, then scores both against Phase 14's real Leiden clusters.
# Answer, and why topic-driven theta still ships despite losing on
# precision: see NOTES.md. Not required for the main build -- an analysis
# script, its output isn't consumed by build_web_explorer.py.
python3 scripts/19_costar_circular_embedding.py
python3 scripts/20_compare_theta_sources.py
```

## Status

- Schema mapped: 53 predicates, exact counts over the full file.
- 51-repo "famous/top-starred" cohort with real aggregate stats (forks, open
  issues, watchers, contributors — all real, not simulated) plus 15 of them
  expanded with a real individual-level neighbor sample.
- Real repo-repo graph computed from full shared-stargazer overlap, pruned to
  each node's top-4 strongest edges (151 edges over 51 nodes, all 51 retain
  at least one connection).
- A second, real repo-repo edge type: shared-contributor overlap (via
  `contributorreference/{repo}/{n}` -`hasContributor`-> `person/{username}`,
  the same identity space as stargazers), pruned the same way (min 2 shared,
  top-4 per node) -- 59 edges over 50 nodes (`torvalds/linux` has no
  contributor data in this dump). Unlike shared-stargazers this is a genuine
  technical/organizational coupling signal, not an audience proxy -- see
  `NOTES.md`. Off by default in `web/index.html`; hovering an edge once shown
  names the actual shared contributor(s).
- All four repo-repo edge types (see below for the third and fourth) are
  independent legend toggles with distinct colors, and each actually pulls
  its connected repos together in the force layout while checked -- not
  just drawn on top of a layout it doesn't affect. Dependency is on by
  default; shared-stargazer, shared-contributor (both "linked via a shared
  person" proxies rather than a direct structural relationship), and
  semantic (shared GitHub topic tags) start off.
- Repo nodes render the owner's real GitHub avatar (`web/logos/`, downloaded
  once, see Pipeline) clipped into the node circle instead of a flat color.
- `web/index.html` now renders the real two-tier LOD: the 51-repo aggregate
  layer (sized by fork count, edges from the shared-stargazer graph above)
  with 14 of those repos (the `expand15_repos.txt` cohort, minus
  `compvis/stable-diffusion` which isn't in the 51-repo cohort -- see
  below) expandable in place into their individual-level neighbor sample
  (issues/stargazers/watchers/forks/contributor-refs) via
  `data/processed/repo_expansions.json`. Force-directed, pan/zoom/drag,
  click-to-expand/collapse, no longer wired to the old 2-repo hand-curated
  demo data. Nodes start on a jittered ring rather than a full random
  scatter, per-tick speed is clamped, and a repo's individual-level sample
  fans out around it and pre-settles before the next paint -- a random
  scatter occasionally spawned near-coincident nodes, and expanding a repo's
  ~30-node sample used to fling already-settled nodes hundreds of px away in
  a single tick.
- Clicking a repo/forked-repo/person/issue node fetches its live
  description/bio/title (plus star count for repos, state/comment count for
  issues) straight from the public GitHub API and shows it in the side panel,
  with loading/error/retry states -- the one piece of "what is this, right
  now" data the static dataset can't provide. Hovering a node not covered by
  the description cache below does the same fetch (debounced ~350ms so
  sweeping across a cluster doesn't burn through the 60/hour cap) and shows
  it in the tooltip instead of the raw semrepo node URI. (Contributor-ref
  nodes aren't backed by a single fetchable GitHub resource, so they still
  just show the URI.)
- A third, genuinely structural repo-repo edge type: real dependency edges,
  resolved from `usedPackage` triples (repo → PyPI package → the GitHub repo
  that publishes it) — directed and arrowed, unlike the two proxy edge
  types above. Grew the cohort from 51 to 319 repos (168 newly-discovered
  "library" repos the packages resolve to, plus a curated top-100 slice of
  the ~21,100-repo dependency cohort itself), 863 edges after top-4-by-
  degree pruning (weight is always 1 here, so degree drives pruning instead
  — see `NOTES.md`). **On by default** — the one edge type that isn't a
  shared-person proxy; hovering an edge names the real package(s) behind it.
- Open question on which relationship should drive the main graph, resolved:
  real dependency edges now ship alongside the two proxy edge types, and are
  the default view — see `NOTES.md`.
- Every cohort repo's live GitHub description is pre-fetched once
  (`scripts/12_cache_repo_descriptions.py`) and shipped inline in
  `repo_aggregates.json`/`web/index.html`, so hovering a repo node shows its
  description immediately with zero network requests -- previously every
  hover (debounced, but still per-node) spent part of the unauthenticated
  60/hour GitHub API budget just to show tooltip text, which stopped scaling
  once the cohort grew to 319 repos. Clicking still live-fetches (star/fork
  counts and language genuinely are live); only the hover path changed.
- A fourth repo-repo edge type: semantic edges from shared GitHub topic tags
  (`scripts/13_semantic_edges.py`), reusing the `topics` field already
  present in the cached GitHub API responses from the two scripts above --
  no new network calls needed. 348 edges over 134 of the 184 tagged repos
  (min 2 shared tags, top-4 per node). Off by default; hovering an edge
  names the actual shared tags. Deliberately the crude version -- literal
  tag-string overlap, not NLP/embedding similarity -- see `NOTES.md` for
  the planned follow-up over repo descriptions/READMEs.
- Level-of-detail clustering: `scripts/14_cluster_hierarchy.py` precomputes
  a multi-level cluster hierarchy. `web/index.html` renders/simulates only
  the top-level cluster meta-nodes by default and lazily expands one into
  its real children on click or zoom-in, so render/simulation cost stays
  bounded regardless of how large the cohort grows -- the all-pairs O(n²)
  force simulation and canvas draw loop never see more nodes than are
  actually on screen. Six-degrees pathfinding, Compare mode, search, and
  permalink restore all auto-expand whatever cluster currently stands in
  for a specific repo, so picking a repo by id still works regardless of
  how collapsed the graph currently is. As of Phase 13 below, clustering
  runs on co-star + topic similarity, not dependency, which changed the
  top-level count from 28 to 169 (14 real clusters + 155 repos with no
  clustering signal shown directly) -- see that phase for why.
- Two seeded examples in the path finder (`dmlc/xgboost` <-> `psf/requests`,
  `Kludex/starlette` <-> `NVIDIA-NeMo/Speech`) demonstrate real cross-tier
  chains -- both pairs have zero direct edge in this dataset across any of
  the four tiers, only a genuine 2-hop bridge. See "Why" above for what
  that's meant to show. (Re-picked after the co-star/contributor data
  backfill below invalidated the original two picks -- see that section.)
- Coordinate system v2 (Phase 12): the graph's y-axis is now trophic height
  (`scripts/15_trophic_levels.py`, solved from the dependency graph's
  Laplacian rather than walked as longest-path depth), and its horizontal
  placement is a circular topic embedding (`scripts/16_topic_circular_embedding.py`
  -- angle from a PMI-weighted topic co-occurrence graph's 2D spectral
  embedding, radius from the same circular mean's resultant length, i.e.
  real topic coherence). Both are soft targets a constrained force
  simulation settles into, not a hard layout -- see `NOTES.md` for the
  sign-convention verification and a real finding worth knowing before
  reading too much into the y-axis: this cohort's dependency graph is
  currently exactly bipartite (0 chains longer than one hop), so trophic
  height today separates into essentially two real bands, not yet a rich
  gradient. Shared-stargazer and shared-contributor edges are no longer a
  standing drawn layer at all -- both still contribute a gentle attraction
  force, but only ever render when they touch the currently hovered or
  selected repo (first tracked Python dependency: `numpy`, for the linear
  solve and spectral embedding -- see `NOTES.md`).
- Clustering v2 (Phase 13): the LOD hierarchy above now clusters a PMI-
  weighted co-star + topic similarity graph, sparsified with mutual-kNN
  (`k=20`) and partitioned with Leiden (`leidenalg`/`python-igraph`, the
  second tracked Python dependency) instead of the old dependency-inclusive
  Louvain union -- Leiden fixes a real correctness bug Louvain has
  (internally disconnected communities), not just a naming change. Produces
  clearly thematic clusters (web-frameworks/async, generative-AI/LLM-chat,
  classic-ML, computer-vision, NLP/transformers, core-PyTorch, notebook
  tooling, general-purpose libraries, gradient-boosting, Pallets micro-libs)
  instead of communities that mostly separated by package ecosystem. Cluster
  meta-nodes already inherited Phase 12's coordinate system by construction
  (`clusterLayoutTargetFor()` predates this phase) and still render
  dependency crossing-edges when collapsed, via the existing per-tier edge
  aggregation -- neither needed a change. See `NOTES.md` for the resolution
  search and the real finding that 155/319 repos have no co-star or topic
  signal at all and now show up as standalone repos rather than a forced
  grouping.
- Co-star/contributor data backfill: the shared-stargazer and shared-
  contributor extraction (`scripts/05_shared_stargazers.sh`/
  `07_shared_contributors.sh`) originally only ever queried the raw dump
  for the top-50 cohort, so every repo added later via dependency expansion
  structurally had zero co-star/contributor signal regardless of whether
  the dump actually had data for it. Re-running both against the full
  319-repo cohort recovered real coverage for 88 repos (stargazer) and 89
  repos (contributor), up from 51 and ~31 -- shrinking Phase 13's "no
  clustering signal" population from 166 to 155 repos. It also surfaced a
  real direct shared-stargazer edge between `pytorch/pytorch` and
  `pytorch/vision` (1052 shared stargazers) that simply hadn't been queried
  for before, which invalidated Phase 11's original two seeded path-finder
  examples (both had been picked specifically because they looked like they
  had *no* direct edge) -- replaced with two pairs re-verified against the
  current full data (`dmlc/xgboost` <-> `psf/requests`, `Kludex/starlette`
  <-> `NVIDIA-NeMo/Speech`). See `NOTES.md`.
- Real similarity signal via text embeddings (Phase 14): `scripts/
  17_fetch_readmes.py` + `scripts/18_text_embeddings.py` embed
  `description + topics + a cleaned README first paragraph` per repo with
  `BAAI/bge-small-en-v1.5` (`fastembed`, the third tracked Python
  dependency -- ONNX runtime, no PyTorch) and fuse the cosine-similarity
  result into Phase 13's clustering substrate. 317/319 repos end up
  embeddable, versus ~155/319 for the two PMI signals alone -- the "no
  clustering signal" population drops from 166 to 84, and the cluster
  count grows from 14 to 20, still with no ecosystem-boundary or generic-
  jargon mega-cluster (a real one appeared during development -- ~50
  small research-paper repos welded together purely by shared academic-
  README phrasing -- and needed a text-tier-specific mutual-kNN, not just
  a cosine threshold, to actually fix; see `NOTES.md`). Also runs the
  review's contrarian-claim test from Phase 12: a co-star-PMI-driven theta
  (`scripts/19_costar_circular_embedding.py`) turns out genuinely *more
  precise* than the topic-driven theta Phase 12 shipped (checked via
  within-cluster circular concentration against Phase 14's own real
  clusters, `scripts/20_compare_theta_sources.py`) -- but only covers
  79/319 repos against topic's 169/319, so topic-driven theta stays the
  shipped axis. A real, answered test, not a skipped one -- see `NOTES.md`
  for the full numbers and the coverage-vs-precision reasoning.
- Cluster id stability and real labels (Phase 15): `scripts/
  21_stabilize_cluster_ids.py` matches each run's fresh Leiden clusters
  against the previous run's by Jaccard similarity on membership (Hungarian
  assignment, `scipy.optimize.linear_sum_assignment`), so a data refresh
  that reshuffles Leiden's internal community ordering no longer reshuffles
  cluster ids and silently breaks permalinks that name one -- verified with
  a synthetic before/after pair exercising same-membership-different-index,
  partial-overlap, and no-overlap cases, since this cohort's real data
  hasn't yet produced two genuinely different snapshots to test against
  (the 0.5 Jaccard match threshold is picked from general practice, not
  tuned against this project's own history yet -- revisit once it has
  some). `scripts/22_label_clusters.py` replaces the old hub-repo-name
  placeholder with a real category label per cluster: c-TF-IDF over
  members' real description/topic/README text picks each cluster's most
  distinctive terms, then one cached `claude -p` call turns those terms
  into readable prose (`Async Python Web`, `Gradient Boosting Libraries`,
  `Scene Text Recognition`, ...) -- see `NOTES.md` for the full label set
  and the reasoning against the project's earlier "not a guessed name"
  stance.
- Real 3D view (Phase 18): the graph explorer moved from a 2D canvas to a
  real three.js/WebGL scene, ahead of `ROADMAP.md` Phase 17's own
  scale-driven WebGL trigger -- a deliberate call at the current ~319-node
  scale, made explicitly over a lighter dependency-free alternative. `y =
  r*sin(theta)`, the natural depth axis the 2D view's own coordinate system
  had always computed but never rendered, is now real, orbitable depth.
  Hybrid rendering: node fills/avatars/edges/dependency arrowheads go
  through WebGL (real depth/occlusion), rings/badges/labels stay Canvas-2D
  on an overlay layer (heavily stateful, cheap, not worth reimplementing as
  WebGL materials at this node count). Picking, node dragging, LOD auto-
  expand/collapse, and camera framing all moved onto `OrbitControls` and
  real perspective-aware screen-space queries, replacing the old 2D pan/
  zoom entirely. A labeled vertical reference line marks the trophic-height
  axis, since a rotatable view (unlike the old fixed 2D plane) has no other
  way to convey which direction is meaningful. See `NOTES.md` for the full
  build history, including a real `this`-binding bug the test suite's own
  construction initially masked, and why `headless-gl` couldn't provide
  headless WebGL verification for this phase.
- Hierarchical edge bundling (Phase 16): dependency edges now route
  through the shared ancestor of their two endpoints in the existing
  cluster hierarchy instead of a dead-straight chord across the topic
  circle, converging visually wherever two edges share an ancestor
  (Holten 2006, generalized to 3D). Real finding along the way: only 13 of
  863 dependency edges share a genuine cluster ancestor at full expansion
  (expected -- Phase 13 deliberately excluded dependency edges from the
  clustering substrate), so the `CLUSTERS` forest is rooted at one
  synthetic hub at the exact world origin so every edge still bundles
  through at least one real waypoint. See `NOTES.md` for the full
  measure-first build history and the bundling-strength tuning call.
- Issue-poster edges + issue-text semantics (Phase 19): a fifth repo-repo
  edge tier, shared issue posters (`scripts/23_shared_issue_authors.py`,
  same "linked by a common person" family as shared-stargazer/shared-
  contributor, same not-a-legend-toggle treatment) -- 137 edges over 59 of
  the 74 cohort repos that have any issue data in this dump at all, from a
  capped 300-issues-per-repo sample (picked by measuring 40/100/150/200/300
  directly, not guessed). Real data-quality fix found along the way:
  GitHub's shared "ghost" placeholder for deleted accounts and bot authors
  (recorded as a nested URI under `person/`, a quirk specific to this
  predicate) were fabricating shared-person edges between unrelated repos
  until filtered out. Issue titles also folded into Phase 14's text-
  embedding input for the 74 covered repos; re-running the downstream
  clustering pipeline moved exactly those 74 repos' embeddings (avg cosine
  0.92 against the pre-change vectors, the other 243 unchanged) and gave
  Phase 15's cluster-id stabilization its first real (non-synthetic) test,
  matching 19 of 20 clusters to their previous stable id. See `NOTES.md`
  for the full measure-first build history, including a real finding from
  wiring this into six-degrees pathfinding: no shortest path in this
  cohort actually routes through the new tier, since every issue-poster-
  linked pair already has an equally-short connection via another tier.
- Cohort inflated from 319 to 1019 repos: `scripts/10_fetch_new_repo_stats.py`'s
  `top_n_source` raised from 100 to 800 (its `CANDIDATE_POOL` now scales
  with that target instead of a flat constant), well within the
  authenticated `gh api` cache-backed 5000 req/hour budget. Full downstream
  pipeline re-run at the new scale -- dependency edges to 3765/962 nodes,
  semantic edges to 745/281, 49 top-level/123 total Leiden clusters
  covering all 1019 repos, level-of-detail still bounds the initial
  render to 341 on-screen nodes. Real bug found and fixed in
  `scripts/22_label_clusters.py`: it only persisted cluster labels once,
  at the very end of a ~123-cluster `claude -p` labeling loop, so an
  interrupted run silently discarded every already-completed label; now
  checkpoints after each one. See `NOTES.md` for the full numbers and the
  coverage-fraction caveat on the shared-person edge tiers.
- Cohort inflated again, 1019 to 1983 repos (`top_n_source` 800 -> 1800).
  An unthrottled `gh api` loop in `scripts/10` tripped GitHub's rate limit
  mid-run (0/5000 remaining within minutes, confirmed directly, not
  inferred); fixed with a `time.sleep(0.4)` after every uncached call in
  `scripts/10`, `12_cache_repo_descriptions.py`, and `17_fetch_readmes.py`
  (cache hits are unaffected). Dependency edges to 7632/1929 nodes,
  semantic edges to 1331/499, 86 top-level/203 total Leiden clusters
  covering all 1983 repos, level-of-detail still bounds the initial render
  to 660 on-screen nodes. See `NOTES.md` for the full numbers and the
  rate-limit incident writeup.
- The graph is now static by default: repos/clusters spawn once at a
  fixed, deterministic `WORLD_POS` and physically cannot drift afterward
  (`tick()` skips their position update entirely) unless an opt-in
  pull-force slider (0 by default, new legend row, `#force=` in
  permalinks) is raised. Dependency/semantic edge force and global
  repulsion are decoupled from edge visibility and cut way down
  (`REPULSION_STRENGTH` 2800 -> 120) for the same reason. Collapsed/
  expanded clusters now show a real translucent `THREE.Mesh` volume built
  from their members' true (static) positions -- tessellated and
  Laplacian-smoothed to round off the raw convex hull's sharp facets, no
  wireframe -- colored by sibling position (not hierarchy level, which
  made every cluster at a shared level the same color) so whatever's on
  screen together reads as visually distinct. See `NOTES.md` for the full
  writeup, including two design attempts that were tried and replaced.
- Cluster meta-nodes no longer duplicate their own volume: the leftover
  solid marker disc, colored ring, and always-on member-count badge/label
  are gone, replaced by the volume itself plus a hover-only name/stats
  reveal. Volume readability also got three fixes grounded in the real
  cohort's actual geometry: each cluster's hull is trimmed to its
  90th-percentile-by-distance members (a few far-flung outliers were
  ballooning some hulls to 800+ world units), LOD auto-expand/collapse now
  measures the real hull radius instead of an unrelated fixed marker size,
  and at most 18 cluster volumes render at once, ranked by current
  on-screen prominence. A near-coplanar member set no longer hulls into a
  paper-thin sliver (minimum-thickness inflation per axis). Crowded repo
  labels in dense regions are a known, not-yet-fixed follow-up. See
  `NOTES.md` for the full before/after numbers.
- A toolbar "export rotation" button records one full 360° sweep of the
  camera around the trophic axis as a downloadable WebM, driving its own
  scripted camera loop (`Scene3D.getOrbitState()`/`setOrbitCamera()`)
  independently of the live render loop rather than trying to speed the
  latter up. `playwright` is now a committed `web/` devDependency
  (`web/tools/screenshot.mjs`) used for the AI's own first-pass visual
  verification of `web/` changes, replacing the previous "ask a human to
  check a real browser" convention now that headless Chromium can
  actually provide the real WebGL2 context `headless-gl` never could. See
  `NOTES.md` for the two real bugs this caught on first use.
- The default top-level view is now both fast and actually labeled. Two
  real, measured performance bugs (CPU-profiled via a new `web/tools/
  cpuprofile.mjs`) cut the live render loop's per-frame cost by roughly
  8x: edge geometry was rebuilt from scratch every frame regardless of
  whether anything moved, and the physics loop kept computing forces
  between pairs of permanently-frozen nodes it discards immediately
  after. Separately, cluster names used to only ever show on hover
  (leaving the large cluster volumes -- the most visually dominant shapes
  on screen -- unlabeled by default), and the initial camera looked
  straight down the one axis (`r*sin(theta)`) the whole Phase 18 move to
  3D exists to expose, silently flattening the "3D" view back to the old
  2D projection's silhouette on every load. See `NOTES.md` for the full
  diagnosis, numbers, and a real 1000-repo data-completeness gap found
  (not fixed) along the way.
- The dense point-blobs visible at the default zoom now actually mean
  something. Most of the cohort's 574 top-level singleton repos (no real
  Leiden cluster of their own) land within a handful of tight visual
  neighborhoods purely by trophic/topic position; a new grid-binning pass
  (`buildDeclutterPiles`, `web/template.html`) groups them into synthetic,
  honestly-labeled ("N repos") pile nodes with the same real hull/hover/
  expand behavior as a genuine cluster, cutting the default view's
  materialized top-level entities from 660 to 93. Separately, the cohort's
  biggest real clusters were undertrimmed: `CLUSTER_HULL_TRIM_FRACTION`
  0.9 -> 0.5 shrinks their hull radii 60-75% by dropping a long, sparse
  tail of outlier members that was ballooning them into huge, mostly-empty
  translucent volumes. See `NOTES.md` for the reverted first attempt (a
  global position-spreading fix that inflated unrelated real clusters'
  hulls) and the measured before/after numbers.
- The CLUSTERS side panel is genuinely live now, not a one-time snapshot:
  it lists whatever cluster meta-nodes are actually materialized (updates
  on every expand/collapse/reveal), each row's color matches its on-screen
  volume, and clicking a row focuses that cluster in the 3D view the same
  way search-to-jump does. Cluster volumes also got real per-cluster
  padding (`clusterVolumePadding`, scaled to each cluster's own hull
  radius) instead of a flat 40 units that swamped small clusters into
  near-identical spheres regardless of their real member spread. See
  `NOTES.md` for a second, deeper cause of the "sphere" look (a
  cluster-agnostic position tie-breaker) that was investigated and
  deliberately left unfixed after a first attempt measured worse, not
  better, for several real clusters.
- Cluster hull sizes are now actually bounded. Growing the cohort to 2983
  repos (the JS-ecosystem addition) surfaced real Leiden clusters with as
  few as 2 members whose hull radius hit 459 world units -- bigger than the
  entire topic-circle radius -- because the existing "keep the closest 50%"
  trim never trims a cluster with 4 or fewer members at all, and a real
  cluster grouped by non-spatial similarity (co-star/topic/text) can still
  have those few members sit far apart in the trophic/topic embedding.
  `clusterHullFor` now runs a second, iterative pass on top of the fraction
  trim -- repeatedly recentering on the current surviving set and dropping
  its farthest member -- capped at `CLUSTER_HULL_MAX_RADIUS = 130`, picked
  empirically the same way the fraction trim was. Measured directly: max
  hull radius across this cohort's 244 real clusters drops 459 -> 126,
  clusters past 100 units drop from 73/244 to 5/244 (mean 93 -> 40), and a
  real Playwright screenshot of the default view shows the two dominant,
  overlapping, label-crushing blobs from before replaced by several
  smaller, mostly-distinct volumes with legible labels. See `NOTES.md` for
  the two other, more visible-looking hypotheses (declutter-pile grid size,
  the cluster-agnostic position-tie fan above) that were checked directly
  against real instrumented data and ruled out before finding this cause,
  plus a separately-flagged, not-yet-fixed bug found along the way: repo
  avatar textures fail to load under `file://` (a CORS "origin null" issue
  in `THREE.TextureLoader`), likely silently defeating the avatar feature
  for anyone using the app via its documented primary distribution method.
- Selecting a node now turns the sidebar into a focused inspector for it:
  "Selected node" expands and scrolls into view while every other panel
  folds shut (except the one that drove the selection, so walking down the
  Clusters list doesn't fold that list away). A selected repo also gets a
  **Read README** button opening a reader over the graph at real reading
  width -- the pipeline's own README cache is 96MB for 7051 repos and
  exists only as embedding input, so the reader fetches the one repo asked
  for, live, via `Accept: application/vnd.github.html` (GitHub's own
  rendered, sanitized HTML -- no markdown parser to maintain, and image
  URLs come back already absolute). It renders in a sandboxed `srcdoc`
  iframe with no `allow-scripts`. Two real findings, both fixed: GitHub
  prefixes heading ids with `user-content-` while leaving a README's own
  table-of-contents links pointing at the unprefixed fragment (34 dead
  links in `nlohmann/json` alone), and a `srcdoc` document resolves URLs
  against its *parent* page, so a `#install` link navigated the reader to
  `index.html#install` -- loading a second full copy of this 11.8MB
  explorer inside itself. `<base href="about:srcdoc">` was the one of four
  probed variants that makes fragment links the same-document scroll they
  read as (a `blob:` URL doesn't work at all under sandbox). Link hrefs
  are absolutized the way github.com resolves them; unlike the live
  star/fork readout the README is a button rather than automatic, since
  the unauthenticated API budget is 60 requests/hour/IP.
- Console cleared, one real fix and one measured non-issue. The single
  `logos/*.png` 404 was a renamed owner (`flagalpha` -> `LlamaChinese`):
  `github.com/{owner}.png` is keyed on the *current* login so it can never
  resolve an old one, while the file still has to be saved under the old
  name the graph nodes carry. `08_download_repo_logos.py` now falls back to
  `gh api repos/{repo}`'s `owner.avatar_url`, which does follow a rename,
  and `build_web_explorer.py` ships the list of owners with no logo file so
  the renderer never requests one at all (empty today; non-empty whenever
  the cohort grows ahead of an `08` re-run, or an account is deleted rather
  than renamed). The README reader's `ERR_BLOCKED_BY_ORB` badge is *not*
  ours: rendering all 85 image URLs from eight real cohort READMEs in a
  sandboxed frame and a plain frame side by side, exactly **0** are blocked
  only by the sandbox -- the 3 failures fail both ways and are dead
  third-party services (`api.cirrus-ci.com` no longer resolves; the camo
  URL returns `502 text/plain`, which is why ORB rejects it). github.com
  renders that README with the same broken badge.
- Reactive search + a computed card for repos outside the cohort. The
  suggestion list used to know only the 7051 fetched repos, so anything else
  needed its exact `owner/repo` typed from memory; a 3+ character query now
  also hits `/search/repositories` (debounced, session-cached, and on that
  endpoint's own 10/minute pool rather than the 60/hour the rest of the page
  spends). A looked-up repo is then *computed* rather than placeholdered: its
  GitHub topics go through `scripts/16`'s exact TF-IDF circular mean for a
  real theta/r, its shared-tag edges use `scripts/13`'s >=2 rule, its
  manifests are parsed with the same direct-runtime-only rules as
  `scripts/29/31/33/36/38/41` and resolved through shipped coordinate tables,
  and its trophic height is the closed-form stationary point of
  `scripts/15`'s own objective for one new node -- so it shares the cohort's
  scale instead of a parallel invented one. Costs 2-3 requests per card.
  Measured first: shipped tables captured **100%** of the dependency edges
  that reach this cohort, with live registry lookups adding none, so the card
  spends nothing on registries. Person-based tiers stay unavailable and are
  labelled as such (they would need one call per cohort repo). Verified
  against the same card computed independently in Python: theta agrees to the
  last float digit, and the dependency and shared-tag targets match exactly.
  Ships +1.0MB of lookup tables (page 11.8 -> 12.8MB). See `NOTES.md` for the
  two wrong diagnoses of one hang that measuring frame times corrected, and
  for a misleading count in this feature's own panel that the parser check
  caught.

- Java's dependency edges were fixed at the root of the problem, literally:
  a Maven/Gradle root manifest usually lists child projects rather than
  dependencies, so reading only it left 619 of 811 Java repos with a manifest
  and no edges (185/196 zero-dependency POMs carry `<modules>`, 331/423
  zero-dependency Gradle roots carry `allprojects` -- measured, not assumed).
  `scripts/32` now sweeps each repo's whole tree for submodule manifests (one
  recursive-tree call plus batched GraphQL, where 50 aliased blobs cost 1
  rate-limit point, so a 524-module repo costs ~11 requests not 525), and
  `scripts/33` parses root and modules together. Java goes **12.3% -> 58.0%**
  of repos with a real outgoing dependency edge; **243 -> 3376** edges with
  none lost, 120 -> 579 source repos, 45 -> 223 targets, and 4551 -> 5076
  cohort repos with a trophic height. Two things the sweep's own output
  taught: 94 of 2465 module manifests are test fixtures / build logic /
  vendored trees rather than project modules, and 21% of all (repo,
  coordinate) pairs are a repo naming a module it publishes itself -- both
  now dropped. See `NOTES.md` for the silent-failure cache format caught at
  309 repos, and for the search.maven.org 403 whose blocked lookups were
  being cached as permanent "unresolved" (fixing that alone took resolved
  coordinates from 355 to 3512).

- Zoom-in performance: the worst frame during a zoom is **halved** (3585ms ->
  1727ms, interleaved A/B) and idle is ~14% cheaper. Measured first, and the
  measurement mattered twice over. The frame split showed `Scene3D.render()`
  taking 78-80% of every frame with the whole cohort materialised (386 ->
  7446 nodes, 1408 -> 18613 dependency edges as the camera dollies in), and a
  CPU profile put 70% of this file's own JS in two functions. Fixed, all
  without changing a pixel: per-frame memoised cluster centroids, reused
  bundled-path buffers, cached label metrics, material writes skipped when
  unchanged, one shared arrowhead geometry instead of ~15k identical ones,
  object pools that shrink (43,430 released on one zoom-out), LOD expansion
  gated on the viewport, and a dozen `rebuildTierEdges()` calls per frame
  batched into one. **Steady-state cost while zoomed in is unchanged** -- it
  is draw-call bound, and none of this reduces the ~47k objects in the scene.
  See `NOTES.md` for the LOD gate that flapped 153 times in 8 frames before
  being pinned to a stable position, for the machine drift that made a
  sequential A/B lie, and for the two optimisations measured and *not* kept.

## License

This project is released under the [MIT License](./LICENSE).

The upstream [SemRepo](https://semrepo.org) dataset it derives from is CC0
(data) and MIT (pipeline); see the *Data source* section above for what
remains SemRepo-derived, and *People in this dataset* for how person
identifiers are handled.
