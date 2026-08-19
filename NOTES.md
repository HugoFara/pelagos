# Design notes

## Dataset quirks worth knowing before trusting a number

- **`hasTotalStargazers` is capped at exactly 10000** for popular repos — 297
  repos in the full file hit this ceiling. It's a scrape/pagination limit, not
  a live GitHub star count, so it can't be used to rank *within* the top tier
  (it's a tie, not a ranking). `hasTotalForks` doesn't show the same round-number
  cap and has real spread (189–1099 in the top cohort) — used as the size
  encoding for repo nodes for that reason.
- `hasTotalOpenIssues` / `hasTotalContributor` / a few others are sometimes
  `null` for a given repo even in the "famous repo" cohort (e.g.
  `torvalds/linux` has no `hasTotalContributor` triple at all, `Avik-Jain/100-Days-Of-ML-Code`
  has no `hasTotalOpenIssues`). Treat missing as missing, not zero.
- `expand15_repos.txt` / `repo_expansions.json` include `compvis/stable-diffusion`,
  which is a different repo from `compvis/latent-diffusion` in the 51-repo
  `top50_repos.txt` cohort -- so 14, not 15, of the expansions actually line
  up with an aggregate node. `scripts/build_web_explorer.py` drops the
  mismatched one rather than rendering an expandable node with no aggregate
  stats.

## Two cohorts that do not overlap

`usedPackage` (95,505 triples, real repo→PyPI-package dependency edges) covers
a **different ~21,100-repo population** than the top-starred "famous repo"
cohort — checked directly, zero overlap with the current 51-repo list. The
`usedPackage` cohort looks paper/research-code linked (small, oddly-named
repos; correlates with `hasLpwcUrl`/`hasMlseaUrl`, i.e. Papers-with-Code /
MLSEA references elsewhere in the schema). So: real dependency edges exist in
this dataset, but only for a corpus of academic/research repos, not for
`pytorch/pytorch`-tier projects.

## The open question: what should the main repo-repo edge be?

Prompted by comparing against [anvaka's Map of GitHub](https://github.com/anvaka/map-of-github),
which clusters ~690k repos by shared-stargazer Jaccard similarity + Leiden
community detection. Observation: that kind of graph tends to separate repos
that are genuinely technically coupled (a library and its dependents) if their
audiences don't overlap much, because shared-stargazer similarity is really an
*audience/interest* signal, and audience correlates heavily with programming
language. Two options surfaced so far:

1. **Stay with shared-stargazers for the famous-repo cohort.** It's the only
   real relational signal available for those 51 repos (no dependency data
   covers them). Already computed: `data/processed/repo_shared_edges_pruned.json`
   (151 edges, top-4-per-node pruning of the full 1275-edge >=3-shared graph).
   Inherits the same audience/language bias as Map of GitHub — no way around
   that without different source data for this cohort.
2. **Build a second, separate graph on the `usedPackage` cohort** (the ~21k
   research repos) using real shared-dependency edges instead of
   shared-stargazers — a genuinely structural signal, not an audience proxy.
   Trades "browsing recognizable famous projects" for "package co-usage
   clusters in an academic code corpus" — different, less immediately
   legible subject matter, but actually answers the clustering critique
   instead of just inheriting it.

**Update:** added a third option that sidesteps the cohort-mismatch problem in
option 2 entirely. Contributors hang off a per-repo
`contributorreference/{owner}/{repo}/{n}` node (`hasContributorReference` from
the repo, `hasContributor` -> `person/{username}` from the reference node) --
`person/{username}` is the *same* identity space `hasStargazer` uses, so
cross-repo contributor overlap is directly computable **for the same 51-repo
cohort**, no need to switch to the disjoint `usedPackage` population. This is
a genuine technical/organizational-coupling signal (shared maintainers/
contributors), not an audience proxy. Computed via
`scripts/07_shared_contributors.sh` (a grep + subject rewrite, since the
contributor-reference URI already embeds the owning repo, no join needed)
piped into the existing generic `compute_shared_edges.py` (min 2 shared,
top-4 per node -> 59 edges over 50 of the 51 repos; `torvalds/linux` has zero
individual-level contributor data in this dump, consistent with its missing
`hasTotalContributor` aggregate noted above). Sample edges found:
`pytorch/examples` <-> `pytorch/pytorch` (44 shared), `dmlc/dgl` <-> `d2l-ai/d2l-en`
(9), `facebookresearch/faiss` <-> `pytorch/pytorch` (7) -- these read as real
maintainer/org coupling, not fandom overlap.

Resolved (for now): both as switchable, equally-real lenses on the same
explorer, alongside the real dependency edges added later (see below).
Shared-stargazer and shared-contributor are independent legend toggles with
distinct edge colors, and checking either one actually adds its pull to the
force layout rather than just drawing lines over a layout it doesn't affect
-- unchecking all three lets the cohort drift apart with only generic
repulsion/centering left. The shared-contributor edge also embeds the
actual shared usernames (small enough to ship: overlaps top out around a
few dozen, unlike shared-stargazers), shown on hover. Both started on by
default (stargazer) / off by default (contributor); once real dependency
edges shipped and became the default lens instead (see below), both of
these switched to off by default too -- they're "linked via a shared
person" proxies, not the direct structural relationship this project is
about.

**Update:** option 2 shipped too, without needing a separate explorer.
`usedPackage` triples don't point at another repo, they point at a PyPI
*package name* -- but only 186 distinct package names back all 95,505
triples, and resolving each one to the GitHub repo that publishes it
(`scripts/09_resolve_packages.py`, PyPI's JSON API, cached to
`data/raw/pypi_cache/`) turns `repo --uses--> package` into a real, directed
`repo --depends on--> repo` edge. A lot of those resolved repos are already
famous enough to be in the 51 (`torch` -> `pytorch/pytorch`, `sentencepiece`
-> `google/sentencepiece`), and the rest (`transformers`, `spacy`, `jax`,
`fastapi`, `optuna`, ...) are genuinely well-known libraries worth being
cohort members in their own right -- `scripts/10_fetch_new_repo_stats.py`
adds them (168 new "library" nodes), plus a curated top-100 slice of the
dependency-cohort repos themselves, ranked by distinct-resolved-package
count (a real "how plugged into this ecosystem is it" signal) since the
other ~21,000 have zero aggregate stats in the dump and adding all of them
would both need thousands of live GitHub calls and wreck the graph's
legibility. `scripts/11_dependency_edges.py` builds the edges; unlike the
other two tiers, every edge here has weight 1 (each package resolves to
exactly one specific repo -- `torch` and `torchvision` are different repos),
so the usual weight-based top-4 pruning doesn't apply -- pruned instead by
degree (target in-degree / source out-degree), which is still a real,
derived number, never guessed.

**Data quirk found along the way:** dependency-cohort repo ids
(`data/processed/dependency_cohort_repos.txt`) are inconsistently ordered.
`ntparse.py` assumes `repository/owner/name` and the 51-cohort respects
that, but dependency-cohort ids are sometimes backwards -- e.g. the id
`kvquant/squeezeailab` 404s on GitHub, `SqueezeAILab/KVQuant` (reversed) is
the real repo. Resolving every source-repo id by trying both orderings
against the GitHub API (and keeping whichever exists) also caught cases
where a "new" source repo was actually already one of the 51 under its
backwards raw id -- `dgl/dmlc`, `colossalai/hpcaitech`, `h2ogpt/h2oai`, and
`intellij-community/jetbrains` are really `dmlc/dgl`, `hpcaitech/ColossalAI`,
`h2oai/h2ogpt`, and `JetBrains/intellij-community`, all already in the
cohort -- so those four picked up real outgoing dependency edges (e.g.
`dmlc/dgl` -> `RDFLib/rdflib`) instead of spawning duplicate nodes.

## A fourth edge type: shared tags (semantic, crude version)

The first three repo-repo edges (stargazer, contributor, dependency) are all
either audience/organizational proxies or a real-but-narrow structural
signal (imports). None of them capture "these two repos are *about* the
same thing" independent of who uses them or what they import. GitHub's
per-repo topic tags are a cheap, already-available proxy for that: two
repos both tagged `diffusion-models` and `pytorch` are related on subject
matter even if they share no contributors and no dependency edge.

Two possible sources for tags, checked directly:

- The SemRepo dump has a matching `foaf:topic` predicate (272,328 triples
  total) — but it only covers **55 of the 319** cohort repos (checked by
  grepping the dump for the cohort's repo URIs and filtering to that
  predicate).
- The GitHub API's `topics` field, already present in every cached
  `data/raw/github_cache/{owner}__{repo}.json` response from scripts 10-12
  (fetched for other reasons — new-repo stats and description caching) —
  covers **184/319**, with zero new network calls needed.

Used the second source. `scripts/13_semantic_edges.py` builds a repo->set(tags)
bipartite map from the cache files and reuses `compute_shared_edges.py`'s
generic `build_edges()` core (factored out of that script for this — it
was already parsing a different bipartite shape from N-triples for the
stargazer/contributor tiers, so the actual overlap+top-K-pruning logic is
now shared instead of duplicated a third time). Min 2 shared tags, top-4
per node, same thresholds as the contributor tier: 787 edges before
pruning -> 348 after, touching 134 of the 184 tagged repos. Spot-checked
the top edges by weight and they read as real: `huggingface/pytorch-image-models`
<-> `rwightman/pytorch-image-models` (20 shared tags — actually the same
project, ownership transferred), `catboost/catboost` <-> `lightgbm-org/LightGBM`
(9, both gradient-boosting libraries), `explosion/spaCy` <-> `piskvorky/gensim`
(6, both NLP/Python libraries).

**Explicitly the crude version.** This is literal tag-string overlap, not
semantic similarity — two repos about the same thing using different
vocabulary (or one undocumented/untagged repo, 135 of 319 have zero
topics) show no edge at all. A real next step: NLP/embedding-based
similarity over repo descriptions (already cached, see the dependency-edge
section above) or README content (`hasReadMeContent`, 194,737 triples in
the dump) — cosine similarity over embeddings would catch "same subject,
different words" in a way tag matching structurally cannot. Left as future
work; this phase ships the cheap version first since it needed zero new
data collection.

## Level-of-detail clustering: bounding render/simulation cost as the cohort grows

Growing the cohort past 319 repos hits two real limits at once: the graph
becomes visually unreadable, and `tick()`'s repulsion force is an all-pairs
O(n²) loop with no spatial index (no quadtree/Barnes-Hut anywhere in the
file) — fine at 319 nodes (~50k pair-checks/frame), not fine at a few
thousand. Both are fixed by the same mechanism: never materialize more nodes
than are currently relevant. `scripts/14_cluster_hierarchy.py` precomputes a
multi-level cluster hierarchy; the frontend renders/simulates only the
top-level cluster meta-nodes by default, lazily expanding one into its real
children on click or zoom-in (`expandCluster`/`collapseCluster`, generalized
from the tier-2 click-to-expand machinery already in the file, but with
*replace* rather than *coexist* semantics — expanding a cluster removes its
meta-node and materializes its children instead of adding them alongside).

**Fix: singleton clusters aren't interesting clusters.** Reported after
Phase 12 shipped -- the top-level view showed a lot of clusters that,
clicked, expanded into exactly one repo. Louvain naturally produces these
at every level (a hub repo, or anything without a good community fit, just
stays alone), and this cohort had a lot of them: 82 of 134 clusters,
24 of the 28 *top-level* ones -- meaning 24 of the 28 things the app
showed you before you'd clicked anything were a pointless click-through to
a single repo, not a real cluster. `collapse_singletons()` in
`scripts/14_cluster_hierarchy.py` now removes every memberCount == 1
cluster after the hierarchy is built, promoting each one's single
descendant repo (walking through however many levels of
singleton-wrapping-a-singleton it takes -- a high-degree hub can fail to
merge with anything at *every* level) into whichever real cluster or
top-level slot referenced it. Ships as a new `topLevelIds` field (mixed
cluster ids and, now, real repo ids for anything that's a singleton all
the way up) that the frontend's `CLUSTER_ROOTS` reads directly instead of
deriving `parent === null` clusters itself. Net effect on this cohort:
28 top-level entities either way, but now 4 real clusters (46, 62, 73, 123
members) plus 24 real repos shown directly, instead of 4 real clusters
plus 24 pointless singleton wrappers. `repoParentCluster` (the map
`revealRepo`/search/six-degrees walk to find a repo's nearest
materialized stand-in) had to stop assuming a repo's immediate cluster
parent is always level 1 -- after collapsing, it can be any level, since a
repo that fails to merge with anything until level 3 now attaches directly
there. The "color by cluster" machinery (`clusterColorOf`,
`renderClustersPanel`) had the same level-1 assumption baked in and needed
the same generalization -- both now derive their id list from
`repoParentCluster`'s actual values rather than hardcoding `level === 1`.

**Clustering algorithm, hand-rolled rather than a new dependency.** This
repo has zero tracked Python dependencies (no requirements.txt at all,
every script stdlib-only except Pillow). The runtime JS already had a
single-level greedy-modularity Louvain implementation (`louvainCommunities`,
used only for a cosmetic "color by cluster" toggle, explicitly commented as
"no multi-level coarsening -- plenty for a 51-node graph"). Ported it to
Python and added the missing second phase: coarsen the graph (communities
become super-nodes, inter-community edges summed), rerun local-moving on
the coarsened graph, repeat until the top level drops under a ~40-cluster
legibility budget or no further merge is possible. The runtime JS clusterer
was then retired entirely (~130 lines) in favor of reading the same
precomputed hierarchy — one source of truth for "cluster" instead of two
that could disagree.

**Resolution-limit problem, found by actually running it.** Clustering the
union of all four repo-repo edge tiers (normalized per-tier, since
stargazer weights run into the thousands and semantic-tag weights top out
around 20) with plain modularity (resolution=1) produced 31 top-level
clusters, but 4 of them were 56-101 members each -- 283 of 319 repos in
four giant blobs, because the shared-stargazer tier alone (1275 edges) is
dense enough to swamp the more specific tiers. That's the well-known
modularity resolution limit, not a bug. Fix: raise the resolution multiplier
(Reichardt-Bornholdt style, `gain = w - resolution * expected`) to 4 for the
*first* pass only (raw repos -> fine clusters), then back to 1 for later
coarsening passes -- a uniformly elevated resolution across every pass was
tried first and barely merged anything level over level (106 -> 100 -> 97
clusters, never reaching budget within the level cap), because the
coarsened graph is already much sparser and doesn't have the same
resolution-limit issue plain modularity does on it. End result: 106
level-1 clusters (max 46 members) -> 28 level-2 top-level clusters (max 38
direct children), a real progressive drill-down instead of a few
all-or-nothing blobs. Spot-checked: the famous-repo cohort (pytorch,
stable-diffusion, langchain, ...) lands together as one level-1 cluster
under `dmlc/dgl`'s hub, distinct from the narrower academic
dependency-cohort clusters.

**A bug the static verification alone wouldn't have caught.** Because
`transform.scale` never changes during a tick(), the LOD auto-expand/
collapse check was first wired to run periodically from inside `tick()`
(throttled). This turned out to actively break `revealRepo` (the function
that expands whatever ancestor clusters block a specific repo, used by
search/pathfinding/compare/permalink): `expandCluster`/`collapseCluster`
each run their own 30-60 tick synchronous pre-settle burst, and the
throttled check firing *inside that same burst* would re-evaluate a stale
scale and immediately collapse the very cluster the burst had just
expanded -- permanently stalling any reveal that needed to descend more
than one level. Found via a headless Node+jsdom harness (see below) that
actually drove `expandCluster`/`revealRepo`/`computePath`/`computeCompare`
end to end and asserted every edge endpoint resolves to a live node --
static syntax/placeholder checks alone would not have caught this, since
nothing about it is a syntax error or a missing reference. Fixed by moving
the check to fire only at the actual points `transform.scale` changes
(wheel zoom, `fitView`, `focusNode`) instead of on a tick counter, and by
*not* calling it from `fitToNodeIds` specifically -- that function computes
a tight fit around an already-deliberately-revealed node set, and if those
nodes are far apart in world-space the resulting scale can be very small,
which would otherwise trigger a mass auto-collapse right back over the
nodes it was just asked to frame.

**Verification without a browser.** Per standing preference, no
Playwright/interactive browser use. Beyond the usual static checks (`node
--check` on the extracted script, zero unreplaced `__PLACEHOLDER__` tokens),
this phase also used a temporary headless Node+jsdom harness (not
committed -- scratch-only) that loads the *built* `web/index.html`, stubs
just enough of the Canvas 2D API to let the app's real init code run to
completion, and then calls the app's own `expandCluster`/`collapseCluster`/
`revealRepo`/`computePath`/`computeCompare`/`checkLodTransitions` through a
debug hook to assert real invariants (every edge endpoint is a currently
materialized node, expand/collapse round-trips back to the same node count,
a found path's every hop actually gets revealed). This is what caught the
`tick()`-driven auto-collapse bug above -- a case where "does it parse" and
"are all placeholders substituted" both say yes but the actual interaction
sequence is broken.

**Not built, deliberately (crude-first, same pattern as the semantic-edge
phase).** No explicit "auto-collapse least-relevant cluster" eviction
budget -- zoom-threshold auto-expand is self-limiting already, since only
clusters actually crossing the on-screen legibility threshold at the
current zoom expand, which is inherently bounded by available screen
space. No recursive splitting of an oversized *single* cluster beyond what
the coarsening pass already produces -- the current data tops out at 46
members for the biggest level-1 cluster, comparable to this project's
original 51-repo cohort, so a single expand click never dumps more nodes
than the app already handled comfortably before this phase. Both are
plausible follow-ups if a much larger future cohort makes either
insufficient.

## Prior art surveyed

RDF/Linked-Data-specific: rdf:SynopsViz (hierarchical multilevel aggregation —
closest match to the LOD approach used here), graphVizdb (spatial-index-backed
viewport rendering for very large RDF graphs), Lodlive/Fenfire (click-a-node-
to-expand-its-neighbors interaction, used as the model for `web/index.html`'s
click-to-expand), ZoomRDF, RelFinder, LODeX.

GitHub-ecosystem-specific: Map of GitHub (see above), StarMapper (per-repo
contributor/stargazer geography), Daily Stars Explorer (star/fork/issue
history over time), star-history.com (star-growth comparison). None of these
operate on this specific SemRepo dump — they all pull their own live
GitHub/GH-Archive data.

## Coordinate system v2: trophic height + circular topic embedding (Phase 12)

First tracked Python dependency: `numpy`. Two things need real linear
algebra -- solving the (singular) trophic-level system and the topic
co-occurrence graph's spectral embedding -- and this repo's prior zero-
dependency stance (see the LOD-phase note above on hand-rolling Louvain
instead of taking `python-louvain`/`networkx`) was a judgment call about
whether a given feature was worth a dependency, not a hard rule. Handing a
Laplacian solve to `numpy.linalg.lstsq` instead of hand-rolling Gaussian
elimination was that judgment call going the other way this time.

**Trophic level sign convention, derived and checked, not assumed.**
`scripts/15_trophic_levels.py` minimizes, over every dependency edge `a ->
b` ("a depends on b"), `(h_a - h_b - 1)^2` -- wanting every consumer one
level above what it depends on. Taking the gradient and setting it to zero
gives `Λh = v` with `v_k = outdeg(k) - indeg(k)`, derived directly from
this objective rather than copied from a food-web paper's convention:
ecological trophic-level papers point edges the *other* way (resource to
consumer, e.g. prey to predator), so blindly reusing their v-formula with
this dataset's consumer-to-resource edge direction would have silently
flipped the y-axis. The script still self-checks empirically (mean of
`h[a] - h[b]` over real edges, flip globally if negative) rather than
trusting the derivation alone -- belt and suspenders, and it came back
positive on the first run, matching the derivation.

**Real finding: this cohort's dependency graph is exactly bipartite.**
Checked directly: 0 of the 244 repos that touch a dependency edge are
*both* a source and a target anywhere in `repo_dependency_edges.json` --
every edge is "top-100 dependency-cohort consumer -> resolved library
repo", never a chain. Trophic incoherence (the MacKay/Johnson/Sansom
free readability metric this same solve produces) comes out at exactly
0.000 as a direct consequence -- a perfectly layered graph, but only
because there's currently nothing *to* misalign: sources collapse to one
raw height, targets to another. The y-axis is real and correctly signed,
but today it's closer to a binary consumer/library split than the rich
continuous gradient the design is for. Resolving the library repos' own
transitive dependencies too (not just the dependency cohort's) would be
the natural way to earn real multi-level stratification -- not scheduled,
noted here so a flat-looking y-axis doesn't get mistaken for a bug.

**Circular topic embedding, verified connected.** The PMI-weighted topic
co-occurrence graph (min 2 supporting repos per topic *and* per pair,
positive PMI only) comes out as a single connected component over this
cohort -- 175 of 908 distinct topics, 545 edges -- checked directly before
trusting a 2D spectral embedding of it to mean anything (a fragmented
graph would need per-component handling the current script doesn't do).
Eigenvalues of the graph Laplacian: `[0, 0.013, 0.117, 0.118]` -- a real
gap after the trivial zero, so the 2D embedding (eigenvectors 2 and 3) is
meaningful, not noise. 169/319 cohort repos end up with a real theta;
R (topic coherence) spreads 0.211-1.000, mean 0.917 -- unsurprisingly high
since most tagged repos only carry 1-2 topics, which forces R close to 1
by construction.

**Tier-edge forces needed to stop fighting the trophic axis.** The first
pass (target-force pull toward (y, theta, r), full-strength stargazer/
contributor/dependency/semantic springs left untouched) produced far
weaker visual stratification than the numbers implied it should -- a
dependency edge's spring wants short euclidean distance regardless of
direction, and at full strength it was pulling a consumer down toward its
library's y-band about as hard as the trophic force pulled it up,
partially washing the constraint back out. Fixed by damping the *vertical*
component of every repo-repo tier-edge force to 12% of normal
(`TIER_EDGE_Y_DAMPING` in `web/template.html`'s `tick()`) while leaving the
horizontal component at full strength -- matches the design's own framing
directly: the hard constraints are y and theta, edges should only resolve
local (i.e. horizontal/angular) jitter within that, not fight it. Measured
effect: `pytorch/pytorch` (consumer) vs. `pytorch/vision` (dependency, via
the `dmlc/dgl` bridge) y-separation went from ~427 world-px to ~635 after
damping, at the same 0.006 target-force stiffness. Not exhaustively tuned
past that one pass -- `TROPHIC_Y_RANGE`, `TOPIC_R_SCALE`, the 0.006 target
stiffness, and `TIER_EDGE_Y_DAMPING` are all real, named constants in
`web/template.html` if a future pass wants to push further.

**Co-star/contributor: force always on, drawn only in focus.** Both tiers
used to be a legend checkbox that gated *both* their tick() attraction and
whether they were drawn; Phase 12 splits that in two. `edgeVisible.
stargazer`/`.contributor` are now permanently `true` (so their gentle
attraction force is unconditional, matching "linked via a shared person"
still being a real if soft signal) and excluded from the permalink hash
entirely, while `draw()` only ever renders one when it touches the
currently hovered or selected node (`edgeInFocus()`) -- selecting
`pytorch/pytorch` and re-drawing surfaces a legible burst of its real
stargazer/contributor neighbors (`torvalds/linux`, `tensorflow/magenta`,
`ray-project/ray`, `jetbrains/intellij-community`, ...) with everything
else dimmed, verified via a real Chromium screenshot -- see the verify-
without-a-browser note above for why that's a deliberate one-off, not the
new default.

## Cluster the similarity substrate, not dependency (Phase 13)

**Second tracked Python dependency: `leidenalg` + `python-igraph`.**
Louvain has a documented bug (Traag, Waltman & van Eck, "From Louvain to
Leiden", 2019) where its local-moving phase can leave a community
internally disconnected -- a correctness issue, not a style nit, so it
overrides the LOD-phase note above about hand-rolling Louvain rather than
taking a dependency. Leiden's refinement step guarantees every community
it produces is connected. `leiden_communities()` in
`scripts/14_cluster_hierarchy.py` swaps in for `louvain_communities()`
with the exact same `(ids, weighted_edges, resolution) -> {id: label}`
contract, so Phase 10's coarsen-and-repeat scaffold (`build_hierarchy`)
needed no other changes -- this is "recursive Leiden", one of the two
hierarchy strategies the Phase 13 design named as options.

**Dependency edges pulled out of the clustering substrate entirely,**
confirming the review's stated concern was real: clustering the old
four-tier union (dependency included) converged on communities that
mostly separated ecosystems, not themes. Dependency still drives the
y-axis (Phase 12) and still renders as meta-edges between collapsed
cluster nodes -- the frontend's `buildTierEdges`/`materializedAncestorOf`
aggregation already worked from raw per-tier edges mapped onto whatever's
currently materialized, completely independent of anything this script
computes, so "meta-edges = summed crossing dependencies" from the Phase
13 design needed zero frontend changes to already be true.

**Co-star and topic PMI, not raw overlap counts.** Both computed with the
same generic positive-PMI formula (`pmi_edges()`), just with documents and
items swapped between the two calls: co-star PMI treats each *stargazer*
as the document and repos as items (mirrors `compute_shared_edges.py`'s
bipartite, but PMI-weighted instead of raw intersection size); topic PMI
treats each *topic tag* as the document and repos as items -- the exact
transpose of `scripts/16_topic_circular_embedding.py`'s topic-topic PMI,
which treats each *repo* as the document and topics as items. Both
normalized (min-max) and summed into one similarity graph before
clustering, same reasoning as the old four-tier union: PMI values on
different natural scales shouldn't let one signal drown out the other.

**Real finding: the raw co-star bipartite only covers the original
top-50 cohort.** `data/raw/repo_stargazers_full.nt` has hasStargazer
triples for exactly 51 repos (checked directly) -- uncapped shared-
stargazer overlap was only ever computed for the original cohort, not the
268 repos added later via dependency expansion (see "Two cohorts that do
not overlap" above). Combined with topic tags only covering 184/319 repos
(150 of the 268 non-top-50 repos, 134 of which actually clear the PMI
min-support thresholds), **166 of 319 cohort repos end up with zero
co-star and zero topic signal** -- genuinely nothing to cluster them by,
not a bug or an overly strict threshold. They become singleton Leiden
communities and, via the existing `collapse_singletons()` pass, show up
as real repos directly at the top level rather than a guessed grouping --
same "not an interesting cluster, show the repo itself" principle the
LOD-phase singleton fix already established, just triggered by data
sparsity instead of hub-repo degree this time. Net effect: 178 top-level
entities (12 real clusters + 166 repos shown directly) versus the old
four-tier union's 28 -- a real, honest regression in *how much* the LOD
layer declutters the view, worth knowing about rather than hiding by
quietly loosening a PMI support threshold or smuggling dependency back
into the substrate. Every one of Phases 7-9 already rendered up to 319
fully materialized nodes at once with no reported performance problem, so
178 isn't a performance concern, just a smaller decluttering win than
before.

**A stopping-criterion bug that only this data exposed.** The coarsening
loop's original stop condition (`len(group_items) <= TOP_LEVEL_BUDGET`)
implicitly assumed nearly the whole cohort participates in some cluster --
true of the old dependency-inclusive graph, false now that ~166 repos are
structurally isolated. Isolated super-nodes can never merge into anything
at any resolution (zero edge weight to any neighbor), so total group
count could never drop under the 40-cluster budget by further coarsening
regardless of how many levels ran -- the loop kept "coarsening" a
perfectly good level-1 partition (10-12 real clusters, already well under
budget) into an ever-larger, less meaningful blob for no legibility gain,
purely because it kept counting the irreducible singleton population
against a budget that could never include them. Fixed by counting only
multi-member clusters against `TOP_LEVEL_BUDGET`; singletons still end up
in the final render via `collapse_singletons()` regardless of how many
coarsening levels ran. With the fix, this cohort now always stops after
level 1.

**Resolution tuning, level 1 only in practice.** Tried 1.0/1.2/1.4/1.6/
2.0/3.0 (same search pattern as the old FIRST_PASS_RESOLUTION tuning).
Below 2.0, one deep-learning/generative-AI-flavored cluster alone held
40+ of the ~185 repos with any signal at all -- a smaller version of the
same resolution-limit problem the old four-tier union hit at resolution=1
(one dense signal swamping the rest). At 2.0 the clusters read as
genuinely distinct themes, checked directly by hand:
- `arrow-py/arrow` (25): general-purpose Python libraries (rich, psutil,
  sqlalchemy, pillow, pygame, polars, orjson, ...)
- `mlc-ai/web-llm` (24): generative AI / LLM chat (chattts, langchain,
  chatglm-6b, minigpt-4, controlnet, mamba, ...)
- `encode/httpx` (20): web frameworks / async networking (fastapi,
  django, aiohttp, requests, scrapy, twisted, trio, ...)
- `scikit-learn/scikit-learn` (17): classical ML / data science
  (xgboost, pandas, lightgbm, gradio, streamlit, ...)
- `nvidia/deeplearningexamples` (16): computer vision / deep learning
  (stylegan2, insightface, ultralytics, swin-transformer, ...)
- `allenai/allennlp` (14): NLP / transformers (fairseq, onnx,
  huggingface/transformers, fastai, ...)
- `pytorch/pytorch` (13): core PyTorch ecosystem (vision, examples,
  faiss, ray, sentencepiece, ...)
- `ipython/ipython` (12): notebook/interactive tooling (dask, scipy,
  jupyter/notebook, plotly, tqdm, ...)
- `pallets/flask` (6): Pallets-adjacent web micro-libraries.

Not one of these is an ecosystem/package-manager boundary -- exactly the
outcome the review's critique of the old substrate predicted would be
possible once dependency stopped dominating. Above 2.0, clusters kept
splitting into smaller pieces without reading as more coherent, so 2.0 is
what shipped.

**Mutual-kNN sparsification, checked it isn't a no-op.** `k=20` (middle
of the review's suggested 15-30 range) cuts the combined PMI graph from
1543 to 757 edges on this cohort -- real pruning, not vacuous, since
average degree among touched repos (~9) sits comfortably under `k`, so
sparsification is only removing genuinely weak tail edges from the
higher-degree nodes rather than truncating everyone uniformly.

**Cluster radius: log(size), not sqrt(normalized size).** `clusterRadius`
in `web/template.html` used to min-max normalize member count then take
its square root; Phase 13's clusters span 2-25 members (versus the old
substrate's 2-123), a much wider relative range where sqrt-of-normalized
compresses small real clusters into looking nearly identical. Switched to
linear-in-log-of-size instead, same `[14, 40]` visual radius range.

**Not addressed here, deliberately.** Leiden is not deterministic run to
run (no fixed seed in the shipped script beyond what's used for local
testing) -- unlike the old hand-rolled Louvain, re-running this pipeline
on refreshed data will reshuffle cluster ids/membership. That's real and
already flagged as Phase 15's problem (cross-snapshot stability via
Jaccard + Hungarian matching), not something to solve here.
`CLUSTER_EDGES_BY_LEVEL`/`edgesByLevel` in the output JSON remains
unused by the frontend, same as before this phase -- not touched, since
removing genuinely-dead code wasn't part of what was asked.

## Co-star/contributor data backfill: the top-50 scoping was a bug, not a data limit

Immediately after Phase 13 shipped, re-examined the "raw co-star bipartite
only covers the original top-50 cohort" finding above and it didn't hold
up as a *data* limitation -- it was a *query* limitation.
`scripts/05_shared_stargazers.sh`/`07_shared_contributors.sh` had only
ever been invoked with `data/repo-lists/top50_repos.txt` as the repo list
(chronologically unavoidable at the time: the dependency-expansion cohort
in `dependency_extra_repos.txt` didn't exist yet when those two scripts
first ran, back before Phase 7). Nothing about the underlying SemRepo dump
actually restricts hasStargazer/hasContributor coverage to those 51 repos
-- re-running both scripts against the full 319-repo cohort (`cat
top50_repos.txt dependency_extra_repos.txt | sort -u`) against the local
12.8 GB dump took ~17s and ~13s respectively (`grep -a -F -f` over 319
patterns, not slow) and recovered real triples for 37 more repos
(stargazer: 51 -> 88) and a comparable jump for contributors (-> 89).
Re-running `compute_shared_edges.py` on the enriched raw files and then
`scripts/14_cluster_hierarchy.py` shrank the zero-signal population from
166 to 155 repos and grew the real-cluster count from 12 to 14 (still
resolution=2.0 -- re-checked with a fresh 1.0-3.0 sweep on the enriched
graph, 2.0 was still the point of no dominant blob). One cluster that
didn't separate out before now does cleanly: `catboost/catboost`,
`dmlc/xgboost`, `lightgbm-org/LightGBM`, `facebook/prophet` -- the
gradient-boosting sub-theme was previously absorbed into the larger
classic-ML cluster for lack of enough co-star signal to pull it apart.

**This also broke Phase 11's two seeded path-finder examples**, discovered
by re-checking them after the backfill out of general caution (any time
underlying edge data changes, claims that were verified against the old
data need re-verification, not just carried forward). Both had been
specifically chosen because `shortestPath()` found *no* direct edge
between the pair, only a 2-hop bridge -- e.g. `pytorch/pytorch` and
`pytorch/vision`, picked because the top-50-scoped stargazer data never
had a chance to see a direct edge between them (`pytorch/vision` isn't in
top50). Once the full cohort was actually queried, both pairs turned out
to have a very real, very large direct shared-stargazer edge
(`pytorch/pytorch` <-> `pytorch/vision`: 1052; `google/sentencepiece` <->
`huggingface/transformers`: 956) -- unsurprising in hindsight (same org;
an extremely common real-world dependency), but exactly backwards from
what the example was supposed to show. Re-ran the same offline
`shortestPath()`-equivalent BFS used to find the originals, this time
requiring **both endpoints and the bridge node** to be reasonably
well-known (top-60-by-stargazers within the cohort) to avoid picking an
obscure academic repo as the bridge, and confirmed zero direct edge across
all four tiers (stargazer/contributor/dependency/semantic, checked
explicitly, not just the three the original write-up mentioned) for the
replacements: `dmlc/xgboost` <-> `psf/requests` (723 shared stargazers
with `dmlc/dgl`, which depends on `requests`) and `Kludex/starlette` <->
`NVIDIA-NeMo/Speech` (`PaddlePaddle/PaddleSpeech` depends on `starlette`
and shares 4 topic tags -- `asr`, `speech-synthesis`,
`speech-translation`, `tts` -- with NeMo). Updated in `web/template.html`,
`README.md`, and `ROADMAP.md`.

**Lesson worth naming directly:** a "real finding" documented from a
partial/scoped data pull is only as trustworthy as the scoping was
deliberate. The top-50 restriction here wasn't a considered decision about
what data to use, it was a leftover from pipeline ordering -- worth
double-checking *why* a coverage gap exists before writing it up as a
structural fact about the dataset, not just confirming that the gap
exists.

## Real similarity signal via text embeddings (Phase 14)

**Third tracked Python dependency: `fastembed`.** Needs a real embedding
model, not just linear algebra (numpy) or graph partitioning (leidenalg/
igraph). `sentence-transformers` was the obvious first candidate but pulls
in PyTorch -- multiple GB, GPU-oriented, way past "smallest workable
dependency" for embedding a few hundred short blurbs. `fastembed` runs the
same model family through ONNX Runtime instead (no torch, ~20MB wheel);
`BAAI/bge-small-en-v1.5` (384-dim, one-time ~130MB model download, cached
locally after) was picked as the smallest model in the family the review
itself named (bge-small/e5-small). Embedding 317 repos after the model's
loaded takes under a second -- the entire cost here is the one-time model
download, not runtime.

**README fetch, real coverage.** `scripts/17_fetch_readmes.py` -- 319/319
cohort repos queried via authenticated `gh api repos/{owner}/{repo}/readme`
(base64-decoded, cached raw to `data/raw/readme_cache/`), 318 have one.
Authenticated GitHub API (5000 req/hour) makes this a non-issue compared to
the unauthenticated 60/hour cap the live frontend has to work around --
319 sequential requests took under 3 minutes.

**Cleaning found two real failure modes, not hypothetical ones.** First
pass of `clean_first_paragraph()` (strip code blocks/HTML/badges/headers,
take the first paragraph with >= 8 words) produced garbage on real
samples: a language-switcher line ("English | 简体中文 | 日本語 | ...", common
on repos with translated READMEs) and a ReStructuredText image directive
(`.. image:: :height: 64px ...` -- `aio-libs/aiohttp`'s README is `.rst`,
not `.md`, so the markdown-shaped badge/image regexes never touched it).
Fixed with an ASCII-word-ratio filter (reject a paragraph if < 70% of its
words are ASCII, catches language switchers and other non-prose Unicode
lines) and an explicit RST-directive line filter (`^\s*\.\.\s`). Checked
against a random sample of 15 real cached READMEs after the fix -- 13 of
15 produced genuinely usable prose (the other two: one repo has no real
second paragraph at all before hitting code/citation blocks, one leads
with a "check out our new paper" notice instead of a description -- both
honest misses, not cleaner bugs).

**A real noise problem, found by actually clustering the result --
exactly what "coverage, not noise" warns against.** First full pass (cosine
>= 0.7, no further sparsification) shrank the "no clustering signal"
population from 166 to 50 -- a great number on its own -- but the largest
cluster ballooned to 62 members and stayed above 50 even at Leiden
resolution 3.0+. Inspecting it directly: ~50 small, completely unrelated
research-code repos (poetry generation, speaker anti-spoofing, wireless
communication protocols, knowledge distillation, LoRA variants, ...)
welted into one blob purely because their descriptions/READMEs open with
near-identical academic boilerplate ("Code for the paper...", "This
repository contains our implementation of...", "Official code of X for
paper Y") -- `bge-small` (384-dim, general-purpose) keys on that shared
phrasing more than on the actual subject-matter words next to it, at the
short text lengths these blurbs run.

Two fixes, in order of how much they actually helped:
1. **Boilerplate stripping** (`strip_paper_boilerplate()` in
   `scripts/18_text_embeddings.py`): ~9 regexes matching the common
   "official code / this repo contains / code for the paper" openers,
   applied to both description and README paragraph, not anchored to
   start-of-string (a setext-style README heading glued directly to the
   next sentence with no blank line -- `Title\n===\nThis repository...`
   -- merges into one "paragraph" by `clean_first_paragraph`'s line-
   joining logic, so the boilerplate clause can land mid-string).
   Improved things (median within-blob cosine 0.683 -> 0.672) but didn't
   fix it -- lowering the *fusion weight* on the whole text tier (tried
   0.3/0.5/0.7 of co-star/topic's weight) didn't fix it either, because
   for a repo with **no other signal at all** (which describes most of
   this specific population -- that's exactly why they were part of the
   155-repo gap in the first place), whatever weight text gets is 100% of
   what determines its cluster regardless of the number.
2. **Text-tier-specific mutual-kNN, applied before fusion, not just
   after** (`TEXT_MUTUAL_KNN_K = 4` in `scripts/14_cluster_hierarchy.py`,
   distinct from the `MUTUAL_KNN_K = 20` that sparsifies the *combined*
   graph). This is what actually worked: capping each repo to its 4
   strongest text matches converts text-embedding from "a dense
   similarity graph everyone in a broad subject area weakly matches"
   into "a sparse nearest-neighbor hint", the same role co-star/topic PMI
   already play by construction (both naturally sparse -- PMI requires
   real co-occurrence, not just topical adjacency). Re-clustering with
   this in place: the mega-cluster split into several genuinely coherent
   research sub-themes instead -- checked directly, e.g. one 7-member
   cluster hubbed on `HillZhang1999/ICD` is specifically
   hallucination/decoding-in-LLMs research (`SALT-NLP/Structure-Aware-
   BART`, `hkust-nlp/Activation_Decoding`, `shikiw/OPERA`, ...), a real
   subfield distinction the old blob had erased.

Final numbers with both fixes: 20 real clusters (up from Phase 13's 14),
84 repos with no clustering signal shown directly (down from 166), max
cluster size 27 (down from a 62-member blob at the naive first pass). Not
zero noise -- `MIN_TEXT_COSINE = 0.7` and `TEXT_MUTUAL_KNN_K = 4` are both
picked empirically, not derived, and a boilerplate variant not covered by
the 9 regexes (`"Codes for reproducing the numerical results reported
in:"`) still leaks through uncaught on at least one repo -- but a
qualitatively different, much better result than the naive first pass,
found by actually running the clusterer and reading real cluster
membership rather than trusting a coverage number alone.

## The contrarian-claim test: co-star-driven vs. topic-driven theta (Phase 14)

The review's second-answer claim, held in reserve since Phase 12: co-star
(shared-stargazer) similarity might drive a *better* theta axis than topic
tags do, since audience overlap is arguably the real signal behind why
Anvaka's Map of GitHub reads well, and topic tags are a self-reported,
patchy signal (155+/319 repos have none at all -- see Phase 13's section
above). Testing this needed three things that didn't exist yet: a co-star-
driven theta to compare against, a concrete metric for "better" (not a
vibe check), and ground truth to measure against.

**`scripts/19_costar_circular_embedding.py`**: spectral-embeds the co-star
PMI repo-repo graph directly (`scripts/14_cluster_hierarchy.py`'s
`build_costar_pmi_edges`) -- no aggregation step needed here, unlike the
topic version, since co-star PMI is already repo-to-repo, not repo-to-
topic. Verified single connected component first (79/79 touched repos,
checked directly) before trusting the embedding, same discipline Phase 12
used for the topic graph. `theta = atan2` of the two non-trivial
eigenvectors; `r` = the point's own distance from the origin, normalized
-- not a circular-mean resultant length like the topic version (there's
nothing being averaged; each repo gets exactly one embedding point here).

**`scripts/20_compare_theta_sources.py`**: the actual metric.
Within-cluster circular concentration (resultant length R of member
thetas) against Phase 14's real fused-similarity Leiden clusters as ground
truth -- the best available stand-in for "these repos are genuinely
related" this project has. Two comparisons run: full-coverage (each
source scored on whatever repos it actually reaches) and same-subset (the
53 repos both sources cover, isolating precision from coverage).

**Result: the review's intuition was right, but not actionable today.**
Same-subset weighted-mean R: co-star-driven 0.9585 vs. topic-driven
0.9278 -- co-star *is* the more precise signal where it has data, echoing
Anvaka's audience-similarity intuition. But co-star-driven theta only
reaches 79/319 repos (the raw stargazer data's top-50 origin, even after
the Phase 13-adjacent backfill -- see that section) against topic-
driven's 169/319. Switching the shipped theta axis to co-star would more
than halve real theta coverage in exchange for a ~3-point R improvement on
the repos it still reached -- a bad trade specifically given this phase's
own "coverage, not noise" framing. **Decision: topic-driven theta stays
the shipped axis** (`repo_topic_circular.json`, unchanged from Phase 12).
`data/processed/repo_costar_circular.json` is real, computed, and kept
(not wired into the frontend -- nothing consumes it) as the honest record
of a test that was run and answered, not one that was skipped. Revisit if
the co-star raw data ever gets real coverage past top-50 (see Phase 13's
backfill section for why that's a real, re-runnable gap, not a hard
limit) -- at full-cohort co-star coverage this trade would likely flip.

## Cluster id stability and real labels (Phase 15)

Two independent problems that both only became real once Phase 13 shipped
Leiden: cluster ids reshuffle on any data refresh that changes Leiden's
internal community ordering, and cluster labels were never anything but
the highest-degree member's own name.

**Id stability (`scripts/21_stabilize_cluster_ids.py`).** `scripts/
14_cluster_hierarchy.py` mints `cluster/{level}/{idx}` positionally, where
`idx` is a rank in that run's `group_items` sort. Leiden itself is seeded
(`seed=1`), so re-running on *literally unchanged* data reproduces the
same ids -- but the whole point of this pipeline is to be re-run on
refreshed data (new stars, a repo's topics changed, a README got
rewritten), and any such change can reorder which community lands at
index 0 vs. index 3 even when 19 of 20 clusters kept nearly the same
members. `web/template.html`'s permalink hash names a cluster by id
directly, so every such reshuffle would silently repoint an existing
bookmark at a different cluster.

Fix: keep `repo_cluster_hierarchy_prev.json`, the previous run's
stabilized snapshot, and match each fresh cluster against it by Jaccard
similarity on *flattened* repo membership (walking through any nested
sub-cluster references, not just direct children, so the comparison is
apples-to-apples regardless of nesting depth) -- solved as an assignment
problem via `scipy.optimize.linear_sum_assignment` rather than greedy
nearest-match, so two new clusters can't both claim the same old id and
strand a better pairing. A match at or above 0.5 Jaccard keeps the old
id (and all its internal references -- children, parent, topLevelIds,
edgesByLevel -- rewritten consistently); anything left over mints a fresh
id from a counter that skips every value either snapshot has ever used,
so ids are permanent identifiers, never recycled slots.

`scipy` turned out to already be installed -- a transitive dependency of
leidenalg/python-igraph or fastembed's onnxruntime, checked directly
(`pip3 list`) before assuming it'd need adding. No new dependency line
needed in the pipeline docs beyond noting it's there.

**Untested against real history, by necessity.** This cohort's actual
data hasn't yet produced two genuinely different clustering runs to
validate the matching against -- a deliberate `FIRST_PASS_RESOLUTION`
perturbation (2.0 -> 2.15) was tried specifically to force a reshuffle
and instead reproduced the *identical* 20-cluster partition, meaning this
dataset's community structure is more stable under small resolution
changes than expected, not usable as a test case. Fell back to a
hand-built synthetic before/after pair instead (`/tmp/.../
test_stabilize.py`, not committed -- a one-off check, not a fixture):
same membership at a swapped positional index (must match), ~67% overlap
after two members moved (must still match), ~20% overlap (must *not*
match, mints fresh), and a wholly new cluster with zero overlap (fresh
id, no collision with the three above). All five assertions passed. The
0.5 match threshold itself is picked from general dynamic-community-
matching practice (Greene et al. 2010 uses a comparable bar), not tuned
against this project's own before/after data -- there wasn't any before
this script started producing it. Worth revisiting once a few real
refreshes accumulate and `repo_cluster_hierarchy_prev.json`'s git history
actually has something to check the threshold against.

**Real labels (`scripts/22_label_clusters.py`), and a real tension with
an earlier decision.** `web/template.html` has carried this comment since
Phase 10: "Labeled by each cluster's highest (weighted-)degree member
rather than an invented name -- everything shown in this app is a real,
derived value, not a guessed one." That was a deliberate, reasonable
stance at the time. But it produces genuinely bad labels once clusters
are thematic (Phase 13) rather than dependency-ecosystem groupings: the
22-member "computer vision" cluster's highest-degree member is
`lancopku/well-classified-examples-are-underestimated`, and the
17-member "multimodal generative models" cluster's hub is
`hadasah/btm` -- a reader gets zero signal about the actual theme from
either name. A real value, technically, but not a useful one.

This phase overturns the "not a guessed one" stance but tries to keep
faith with the *reasoning* behind it rather than discard it outright: the
label is never invented from nothing. Pipeline: pool every member's real
description + topics + cleaned README paragraph (reusing `scripts/
18_text_embeddings.py`'s `build_embedding_text()` directly -- the same
cleaned text that already drives the text-embedding similarity signal),
tokenize, and reduce to each cluster's most *distinctive* terms via
c-TF-IDF (Grootendorst 2022, the BERTopic paper's term-weighting scheme:
each cluster is one pooled "class" document; a term's score is its
in-class frequency times `log(1 + A/tf_all(t))`, where `A` is the average
class size in words and `tf_all(t)` is the term's count summed across
every class). The generic-word problem Phase 14 needed a hand-written
boilerplate stripper for doesn't reappear here: a term ubiquitous across
every cluster (`python`, `library`, `paper`) has a large `tf_all(t)` and
so a small idf automatically, no domain stopword list needed beyond
ordinary English function words. Only *then* -- given a list of
already-real, already-distinctive terms plus five real example repos and
their real descriptions -- does one `claude -p --tools "" --model haiku
--no-session-persistence` call turn them into readable prose. The prompt
says so explicitly: "Use only what the terms and examples actually
indicate -- do not invent a theme they don't support." This is this
project's first non-Python-library dependency: the `claude` CLI itself,
already on `PATH` and authenticated in any environment developing this
repo, invoked as a one-off offline subprocess call, no tools, no session
persistence. A CLI call can fail (one of the 20 real clusters timed out
at 45s on the first run -- `Diffusion Models Large`, silently identical
to what the terms-only heuristic fallback would produce; succeeded on a
targeted retry with a genuinely better label, `Multimodal Generative
Models`), so `sanitize_label()`/the fallback path exist so the pipeline
degrades to a plain Title-Case join of the top 3 terms rather than
hard-failing when the CLI is unavailable or misbehaves.

Labels are cached permanently, keyed by cluster id + a signature of that
cluster's exact flattened member set (`data/processed/cluster_labels.json`,
committed) -- this only works because ids are now stable (this script
runs strictly after `scripts/21`), otherwise every refresh would look
like 20 brand-new clusters and burn 20 fresh LLM calls regardless of
whether anything actually changed. Confirmed directly: after
deliberately invalidating 2 of the 20 cache entries, a rerun reused the
other 18 untouched and only recomputed the 2, finishing in 18s versus the
first full run's 5m17s.

Full label set (level 1, current data):

```
Python Parsing Libraries (27)        Web Parsing and Templating (10)
Jupyter and IPython Tools (23)       Sequence to Sequence Toolkits (9)
Computer Vision (22)                 LLM Hallucination & Summarization (7)
Large Language Model Methods (21)    Gradient Boosting Libraries (5)
Async Python Web (20)                Remote Command Execution (5)
Multimodal Generative Models (17)    AWS Python SDKs (3)
Speech Language Models (17)          Parameter Efficient Fine-Tuning (3)
Database and Data Formats (17)       Wireless Communications Research (2)
Deep Learning Frameworks (12)        Scene Text Recognition (2)
Machine Learning and NLP (11)        Typed Attribute Frameworks (2)
```

`web/template.html`'s sidebar cluster panel now shows the real label as
the headline and the hub repo as a secondary "hub: ..." line underneath
(rather than dropping the hub entirely -- it's still a real, useful fact,
just not a good *label*), and the on-canvas cluster node text and hover
tooltip both already inherited the new label automatically since they
read `c.label` generically rather than reconstructing a string from
`c.hub` themselves -- only the sidebar panel and the cluster tooltip's
old `<label> + " cluster"` suffix needed an actual code change.

## Two force-layout bugs: coincident spawns and orbiting clusters

Two reported symptoms in `web/template.html`'s canvas force simulation
(`tick()`), both root-caused with a jsdom harness (build `web/index.html`,
patch a `window.__debug` hook into a scratchpad copy exposing `getNodes`,
`tick`, `expandCluster`, and a first-tick position snapshot, run it
headless) rather than guessed at from reading the code alone.

**Repos spawning on top of each other.** The initial materialization loop
had hardcoded the fan-out args: `CLUSTER_ROOTS.forEach(function (cid, i) {
materializeNode(cid, { x: 0, y: 0 }, 0, 1); })` -- note `0, 1` instead of
`i, CLUSTER_ROOTS.length`. `fanPosition()`'s jitter radius is `total > 1 ?
radius : 0`, so with `total` always forced to `1` no root node ever got
separated from another one landing at the same spot. That matters because
many roots *do* land at the exact same spot by construction: a repo with
neither topic signal nor dependency data defaults to `(theta=0, r=0,
y=0.5)` (see Phase 12's coordinate system above), so any two such repos
-- or singleton "clusters" promoted straight into `CLUSTER_ROOTS` by the
singleton-collapsing pass -- get an identical world position. Measured
directly: pre-fix, spawning the real cohort produced 1209 root-node pairs
within 1px of each other, 0px apart in the worst case; post-fix (passing
the real `i, CLUSTER_ROOTS.length`), minimum pairwise distance was 2.39px
and zero pairs under 1px. Worse, this wasn't just a one-frame visual
glitch: two *exactly* coincident points are a stable fixed point of this
force system, not an unstable one -- `dx = dy = 0` makes the repulsion
term's `(dx/dist, dy/dist)` direction resolve to exactly `(0, 0)`
(`0/0.01 = 0`), so no force ever pushes them apart, and since they share
the same target they get pulled in lockstep forever after. Confirmed by
fully expanding every cluster and running 10,000 settle ticks: pre-fix,
28 repo pairs were still at *exactly* 0.00px apart at the end (e.g.
`agronholm/exceptiongroup` and `asweigart/pyautogui` -- unrelated repos
that just happen to share the no-signal default target); post-fix, zero
pairs closer than 20px. Fix: pass the real loop index/count through
(`web/template.html`, the `CLUSTER_ROOTS.forEach` call).

**Clusters orbiting indefinitely.** The repo/contrib/dependency/semantic
edge springs were deliberately damped in Y only ("resolve *horizontal*
jitter -- vertical position is the trophic constraint's job," to keep
edges from fighting the y-axis trophic placement) by computing a normal
radial force `(dx/dist, dy/dist) * f` and then post-hoc scaling `fy` by
`TIER_EDGE_Y_DAMPING` (0.12) alone. That force is applied
equal-and-opposite to both edge endpoints, and a paired force only
carries zero net torque on the pair when it stays parallel to the line
between them; scaling `fy` but not `fx` bends it off that line for any
edge that isn't already exactly horizontal or vertical -- which in
practice is nearly all of them. Worked out via the standard two-body
torque-about-center-of-mass identity: net torque is proportional to `dx *
dy * f * (1 - TIER_EDGE_Y_DAMPING)`, nonzero whenever both `dx` and `dy`
are nonzero. That's a torque *couple*, re-injected every tick an edge is
off its rest length (i.e. almost always in a live system), which the
0.82/tick velocity damping only partially offsets -- it damps speed
regardless of direction, but can't out-pace a force that's actively
adding rotational energy back in, so affected pairs settle into a
rotating limit cycle instead of coming to rest. Measured on the fully
expanded 319-node graph over 20,000 ticks: pre-fix, total system kinetic
energy (`sum |v|^2`) was flat around 105-148 for the entire run with no
downward trend at all; post-fix, flat around 85-135 -- a real but partial
improvement (~21% lower on average), not a cure by itself. The bulk of
the remaining reduction actually came from the coincident-spawn fix
above: pre-fix, 28 permanently-stuck-together pairs were quietly forcing
their neighbors to fight over the same space forever; eliminating those
did more for long-run stability than the torque fix alone. Fix: replaced
the fy-only damping with `yDampedSpring()`, which scales the *whole*
force vector by the same orientation-dependent factor (`horizFrac =
|dx|/dist`, `yDamp = TIER_EDGE_Y_DAMPING + (1-TIER_EDGE_Y_DAMPING) *
horizFrac`) -- exactly horizontal edges are undamped, exactly vertical
edges are damped to 0.12 same as before, tilted edges interpolate, and
the force always stays parallel to `(dx, dy)` so it can never carry net
torque.

Caveat found and left alone: even after both fixes, system-wide kinetic
energy on the fully-expanded 319-node graph doesn't decay to exactly
zero -- it settles to a noisy, bounded, non-increasing floor (~85-135,
average node speed well under 1px/tick) rather than a perfectly still
frame. That looks like ordinary multi-body jitter inherent to O(n^2)
unbounded-range repulsion among 319 simultaneously-visible nodes with no
cooling/annealing schedule (`tick()` runs at constant force strength
forever, by design, since this is a live view not a one-shot layout),
not a coherent "rotating group" -- confirmed the cluster-only view (104
root nodes, sparser) already settles to near-zero the same way both
before and after these fixes. Left as-is rather than adding a cooling
schedule, since that would change the always-interactive feel of the
simulation and wasn't part of either reported symptom.

## Migrating to a real 3D view (three.js/WebGL)

The layout already computed three real spatial components per repo/cluster
(trophic height `y`, and a circular topic embedding `theta`/`r`), but
`layoutWorldPos()` only ever projected `x = r*cos(theta)` and `y` -- the
natural third axis, `r*sin(theta)`, was computed nowhere and used nowhere.
The topic "circle" was literally flattened onto a line. Migrating to a real
3D renderer (three.js + WebGL, via a new `web/` npm project -- see
`web/package.json`/`web/src/`) so that axis becomes real, orbitable depth
instead of dead data. Full plan and phased build order tracked outside this
file; this section records what each phase's own verification actually
found, same as the rest of NOTES.md.

**Phase 1 (physics/coordinate-system 3D groundwork, rendering untouched):**
`layoutWorldPos` now emits `z = r*sin(theta)*TOPIC_R_SCALE` (reusing
`TOPIC_R_SCALE`, not a separate constant -- x/z are the two Cartesian parts
of one polar pair, scaling them differently would stretch the topic circle
into an ellipse for no reason). All four node-materialize sites gained
`z`/`vz`. `fanPosition()` (the jitter used to keep simultaneous spawns from
landing exactly coincident -- see the coincident-spawn fix above) became a
Fibonacci-sphere distribution instead of a flat ring, and `tick()`'s
repulsion, all four edge-tier springs, the trophic/topic homing force, and
the damp/clamp/integrate step all extended to `z`/`vz`.

The `yDampedSpring` torque-free fix (above) generalizes to 3D exactly,
which is worth stating precisely rather than assuming: the invariant that
actually matters is "force parallel to the separation vector" (`r x F = 0`
whenever `F` is a scalar multiple of `r`), which holds in any number of
dimensions, not just 2 -- so building the force as `scalar * (dx,dy,dz) /
dist` and never touching one axis afterward (the same rule that was broken
before, see above) carries over unchanged. The only real design choice was
what "horizontal" means with two free axes (x, z) instead of one: it's the
fraction of edge length lying in the free xz-plane (`sqrt(dx^2+dz^2)/dist`),
not a naive 3-axis blend, so a fully-in-plane edge is still undamped and a
straight-up-y edge is still damped to `TIER_EDGE_Y_DAMPING`, matching the
2D behavior exactly at those two extremes.

Verified with the same jsdom debug-hook harness pattern used for the
coincident-spawn/torque bugs above, extended to snapshot `z` and re-run the
kinetic-energy convergence check in 3D: on the full 319-node expanded
graph, all 104 initial root spawns have real nonzero z, 0 pairs within 1px
(3D distance) at spawn, 0 pairs still exactly coincident after 10,000
settle ticks, and kinetic energy (now including `vz`) converges over
20,000 ticks from ~17-23 down to a stable ~10-23 floor with no divergence
or growth. That floor is noticeably *lower* than the 2D-era post-fix floor
(~85-135) -- consistent with the physical expectation that 319 nodes
crowded onto a 2D plane need more constant micro-jostling to stay apart
than the same nodes with a third dimension to spread into, not a
regression or a methodology mismatch. `draw()` was intentionally left
untouched this phase (still 2D, ignores the new `z`), so the page renders
identically to before -- this phase is the physics becoming honestly 3D
without yet being seen.

**Phase 2 (the `Scene3D` three.js module, built in isolation, not yet
wired into `template.html`):** new `web/src/scene3d.js`, bundled via
esbuild (`web/package.json`) to a committed `web/scene3d.bundle.js`
(521KB minified -- three.js core + `OrbitControls`/`Line2`/`LineMaterial`
from `three/examples/jsm`, a real and conscious size jump from this
project's previous zero-dependency page). Exposes `init`, `sync`,
`render`, `resize`, `setPalette`, `setOrbitEnabled`, `pick`,
`projectToScreen`, `projectedRadius`, `unprojectToPlane`,
`cameraForwardVector`, `frameNodes`, `focusNode` as a browser global
(`Scene3D`, IIFE build -- not ES modules, since `<script type="module">`'s
own module-graph fetches hit the same `file://` CORS restriction
`build_web_explorer.py`'s docstring already documents for `fetch()`).

Tried to verify this the same way as everything else in this file --
headless, no browser -- and hit a real wall worth recording: `headless-gl`
(the only real option for a WebGL context in plain Node) only implements
WebGL 1.0, and three.js 0.169's `WebGLRenderer` requires WebGL2
unconditionally (`gl.texImage3D is not a function`, a hard failure on
construction, not a config issue). `headless-gl` is also unmaintained
upstream with 5 high-severity vulnerabilities in its dependency chain, so
even a hypothetical fix wouldn't make it worth adding. Raised this with
the user directly rather than silently picking a fallback: agreed that
real WebGL pixel output for this migration gets verified by a human
looking at a real browser, not automation -- jsdom/Node verification
stays exactly as capable as it already was for everything that's pure
math (physics, and now also picking/projection/camera-framing), and
doesn't try to cover actual rendered pixels. No Playwright anywhere in
this migration.

That pure-math boundary turned out to be large enough to matter:
`projectToScreen`/`projectedRadius`/`pick`/`unprojectToPlane`/
`frameNodes`/`focusNode` are all plain geometry over `THREE.Vector3`/
`Camera`/`Raycaster`/`Plane`/`Box3`/`Sphere` -- no canvas, no GPU, no DOM
at all -- so they're checked directly in plain Node (`web/src/
scene3d.test.mjs`, `npm test`, Node's built-in test runner -- no new test
framework dependency). This is the first committed test file in this
repo (everything before this was scratch/throwaway jsdom harnesses, per
this file's own earlier entries) -- a deliberate call given how easy
screen-space geometry code is to subtly break during a refactor and how
hard it is to eyeball-verify compared to, say, watching the force
simulation settle.

Worth it immediately: the first test run found two real problems, not
zero. (1) `pick()`'s nearest-node tie-break compared raw view-space z
directly (`depth < bestDepth`) -- but three.js cameras look down their
local -Z axis, so view-space z gets *more negative* with distance from
the camera, meaning that comparison silently picked the *farthest*
node under the cursor, not the nearest. Fixed by comparing actual
camera-to-node distance instead of a signed axis value. (2) A second
test assumed a plane's coplanar point fully pins down the plane -- it
doesn't; `THREE.Plane.setFromNormalAndCoplanarPoint(normal, point)` only
uses the component of `point` *along* `normal`, so a plane with a
purely-z normal is the plane "z = point.z" regardless of the point's x/y
-- correct three.js behavior, and the test's own expectation was wrong,
not the code. Rewrote it to assert something geometrically real (moving
the plane along its normal shifts the resolved depth) instead of deleting
it. 11/11 pass after both fixes.

**Phase 3 (wiring `Scene3D` into `template.html` -- hybrid rendering):**
`<script src="scene3d.bundle.js">` loads before the existing inline
script; a new `#overlay2d` canvas stacks on top of `#graph` (pointer-
events:none, so `#graph` stays the interactive/mouse-listener surface).
`draw()` now splits: node fills/avatar textures/edges/dependency
arrowheads (the parts that need real depth/occlusion) go through
`Scene3D.sync()`+`render()`; rings/badges/dashed borders/labels (heavily
stateful -- hover, selection, path/compare dimming -- and cheap to keep
as Canvas-2D) stay near-identical to the old code, just repositioned via
`Scene3D.projectToScreen(node)` instead of the old world-space
`ctx.translate/scale` transform. That also simplified every width/radius
constant in the overlay: the old code divided by `transform.scale`
everywhere to stay screen-constant under a zoom transform; the new
screen-space-only overlay needs no such division anywhere, since
`projectToScreen`'s `screenRadius` already bakes in perspective.

Edge styling needed a real design decision Phase 2 hadn't settled: today's
`draw()` computes rich per-edge state every frame (dim on unrelated-to-
selection, "hot" on hover, "active" when touching the selected node) --
not just a flat per-tier default. Rebuilt `syncEdgeTier` to keep one
`THREE.Line2` per edge (not one merged per-tier geometry) so the vanilla
layer can stamp `e._color`/`e._opacity`/`e._width` per edge before
calling `sync()`, same boundary as the node `_clusterTint`/`_dim` hooks
from Phase 2 -- Scene3D stays a "dumb" renderer that draws whatever
style it's handed, all the *decisions* about what should be dim/hot/
active stay in the vanilla layer. Also fixed the dependency-tier
arrowheads to pull their color from the live palette (`e._color`) instead
of a hardcoded hex value Phase 2 had left in as a placeholder -- would
have been visibly wrong in dark mode.

`Scene3D.init()` needs a real WebGL2 context, which isn't guaranteed
everywhere (see the headless-gl finding above for a concrete case where
it's genuinely absent) -- wrapped in try/catch with a `scene3dReady` flag
gating every Scene3D call site, so a WebGL failure can't take down
search/path-finder/compare/permalink, none of which depend on rendering
at all. Verified this is a real, working fallback, not just a hopeful
try/catch: re-ran the jsdom regression harness against the actual built
`web/index.html` + `web/scene3d.bundle.js`, this time *without* stubbing
`getContext("webgl2")` at all (only "2d" is stubbed, same as every other
harness in this file) -- jsdom's real, unimplemented WebGL support throws
exactly the same "Error creating WebGL context" a browser with hardware
acceleration disabled would, `scene3dReady` correctly resolves `false`,
`draw()` no-ops instead of throwing, and materializing the full 319-node
graph via cluster expand/collapse plus 200 tick+draw cycles all run with
zero uncaught errors. That's also, incidentally, the honest current
verification ceiling for this phase: everything above is confirmed
headless, but nobody has yet looked at the actual WebGL output in a real
browser -- still pending before this migration is called done.

**Phase 4 (interaction: picking, drag, LOD, camera fit) -- and a real bug
Phase 3's testing couldn't have caught.** Every `Scene3D.X(...)` call from
the vanilla script had been relying on `this` inside `scene3d.js`'s
exported functions resolving to the renderer instance `init()` created.
It doesn't: `Scene3D.pick(sx, sy)` is a plain method call on the bundled
IIFE's namespace object, so `this` inside `pick` binds to that namespace
object, not a renderer instance -- `s.camera`/`s.lastNodes`/etc. would all
have been `undefined` in the actual browser. `scene3d.test.mjs` never
caught this because it called every function as `fn.call(fakeState,
...)`, which forces the *correct* binding by construction -- exactly the
gap the tests couldn't see past. Caught by tracing through *why*
`Scene3D.resize(...)` would work before wiring up the interaction calls,
not by running anything (this specific bug has no headless reproduction
at all: jsdom can't get far enough to hit it, since `Scene3D.init()`
already throws on the WebGL context first). Fixed by restructuring the
whole module: every internal function now takes its state as an explicit
first argument (`fooCore(state, ...)`, directly unit-testable, no `this`
tricks needed -- the test file got simpler, not more complex, from this
fix), and the public `Scene3D.foo(...)` API is a thin singleton wrapper
closing over a module-level `current` instance set by `init()`. Also
found a second, real bug this same refactor's test suite caught on its
own: `pickCore`'s edge fallback iterated `Object.keys(s.edgeLines)` (the
THREE.Line2 render-object pool) instead of `Object.keys(s._lastEdgeTiers)`
(the actual edge data) to decide which tiers to search -- happens to
always agree in production (`syncEdgeTier` populates both together) but
is needless coupling, and broke a newly added "falls through to an edge"
test that populated edge data without also faking a render-object pool.

Interaction changes: `checkLodTransitions()` moved from three discrete
`transform.scale`-mutation call sites (2D wheel/fitView/focusNode) to
running once per animation frame from `loop()` -- there's no fixed small
set of "camera changed" events anymore once `OrbitControls` can move the
camera continuously mid-drag. Kept the existing reentrancy guard
(protects against `expandCluster`/`collapseCluster`'s own synchronous
tick bursts) and added a one-shot `suppressNextLodCheck` flag so
`fitToNodeIds()` (path-finder/compare framing) can still skip the very
next check the way it deliberately used to by simply not calling the
function -- necessary now that there's no longer a call site to omit.
Picking replaced `findNodeAt`/`findEdgeAtIn` (world-space math against
the old 2D `transform`) with `Scene3D.pick()` (real depth-ordered,
perspective-aware screen-space picking) via two thin wrappers
(`pickNode`/`pickEdge`) that also restrict edge-hover to the three tiers
that actually have a tooltip (contrib/dependency/semantic -- repo/child/
path edges were never hoverable before either). Node dragging is
constrained to a screen-parallel plane frozen at the node's position when
the drag starts (`Scene3D.unprojectToPlane`), with `OrbitControls`
explicitly disabled for the gesture's duration -- both are left-drag
gestures on the same canvas and would otherwise fight over the same
mousemove events. Manual 2D pan/wheel-zoom handlers are gone entirely;
`OrbitControls`'s own listeners (registered on `#graph` inside
`Scene3D.init()`) own orbit-drag and wheel-dolly natively now. Also found
and fixed a real sequencing gap: `fitView`/`focusNode`/`fitToNodeIds` all
run once during initial page load *before* the first `draw()` ever
does -- but they now read `Scene3D`'s last-synced node list (populated by
`sync()`, called from `draw()`) instead of the raw `nodes` array directly,
so the initial camera framing would have silently no-opped without an
explicit `draw()` call inserted before that startup logic runs.

Verified everything jsdom can reach: extended the debug hook with
`pickNode`/`pickEdge`/`checkLodTransitions`/`fitView`/`revealRepo`/
`computePath`/`computeCompare`, confirmed `scene3dReady=false` still
degrades every one of them to a clean no-op (`pickNode`/`pickEdge` return
`null`, `checkLodTransitions`/`fitView` no-op) rather than throwing, ran
300 tick+draw+checkLodTransitions cycles with zero uncaught errors, and
exercised a real path-finder round trip (`revealRepo` both ends of the
`dmlc/xgboost` <-> `psf/requests` example, `computePath()`) to confirm
search/reveal/framing still works end to end through the rewritten
`focusNode`/`fitToNodeIds`. What jsdom categorically cannot confirm --
and what's still outstanding before this migration is done -- is any of
it actually rendering or feeling right: real WebGL output, orbit/drag
interaction against a real camera, avatar textures, dark/light material
colors, dependency arrowhead orientation. That needs a human opening
`web/index.html` in a real browser, by design (see the headless-gl entry
above) -- not yet done as of this note.

**First real-browser look, two fixes:** (1) rotating a real 3D view gives
no fixed reference for which direction is the meaningful axis (trophic
height) versus incidental camera framing -- the old 2D view never needed
one, since the whole canvas already *was* that one plane. Added a
labeled vertical reference line (`Scene3D.buildAxis()`, a real 3D
`Line2` spanning `TROPHIC_Y_RANGE` with short ruler-style end caps, text
labels "consumers"/"dependencies" on the overlay same as every other
label). (2) hover tooltips were showing mid-orbit-drag, since the mouse
sweeps across the whole screen while rotating the camera but isn't
pointing at anything. Fixed via `OrbitControls`'s own "start"/"end"
events (only fire for user-driven gestures, not programmatic camera
moves like `frameNodes()`), exposed as `Scene3D.isOrbiting()`, checked in
the mousemove handler to suppress hover/tooltip entirely during an active
orbit. Re-verified everything jsdom can reach (still nothing throws, full
expand still reaches 319 nodes, path-finder round trip still works) --
the two fixes themselves need another look in a real browser, same
ceiling as before.

Both fixes confirmed good in a second real-browser look -- migration
called done from a functional standpoint. **Final regression pass**
before wrapping up: extended the debug hook to drive every remaining
feature surface end to end (six-degrees path finder over a real xgboost
<-> requests chain, compare mode over two real repos, external repo
lookup outside the cohort, type-filter and edge-tier toggles, dark/light
theme + cluster-color-toggle repaints, permalink hash encoding, reset-
expansions) on top of the fully-expanded 319-node graph -- all ten checks
passed clean, zero uncaught errors. One non-finding worth recording
honestly: `updateHash()`'s `history.replaceState()` throws a
`SecurityError` under jsdom for `file://` URLs (stricter opaque-origin
handling than real browsers apply) -- confirmed via direct source read
that `updateHash()` itself is completely unmodified by this migration, so
this is a harness/environment limitation, not a regression; the hash-
string-building logic that *is* under test runs to completion regardless.

Migration complete: `ROADMAP.md` gained a Phase 18 entry (real 3D view,
explicitly reconciled against Phase 17's scale trigger so a future reader
doesn't conflate the two) and `README.md`'s Layout/Status sections now
describe the WebGL renderer and its separate `npm run build` step
alongside the existing `build_web_explorer.py` one.

## Hierarchical edge bundling (ROADMAP.md Phase 16)

Picked up right after the 3D migration above, and reuses its hybrid
rendering boundary directly: `web/template.html` (data/decisions) computes
*where the bundled waypoints are*, `web/src/scene3d.js` (rendering) turns
that into a smooth curve. Neither side needed to learn anything about the
other's job.

**The tree-path machinery already half-existed.** `materializedAncestorOf`
(built for Phase 10's LOD collapse) already walks a repo/cluster id up
`staticParentOf`'s chain to whichever ancestor happens to be a currently
materialized node. Bundling needed the *other* direction -- given two
already-materialized ids, find their lowest common ancestor and the full
path through it -- so `ancestorChain`/`lcaPath` are new, but they walk the
exact same `staticParentOf` chain, not a second copy of the hierarchy.
`e._pathIds` (just ids) gets stamped once per edge inside
`rebuildTierEdges()`, which already only runs when the materialized
frontier changes; `draw()` resolves ids to live positions and blends them
every frame via `bundledControlPoints()`, since only the positions move
every tick, not which ids are on the route.

**Measured before shipping, and the number changed the design.** The
first working version skipped bundling for any edge whose two endpoints
shared no real ancestor in `CLUSTERS` (dependency's own tier being pulled
entirely out of the clustering substrate in Phase 13, so this seemed like
it'd be common but rare enough to just skip). A jsdom harness that fully
expands the graph and counts real `_pathIds.length >= 3` cases came back
13 out of 863 -- 1.5%. Sanity-checked the reason directly: `CLUSTERS` is
built from co-star-PMI + topic-PMI (Phase 13), a completely different
substrate from the dependency graph on purpose, so two repos connected by
"repo A imports package from repo B" mostly have no reason to land in the
same Leiden community. Shipping the skip-if-no-ancestor version would
have meant 98% of dependency edges rendered exactly as before this phase
started -- not actually solving the "hairball" problem Phase 16 exists to
fix, just quietly not-fixing it for almost everything.

Fixed by giving the `CLUSTERS` forest a single synthetic root
(`SYNTHETIC_ROOT_ID`) sitting at the exact world origin -- every id's
`ancestorChain` now terminates there implicitly (via `lcaPath`'s fallback,
not `ancestorChain` itself, so a real shared ancestor closer to the leaves
still wins when one exists), and `controlPointPosition` resolves it to
`{x:0,y:0,z:0}` directly, no lookup needed. This is the standard fix for
running hierarchical edge bundling over a forest instead of one tree --
Holten's own original examples (a software call graph, a package
directory tree) always have one root by construction; `CLUSTERS` doesn't,
because Phase 10's singleton-collapsing pass deliberately leaves a
singleton-all-the-way-up repo with no wrapping cluster at all. Re-ran the
same harness after the fix: 863/863 dependency edges now resolve a real
`>= 3`-point path, and a stronger per-edge check (added at the same time)
confirms every one of them actually has at least one interior control
point that measurably deviates from the plain straight line, not just a
path array that happens to exist.

**Bundling strength picked down from Holten's ~0.85, not copied
blindly.** That value comes from his paper's deep multi-level hierarchies,
where the pull toward the tree route is shared out over several waypoints
-- softly. Here, the vast majority of routes are exactly one hop to the
synthetic root (a real shared `CLUSTERS` ancestor only happens for that
1.5%), so the same 0.85 would yank nearly every cross-cluster edge hard
toward one single point in space. Shipped at 0.55 instead -- visibly
different from a straight line without dragging the whole graph's
dependency edges through one pixel. This is a first-pass judgment call
with no further tuning behind it, flagged the same way Phase 12's
`TIER_EDGE_Y_DAMPING` was: a real constant in `web/template.html`
(`EDGE_BUNDLING_STRENGTH`) if a future pass wants to push further, not a
value anyone should assume is final.

**Rendering, picking, and arrowheads all had to learn a route can be more
than 2 points**, not just the bundling math itself:
- `scene3d.js`'s `syncEdgeTier` used to call `LineGeometry.setPositions`
  with exactly `[source, target]`; now any edge with a real `e._path`
  (currently only the dependency tier stamps one) gets its waypoints run
  through a `THREE.CatmullRomCurve3` and sampled into a smooth multi-point
  line instead -- the control points are ancestor positions, not meant to
  read as sharp corners.
- `pickCore`'s edge-fallback hit test used to check exactly one segment
  (`e.source` to `e.target`). A bundled edge can bow well away from that
  straight line, so it now walks every segment of the edge's actual drawn
  route (`edgeRoutePoints()`, shared with the sync/arrowhead code so all
  three agree on what "the route" is). Caught with a real unit test before
  it could ship wrong: placed a screen point exactly on the old straight
  source-target chord (computed precisely via the same `PerspectiveCamera`
  math the test file already uses, not eyeballed) and asserted picking
  there now correctly returns nothing, since the edge no longer actually
  passes through that pixel.
- Dependency arrowheads used to back off `target.radius + 10` along the
  raw source-target vector. Now they back off that same distance along the
  route's *final* segment instead -- otherwise a heavily bundled edge would
  place its arrowhead somewhere mid-air along a chord the edge doesn't
  even follow anymore, or point it through the detour rather than along
  the direction it actually approaches the target from.

**Verification ceiling is the same one this migration already
established.** jsdom covers everything that's pure math/logic here in
full: path-id correctness, endpoint-exactness, the real-deviation-from-
straight-line check, the full Phase 18 regression suite re-run against
the changed files (search/six-degrees/compare/external-lookup/toggles/
theme/permalink, all still clean), and three new/changed unit tests in
`scene3d.test.mjs`. What it cannot confirm -- same as every rendering
question since the 3D migration started -- is whether the bundled curves
actually *look* like a decluttering improvement rather than added visual
noise; that's a real-browser judgment call, not yet made as of this note.

## Issue-poster edges + issue-text semantics (ROADMAP.md Phase 19)

This phase sat "flagged, not scheduled" for one concrete reason: `hasIssue`
alone is 2.6M triples dump-wide, two orders of magnitude past anything else
this pipeline fetches per-repo (Phase 8's descriptions and Phase 14's
READMEs are both one fetch per repo; issue data is one-to-many). Unblocking
it meant picking a real sampling design, not writing a script that could be
pointed at the dump as-is.

**The join shape.** `hasIssueAuthor`'s subject is the *issue* URI
(`<repository/{owner}/{repo}/issue/{n}>`), not the repo -- unlike
`hasStargazer`/`hasContributor`, which are direct repo-to-person triples.
`scripts/23_shared_issue_authors.py` resolves this with a two-pass grep
(same two-stage filter-then-parse shape `09_resolve_packages.py` already
uses, reusing `ntparse.py`'s literal/URI handling rather than hand-rolling
NT parsing again): pass 1 walks `hasIssue` lines for the cohort, capping at
`max_issues_per_repo` issues per repo (first-encountered in file order,
deterministic, same idiom `04_repo_expansions.sh` uses for its own
individual-level issue sample) to build an issue-id allowlist; pass 2 greps
exact-subject patterns for just that allowlist (bounded at
`cohort_size * cap`, so still a precise, cheap grep) and extracts
`hasIssueAuthor`/`dc:title` triples for the sampled issues only.

**The cap was measured, not guessed.** A first pass at 40 (roughly
`04_repo_expansions.sh`'s `MAX_ISSUE=12` scaled up for a signal-detection
input rather than a handful of display nodes) yielded exactly *one* pruned
edge over the whole 319-repo cohort -- not useful. Rather than assume a
slightly bigger number would fix it, 40/100/150/200/300 were each run for
real and measured:

| cap | issues sampled | pruned edges | repos with an edge (of 74 covered) |
|-----|----------------|--------------|-------------------------------------|
| 40  | 2,677          | 1            | 2                                    |
| 100 | 6,583          | 20           | 28                                   |
| 150 | 9,704          | 48           | 41                                   |
| 200 | 12,722         | 95           | 52                                   |
| 300 | 18,345         | 137          | 59                                   |

300 shipped: growth was still real at that point (not yet flattened), and
the grep cost stayed cheap regardless (~53s over the 12GB dump for the
whole two-pass extraction at cap 300) -- there was no cost pressure pushing
back toward a smaller number, only diminishing coverage returns eventually
would have.

**A real, honest coverage finding**, same shape as the co-star/topic gaps
elsewhere in this pipeline: only 74 of the 319 cohort repos have *any*
`hasIssue` data in this dump at all (measured directly via a full-cohort
grep before picking a cap, not assumed). Of those 74, real issue counts
range from single digits (a fully-covered sample) into the tens of
thousands (`pytorch/pytorch` alone hits the dump's own 20000-per-repo cap),
so the cap-300 sample is exhaustive for quiet repos and a thin slice for
busy ones either way.

**A real data-quality bug, caught by measuring the intermediate result
before shipping it** (same "measure, don't assume" instinct as Phase 14's
contrarian-claim test and Phase 16's synthetic-root fix): an early
cap-150 run surfaced `rwightman/pytorch-image-models` sharing 2-3 "issue
posters" with several unrelated repos purely via a shared value of
`"ghost"` -- GitHub's placeholder for a deleted account. Since *every*
repo with any issue from a since-deleted user gets the identical `"ghost"`
value, counting it as a shared person fabricates an edge between
otherwise-unrelated repos. The same pass also surfaced bot accounts stored
as a full nested URI under `person/`
(`<person/https://semrepo.org/bot/github-actions[bot]>`, `<.../bot/
pytorch-bot[bot]>`) -- a real quirk specific to `hasIssueAuthor` in this
dump, not seen in `hasStargazer`/`hasContributor` (same flavor of surprise
as Phase 7's "some dependency-cohort repo ids are stored backwards").
Both are filtered out in `23_shared_issue_authors.py` before an author line
is ever written. Re-measuring after the fix confirmed it mattered: cap 150
*before* the fix produced 95 pruned edges (partly from bogus ghost/bot
overlap); cap 150 *after* the fix produced only 48 -- cap 300 was needed to
reach a comparable edge count from genuinely real signal, which is the
number actually shipped.

**The edge tier itself.** `compute_shared_edges.py` (reused unchanged --
same min-2-shared/top-4-pruned overlap core every repo-repo tier here
already shares) turns the cleaned `.nt` into 137 pruned edges over 59 of
the 74 issue-covered repos. Wired into `web/template.html` as a fifth
"linked by a common person" tier, same family and same treatment as Phase
7's shared-stargazer/shared-contributor and Phase 12's demotion of both:
not a legend checkbox, always contributing a gentle `tick()` attraction
force, only ever drawn when it touches the currently hovered or selected
repo (`edgeInFocus`). `palette.edgeIssueAuthor` reuses the existing
`--n-issue` CSS variable (already the color for individual "issue" nodes
from Phase 3's expand-in-place feature) rather than minting a new one --
same dual-purpose convention `--n-contrib` already established for the
contributor tier. Top real edge: `rwightman/pytorch-image-models` ↔
`huggingface/pytorch-image-models`, 106 shared issue posters -- not a
coincidence, that's the real `timm` library's GitHub-org rename, and the
edge is exactly the kind of real relationship this tier is supposed to
surface.

**A real finding from wiring this into six-degrees pathfinding, checked by
exhaustive search rather than assumed either way:** no shortest path in the
current cohort actually routes through the new `issueAuthor` tier --
`buildPathAdjacency()`'s BFS visits stargazer/contributor/dependency/
semantic edges (in that insertion order) before issueAuthor for any given
node, and every real issue-poster-linked pair already has an equally-short
or shorter connection via one of those four. The tier is real, additive
Compare-mode/hover signal, not a new pathfinding shortcut, in this cohort
as it stands today -- plausible in hindsight, since people who file issues
against each other's-adjacent projects likely already overlap on
stargazers/contributors/topics too. This did surface one real bug before
it could ship, though: `computePath()`'s `tierLabel` lookup map (used to
render "N hops via shared contributors & real dependencies" etc.) had no
`issueAuthor` entry, which would have rendered `undefined` in the path
summary the one time real data *did* produce that hop. Since exhaustive
search over live data proved that hop never actually happens today, the
fix was verified by temporarily emptying the other four tiers' full-edge
arrays in the jsdom harness (isolating `issueAuthor` as the only route
between a real pair) and confirming the summary renders "shared issue
posters" correctly instead of `undefined` -- a case where proving a fix
correct required constructing the condition rather than finding it
naturally in the data.

**Issue titles folded into Phase 14's text-embedding input.**
`load_issue_titles()` in `scripts/18_text_embeddings.py` appends a capped,
joined slice of a repo's sampled issue titles (20 titles, 400 chars) when
`repo_issue_titles.json` has any for that repo -- capped shorter than the
README paragraph's 600 chars deliberately, since issue titles are
individually short incident-report phrasing ("TFLite?", "crash on
startup") rather than prose describing the project, meant to nudge the
embedding rather than compete with the parts that actually describe what
the repo is. Only thickens signal for the 74 already-partially-embeddable
repos; doesn't close a coverage gap the way the README paragraph did for
Phase 14 (317/319 repos were already embeddable before this change, and
still are after it).

Re-running `scripts/18_text_embeddings.py` and measuring cosine similarity
against the pre-change vectors confirmed a controlled, bounded
perturbation: exactly the 74 repos with issue titles moved (average cosine
similarity 0.92 against their old vectors, individual repos ranging as low
as 0.72), and the other 243 embedded repos came back bit-for-bit identical
(cosine 1.0 exactly) -- proof the change is additive, not a wholesale
re-embedding.

That perturbation was real enough to ripple into reclustering, though, so
the downstream chain (`14_cluster_hierarchy.py` → `21_stabilize_cluster_
ids.py` → `22_label_clusters.py`) was re-run and checked rather than
assumed safe. Top-level cluster count held at 20. Comparing membership by
hub name undercounted how stable the result actually was (hub = highest-
degree member, which can shift even when a cluster's membership barely
does); the real measure is Phase 15's own Jaccard-matching machinery, and
this was its first real test -- until now it had only been verified
against a hand-built synthetic before/after pair, since this cohort's data
had never produced two genuinely different real clustering runs. It
matched 19 of 20 clusters to their previous stable id, re-minting only
one: the `openmoss/moss`-hub cluster reshaped substantially (13 of its 21
prior members left, 17 new ones joined, net size 21→24) from a mix of
small academic diffusion/few-shot-learning repos into a more thematically
coherent speech/LLM-chat/diffusion grouping (`2noise/chattts`,
`myshell-ai/openvoice`, `lllyasviel/controlnet`, `NVIDIA-NeMo/Speech`,
`PaddlePaddle/PaddleSpeech`, `mlc-ai/web-llm`, ...) -- re-labeled "Speech
and Language AI" by `scripts/22_label_clusters.py` on the next run, which
reused 9 of 20 cluster labels from cache and only regenerated the 11 whose
membership signature actually changed.

**Still true, worth naming honestly:** the dump has a per-issue **title**,
not a body/description field -- no `hasIssueBody` or similar predicate
shows up in `data/processed/predicate_counts.txt`, so "title+description"
as this phase was originally floated is really just "title" until/unless a
body field turns up somewhere this check didn't look.

**Verification**, same jsdom+debug-hook harness pattern as Phases 16/18
(not committed): edge-tier construction and dedup, `edgeVisible.issueAuthor`
permanently true, `edgeInFocus` gating on select/hover, Compare mode's real
overlap count and member list, the exhaustive pathfinding search and the
isolated-tier hop-rendering check both described above, and a full re-run
of the Phase 18 regression suite (cluster expand to all 319 repos, six-
degrees, compare, external lookup, type/edge toggles, theme, permalink)
against the rebuilt `web/index.html` -- all clean. `npm test` (13/13,
unchanged) confirms `scene3d.js`'s generic per-tier rendering/picking never
needed a code change for a fifth tier to work, only its caller in
`template.html` did. Visual judgment of the new edge tier's color and how
it reads in the legend is, same as every rendering question since the 3D
migration, a real-browser check, not yet made as of this note.

## Fading LOD cluster transitions instead of hard swaps

Reported after the LOD clustering phase shipped: clustering hid the
contained repos, and zooming/dezooming (or clicking a cluster) "violently"
changed the view. Root cause was two things compounding:
`expandCluster`/`collapseCluster` swap nodes instantly (see "Level-of-detail
clustering" above), *and* each ran its own synchronous 30-60 tick physics
pre-settle burst immediately after materializing/removing nodes --
originally added specifically to avoid a jumpy multi-frame scramble (see
that section's own comment). That pre-settle turned out to be the actual
culprit: children appeared on the very next paint already at their fully
converged position, with zero visible motion in between, whether the
trigger was a continuous zoom (`checkLodTransitions`, once per orbit-camera
frame) or a click (`selectNode` -> `expandCluster`).

Fix has two parts. First, the pre-settle bursts in `expandCluster`/
`collapseCluster` are gone entirely -- real per-frame `tick()` calls
(already running every frame via `loop()`) now do the settling, which
turned out to be safe to cut: each child spawns via `fanPosition` already
close to its own real trophic/topic target (the fan radius only jitters
siblings apart from each other -- it was never "spawn near the parent,
drift far to the true spot" the way that might sound), so what's left to
settle is a small, local rearrangement, not a big jump. Second, opacity
fades: a freshly materialized node (`materializeClusterNode`/
`materializeRepoNode`) now carries a `_fadeInStart` timestamp and eases in
from 0 over `LOD_FADE_MS` (420ms, `fadeInOpacity()`); a removed node
(`removeMaterializedNode`) leaves a frozen "ghost" snapshot behind in
`fadingOutGhosts` instead of vanishing on the spot -- rendered (via
`Scene3D.sync`) at its last real position with a `_fadeOpacity` ramping
1->0, but never added to `nodes`/`nodeById`, so it gets no physics, no
picking, and doesn't count toward edge routing. `draw()` prunes a ghost
once `LOD_FADE_MS` has elapsed.

`_fadeOpacity` had to be threaded through as a genuinely new multiplier,
not folded into the existing `_dim` (selection/path/compare dimming)
boolean -- the two are orthogonal (a fading-in node can simultaneously be
dimmed by an active selection) and multiply rather than override:
`scene3d.js`'s sprite opacity is now `(n._dim ? 0.25 : 1) * fadeOpacity`,
and the 2D overlay's ring/badge/label `globalAlpha` calls got the same
treatment, so the WebGL fill and the 2D chrome (dashed ring, cluster badge,
label) fade in sync instead of the chrome popping in ahead of the fill.

Scoped to LOD cluster expand/collapse only, not tier-2's click-to-expand
sample-neighbor nodes (`expandRepo`/`collapseRepo`) -- that machinery keeps
its own pre-settle burst and instant swap, unchanged. The complaint was
specifically about clustering, and tier-2 expand doesn't remove/replace an
existing node's identity the same way (children fan out *alongside* their
still-visible parent rather than in place of it), so the "violent swap"
framing doesn't really apply there.

**Verified** via the usual jsdom + Canvas-2D-stub debug-hook harness (not
committed, scratch-only): a freshly materialized node's `fadeInOpacity()`
is in `[0, 1)` immediately after creation and exactly `1` once `LOD_FADE_MS`
has elapsed (simulated by rewinding `_fadeInStart`, not a real sleep); a
child spawned by `expandCluster` starts at `v = (0,0,0)` and only actually
moves after an explicit `tick()` call, confirming the synchronous
pre-settle burst is really gone; `collapseCluster` leaves real ghost
entries with a `_fadeOutStart`, and the re-materialized cluster meta-node
itself starts at rest and mid-fade; `draw()` prunes every ghost once aged
past `LOD_FADE_MS` and stamps `_fadeOpacity` onto real nodes. `npm test`
stayed 13/13 (the `scene3d.js` opacity change is a pure multiply, nothing
existing depended on the old formula), and a full regression sweep
(cluster-expand to all 319 repos, six-degrees, compare, external lookup,
type/edge toggles, theme, permalink) against the rebuilt `web/index.html`
still passes clean. As with every rendering question since the 3D
migration, the actual visual smoothness of the fade is a real-browser
judgment call, not made as of this note -- jsdom can't create a WebGL
context.

**Follow-up, reported immediately after the above shipped: still a "bubble
explosion" on zoom, graph should stay mostly static.** Opacity fading alone
didn't fix the underlying motion -- it just made the pre-existing scramble
visible instead of hidden (see above: the synchronous pre-settle burst was
removed specifically so real per-frame `tick()` calls would show the
settle). The actual culprit is `tick()`'s O(n²) repulsion (`2800/distSq`,
sharp at close range): same-cluster siblings often share similar trophic/
topic targets (that's *why* Leiden grouped them), so a dozen-plus children
can spawn within real physical reach of each other, and with no pre-settle
to hide it, real frames now visibly show them shoving apart -- plus every
tier-edge spring yanking a freshly-spawned node toward its real partner at
full strength from frame one, both reading as a "bubble" expanding outward
right where the click/zoom happened.

Fix: `fadeInOpacity(n)` now doubles as each node's *physical* weight in
`tick()`, not just its render opacity -- precomputed once per node per tick
as `n._physWeight` (not per-pair; calling it inside the O(n²) loop directly
would be ~50k redundant calls/tick at this cohort size for a value that's
constant across one tick for a given node) and multiplied into both the
repulsion force between every pair and every tier-edge spring force
(`e.source._physWeight * e.target._physWeight`). A node barely visible yet
barely pushes or pulls on anything; both ramp to full strength together
with its opacity over `LOD_FADE_MS`. This is also what keeps the *rest* of
the graph close to static during a zoom-triggered expand: an established
node (weight 1) sitting near a freshly-spawned one (weight ~0) feels almost
nothing from it at first, instead of the full-strength shove a same-weight
newcomer would give it -- the "explosion" was never really about the new
nodes' own motion looking bad in isolation, it was that *everything nearby*
got shoved too.

Deliberately scoped to LOD transitions only, not the initial page-load
spawn or tier-2 click-to-expand: `_fadeInStart` is no longer stamped inside
`materializeClusterNode`/`materializeRepoNode` themselves (which are also
called from the initial `CLUSTER_ROOTS` spawn and would otherwise have
zeroed out repulsion across the *entire* graph for the first `LOD_FADE_MS`
after every page load, undermining the initial-layout warm-up loop that
relies on full-strength repulsion to spread a jittered/near-coincident
initial spawn apart). Instead `expandCluster`/`collapseCluster` stamp
`_fadeInStart` explicitly on the specific nodes they just materialized,
right after creating them.

**Verified** (same jsdom + Canvas-2D-stub harness): forced an established
"bystander" node to sit exactly on top of a cluster the instant before it
expands, ticked once, and measured its displacement -- 2.9 world units
while the newly-spawned children are still fully transparent, vs. 9.5 units
once their fade is aged past `LOD_FADE_MS` and replayed from the same
starting position (roughly a 3x reduction). Separately, forced every child
of an expanding cluster to spawn stacked at the exact same coincident point
(worst case) and compared peak single-tick speed: 2.9 while still fading in
vs. 7.2 once fully aged (roughly 2.5x). Both confirm the damping is real
and directionally correct, not just present in the code. Full regression
sweep and `npm test` (13/13) re-run clean against the rebuilt bundle/page.

**Also fixed in the same pass:** the trophic-height reference axis line
(`buildAxis()` in `scene3d.js`) was reported too thin to read -- `linewidth`
1.5 -> 3, `opacity` 0.5 -> 0.65 (later bumped again to 0.9, and the line's
world-space length decoupled from `TROPHIC_Y_RANGE` to overshoot the real
node layout range by 20%, both per further direct feedback -- see below).
Pure constant changes, confirmed present in the rebuilt minified bundle;
actual on-screen legibility is, like the fade smoothness above, a
real-browser judgment call.

**Second follow-up: still a "bubble explosion," graph should stay static
-- diagnosed as an edge-topology change, not (only) repulsion.** The
`_physWeight` damping above only ever addressed the newly-*materialized*
side of a force -- it didn't explain why already-settled, unrelated nodes
kept drifting for a while after a nearby cluster expanded. The real second
mechanism: `rebuildTierEdges`/`buildTierEdges` resolve every raw repo-repo
edge fresh against whichever node currently stands in for each endpoint
(`materializedAncestorOf`). While a cluster is collapsed, every raw edge
touching any of its members merges into *one* edge pointing at the
cluster's own centroid (weights summed). The instant it expands, those
same raw edges resolve individually to specific children instead -- often
several separate edges scattered across different real positions instead
of one aggregate. An external node that was in force equilibrium with a
single spring can suddenly find itself pulled by several different springs
toward different points, and keeps drifting toward the new true balance
for a while, independent of any fade-in damping (which only ever covered
the fresh side of that same edge, not the established one).

Fix, matching the shape suggested directly: a cluster's meta-node no
longer disappears (not even as a transient fading ghost) while it's
expanded. `expandCluster` now creates a persistent `clusterHalos[cid]`
entry instead of the usual ghost (`removeMaterializedNode(node, {
skipGhost: true })`) -- it eases down to a low floor opacity
(`CLUSTER_HALO_OPACITY = 0.16`) over the same `LOD_FADE_MS` and then
*stays there* for as long as the cluster remains expanded (not pruned by
time like a normal ghost), tracking the live centroid of its current
children every frame (`clusterCentroidNow`, already existed for the
zoom-driven auto-collapse check) so the halo visibly follows its contents
rather than sitting frozen. `collapseCluster` deletes the halo entry when
a real, full-opacity meta-node is re-materialized in its place.

Alongside the halo, every direct child of an expanded cluster now gets a
containment leash in `tick()`: zero force while within
`clusterContainRadiusFor(cid)` of the cluster's real aggregate position
(`layoutWorldPos(clusterLayoutTargetFor(cid))`), a proportional pull-back
once outside it. That radius is deliberately *not* a fixed guess or the
visual bubble-size-by-member-count radius -- it's derived from the real
per-child trophic/topic targets (the max real distance from the cluster's
own aggregate target to any direct child's own target, times a 1.4x
margin, floored at 2x the visual radius and 60 world units). This matters
because of a finding from Phase 13 (see "Cluster the similarity substrate,
not dependency" above): clustering runs on the social/semantic substrate,
not the trophic/topic axes, so real members of one cluster can legitimately
sit at quite different trophic heights or topic angles. A fixed/tight cage
would have fought that real, meaningful spread instead of only catching
drift caused by external edges overpowering the homing pull -- the whole
point is to stop *excessive* wander, not to re-flatten data Phase 12/13
already went out of their way to differentiate.

Nesting falls out for free: `tick()`'s leash loop walks every
`expandedClusters` key and only its *direct* child ids: if one of those
children is itself an expanded sub-cluster, `nodeById[childId]` is
undefined (its own meta-node was removed when it was expanded), so that
entry is skipped -- and the sub-cluster's own children get leashed to *it*
independently on the very same pass, since the sub-cluster's id is also a
key in `expandedClusters`. No extra parent-tracking needed.

**Verified** (same jsdom harness): confirmed `expandCluster` no longer adds
to `fadingOutGhosts` at all (the halo path replaces it entirely for the
cluster-meta-node removal specifically); the halo's opacity ramps down to
the floor and then, unlike a ghost, survives well past `LOD_FADE_MS`
without being pruned; `collapseCluster` deletes the halo. For the leash:
placed one real child at 5x its cluster's real containment radius directly
out from the cluster's home position and ran 5 ticks -- distance from home
dropped 2652.6 -> 2452.6 world units (a real, meaningful pull-back, not
just present-but-negligible), while a sibling placed well inside the same
radius (30% of it) drifted only 159.2 -> 151.9 from ordinary forces and
never approached the containment boundary -- confirming the leash engages
only once genuinely warranted, not as a constant tug on everything. Full
regression sweep and `npm test` (13/13, `scene3d.js` untouched by this
specific change) re-run clean.

## Legend section headings, white trophic axis

Two small follow-up polish requests. First: the legend was one flat list of
rows, which is exactly why "only dependency/semantic are togglable" read as
an inconsistency rather than a deliberate split (see the "stargazer/
contributor/issueAuthor are NOT a legend toggle" comment above `edgeVisible`
-- that decision was already correct, it just wasn't visible in the UI).
Fixed by splitting `renderLegend()`'s output into three headed sections --
"Nodes", "Edges — toggle" (dependency, semantic: real checkboxes), and
"Edges — shown on hover/select" (stargazer, contributor, issue-poster: no
checkbox, since they're permanently-on physics forces only ever drawn when
touching the hovered/selected node) -- via a small `.section-heading` CSS
class (uppercase, muted, a hairline top border as the divider) rather than
any change to the underlying toggle logic itself.

Second: the trophic reference axis (`buildAxis()` in `scene3d.js`) was
colored from `--gridline`, a theme CSS variable deliberately chosen to be a
subtle, low-contrast hairline-divider grey -- appropriate for UI dividers,
not for a real spatial reference line competing with the rendered graph.
Changed `palette.axis` to a fixed `#ffffff` (both light and dark theme,
overriding the previous per-theme gridline lookup), plus the matching
fallback defaults in `scene3d.js` (`buildAxis`'s initial material color and
`setPaletteCore`'s `palette.axis || ...` fallback) so there's no brief
grey flash before the palette is applied.

**Verified** (same jsdom harness): `palette.axis === "#ffffff"` after
`refreshPalette()`; the legend DOM has exactly 3 `.section-heading`
elements in the expected order; exactly 2 `input.edge-toggle` checkboxes
exist (dependency, semantic) and none for stargazer/contributor/
issueAuthor; a full cluster-expansion + tick/draw + type/edge-toggle
regression pass runs clean with the new legend structure in place.
`npm test` (13/13) and `build_web_explorer.py` (same counts) both clean.

## Collapsible sidebar panels, collapsed by default except Clusters

The sidebar was six always-open panels (Path finder, Compare, Clusters,
Selected node, Predicate frequency, About this map) stacked in a fixed
column -- a wall of content on every load when usually only one or two are
relevant at a time. Made each panel's `<h2>` a click-to-toggle disclosure:
a `.collapsed` class on the panel hides everything but the header (`.panel
> *:not(h2) { display: none; }`) and swaps the `::before` caret glyph
(`▾`/`▸`). Every panel gets a `data-panel="..."` attribute so the
collapse-by-default pass at load time can name the one exception directly
rather than pattern-matching on header text: all six start collapsed
except `data-panel="clusters"`.

One wrinkle worth calling out: "Selected node" is populated from outside
its own panel (clicking a node in the 3D graph), so collapsing it by
default would otherwise make that core interaction look broken -- click a
node, nothing visibly happens because the result landed in a hidden
panel. Fixed by having `showDetail(n)` remove `.collapsed` from its own
panel whenever `n` is non-null (i.e. an actual selection, not the
clear-selection call). Left the same non-issue alone for Path finder /
Compare on purpose: their results only ever populate after the user has
already interacted with a search box that lives inside their own panel,
so there's no way to trigger them while still collapsed -- unlike
"Selected node," they don't need a special case. A permalinked path/
compare pair restored silently on load is the one case that still lands
in a collapsed panel (the highlighted path/dimming is still visible in
the graph itself either way) -- a deliberately accepted, minor gap rather
than an oversight.

**Verified** (same jsdom harness): all 6 panels present with exactly the
expected `data-panel` values; every panel collapsed on load except
Clusters; clicking a panel's `<h2>` toggles `.collapsed` on and back off;
calling `showDetail(realNode)` auto-expands the Selected-node panel;
calling `showDetail(null)` (clearing selection) leaves whatever collapsed
state was already there alone rather than forcing a collapse.
`build_web_explorer.py` re-run clean (same counts); `scene3d.js` untouched
so `npm test` is an unaffected no-op re-check (13/13).

## Cylindrical-coordinate readout on hover/click, and a real sign bug found along the way

The (y, theta, r) driving the trophic/topic layout *is* a cylindrical
coordinate system (y = height along the central axis, theta/r = polar
position in the horizontal plane) but was never surfaced anywhere in the
UI -- asked for a hover readout of the raw numbers plus a click-through
explanation of what each axis actually means. Added `cylindricalCoordsFor
(n)` (`web/template.html`, right after `layoutTargetForNode`): returns
`{y, r, thetaDeg, hasTrophic, hasTopic}` for repository/cluster nodes
(`null` for every other type, which has no axis position of its own,
matching `layoutTargetForNode`'s own null case), where `hasTrophic`/
`hasTopic` distinguish a real measured position from the documented
no-data fallback (`y=0.5`/`r=0` -- see `REPO_LAYOUT_TARGET`'s
construction) so the UI can say "no dependency data" honestly instead of
presenting a fallback as a real position. `tooltipHtml()` appends a
compact `.t-coords` line (raw numbers only -- a 320px tooltip has no room
for prose); `showDetail()` adds a "Position (cylindrical axes)" section
with one `.detail-axis` block per axis, each pairing the number with a
plain-language note (`trophicHeightNote`/`topicRadiusNote`/
`TOPIC_ANGLE_NOTE`).

**Writing the trophic-height note surfaced a real, verified sign error in
this project's own prior documentation.** `TROPHIC_Y_RANGE`'s inline
comment (and the Phase 12 write-up above, and the "pytorch/pytorch
(consumer)" example in it) claimed "top = consumer, bottom = dependency."
Checked directly rather than trusted, the same way this project's own
convention insists on (see the Phase 12 section's own "derived and
checked, not assumed" framing) -- computed `layoutWorldPos` for both
endpoints of all 863 real dependency edges (`a` depends on `b`) and
compared `worldY`. Result, unanimous across all 863: the source
(consumer) always lands at `worldY = -450` (bottom) and the target
(library) always lands at `worldY = +450` (top) -- the *opposite* of what
the comment said. The math itself (`layoutWorldPos`'s `(1 - target.y) *
TROPHIC_Y_RANGE - TROPHIC_Y_RANGE / 2`) was never wrong; only the English
description of it was stale, likely predating some later change to that
formula. Fixed the comment in place (now points at this NOTES.md section
for the check) and wrote `trophicHeightNote()`'s five buckets against the
verified direction: low `y` (near 0) = top = foundational library; high
`y` (near 1) = bottom = consumer. Did not touch the historical Phase 12
narrative above (`pytorch/pytorch (consumer) vs. pytorch/vision`) --
that's a record of an earlier design pass, not live documentation, and
rewriting it risks erasing real history over a comment that may well have
simply gone stale after a later edit; this section stands as the
correction of record.

**Verified** (same jsdom harness): `cylindricalCoordsFor` returns the
exact `REPO_LAYOUT_TARGET` values (bit-for-bit `y`/`r`, and `thetaDeg`
matching an independently-computed `theta -> degrees` conversion) for a
real repo, and `null` for a non-repo/cluster type; `hasTrophic` is
correctly `false` for a repo absent from `TROPHIC_LEVELS` and `true` for
one present; both the tooltip and the Selected-node panel include the new
sections with the expected labels, for both a repository and a cluster;
the axes section is omitted entirely for types with no coordinate axis;
and the direction check itself -- sorting every repo with real trophic
data by `y` and comparing `layoutWorldPos` of the lowest- and highest-`y`
repos -- confirms low-`y` renders higher (`worldY=450.0`) than high-`y`
(`worldY=-450.0`), matching the corrected comment. Full prior regression
suite (legend sections, axis color, collapsible panels) re-run clean
against the same rebuilt `index.html`; `npm test` (13/13, `scene3d.js`
untouched) and `build_web_explorer.py` (same counts) both clean.

**Immediate correction: the "sign bug" above was diagnosed backwards --
the real bug was in `layoutWorldPos`, not the comment.** Reported back
almost immediately: the rendered result was "visually inverted, but
technically correct" -- the raw trophic *number* (0 = pure dependency, 1 =
pure consumer) was always right, exactly as the previous section
concluded, but the previous section then trusted the *code*
(`layoutWorldPos`'s `(1 - target.y)`) as ground truth and rewrote the
comment to match it. That was the wrong direction to resolve the
disagreement in: `draw()` already renders literal on-screen axis labels
-- "consumers" at the top end, "dependencies" at the bottom end (added
during the 3D migration specifically so a rotatable view could still be
read) -- and those shipped, user-facing labels say top = consumers, which
is the *opposite* of what `(1 - target.y)` actually produced. The likely
history: this inversion made sense in the old 2D canvas renderer, where
+Y points *down*, so `(1 - y)` was needed to put high-consumer-score
nodes near a visually-low pixel-Y (the top of the canvas); three.js
world space has +Y point *up*, so that inversion should have been
dropped during the migration and wasn't.

Fixed by removing the inversion: `layoutWorldPos`'s y is now
`target.y * TROPHIC_Y_RANGE - TROPHIC_Y_RANGE / 2` (no `1 -`), so a
consumer (y near 1) now renders near the top, matching its own axis
label, and a foundational dependency (y near 0) renders near the bottom.
`trophicHeightNote()`'s five buckets flipped to match. This is a real
behavior change, not just documentation -- every repo/cluster's vertical
position in the rendered graph moves to the mirror-image height it had
before this fix.

**Verified** (same jsdom harness, re-run after the flip): the two
existing coordinate-readout tests (bit-for-bit target values, hasTrophic/
hasTopic fallback flagging) still pass unchanged, since they never
asserted a direction. Two direction-specific checks updated/added: sorting
real repos by `y` and comparing `layoutWorldPos`, the highest-`y` (most
consumer-like) repo now renders above the lowest-`y` (most foundational)
one; and, more directly, checked *every one* of the 863 real dependency
edges (not just two extremes) -- 863/863 sources (the consumer side of
"A depends on B") render with a strictly greater `worldY` than their
target (the dependency side), i.e. 100% agreement with the "consumers"/
"dependencies" axis labels, zero exceptions. `npm test` (13/13,
`scene3d.js` untouched) and `build_web_explorer.py` (same counts) both
clean; the physics/fade/containment mechanisms from earlier sections
were independently re-checked and are unaffected, since they only ever
consume whatever `layoutWorldPos` returns rather than assuming a
direction themselves.

## Cohort inflated from 319 to 1019 repos

Grown the cohort roughly 3.2x to exercise the pipeline (and the LOD
clustering from an earlier section) at a materially larger scale, prompted
directly by a quota question: `gh api rate_limit` showed the authenticated
CLI token has the standard 5000 req/hour core budget, and every live call
this pipeline makes (`scripts/10`/`12`/`17`, all via `gh api`, all cached
to `data/raw/github_cache/` or `data/raw/readme_cache/` permanently) costs
at most 1-2 requests per *new* repo, so a batch this size was cheap, not a
quota risk.

The only real lever for cohort size is `scripts/10_fetch_new_repo_stats.py`'s
`top_n_source` (a curated slice of the ~21,100-repo dependency cohort,
ranked by distinct-resolved-package degree) -- the "library" side (repos a
`usedPackage` triple resolves to) is effectively fixed at ~168 without
also widening which repos `scripts/06_used_packages.sh` runs against, out
of scope here. Raised `top_n_source` from 100 to 800. The script's
`CANDIDATE_POOL` used to be a flat constant (220, sized for a 100-repo
ask); replaced it with `CANDIDATE_POOL_FACTOR = 2.2` so the raw-id pool
tried scales with whatever `top_n_source` is asked for instead of silently
under-yielding the next time someone changes the target. Result: 168
library + 800 source = 968 new nodes (1760 candidates tried) -> 51 + 968 =
**1019 total repos**. Most of the resolution work turned out to already be
cached from earlier exploratory runs (`github_cache/` had 738 entries, 518
successful, before this run even started), so the live `gh api` spend for
the whole growth was small; `rate_limit` read back at 5000/5000 remaining
immediately after.

Re-ran the full downstream pipeline against the grown cohort, in the order
`README.md`'s Pipeline section documents: the shared-stargazer/contributor/
issue-author backfill (`scripts/05`/`07`/`23`, each a single ~20-70s pass
over the 12GB dump against the new 1019-repo pattern list) -- absolute
edge/repo counts grew (676/301/215 pruned edges over 251/259/194
participating repos, up from 151-ish/59/rare at 319) but the *coverage
fraction* of the cohort that has any of this data at all dropped further
below the already-low fractions the "Two cohorts that do not overlap"
section above measured, since the growth came entirely from the
dependency-cohort side of the dataset, which this section's earlier
finding already flagged as sparser on stargazer/contributor/issue data
than the original top-50 "famous repo" cohort. Real data, not a bug, but
worth remembering when the explorer looks emptier on these tiers than the
dependency/semantic tiers now that the cohort is 3x bigger. Dependency
edges scaled to 3765 pruned edges over 962 nodes (was 863/319); semantic
(shared-topic) edges to 745 over 281 nodes; 865 distinct owner avatars
downloaded (`scripts/08`, unauthenticated `github.com/{owner}.png`, no API
quota involved); descriptions and READMEs both >95% already cached from
the script-10 resolution pass; text embeddings covered 1008/1019 repos;
Leiden clustering produced 49 top-level clusters (123 total across all
levels) covering all 1019 repos, only 5 of which matched a previous stable
id by Jaccard similarity (expected -- the cohort nearly tripled, so most
cluster shapes are genuinely new, not the same clusters reshuffled).

**Real bug found and fixed along the way, in `scripts/22_label_clusters.py`
this time, not the explorer itself.** That script labels every
new/changed cluster with one `claude -p` subprocess call each (123 of them
here), but only ever wrote `cluster_labels.json`/`repo_cluster_hierarchy.json`
once, at the very end of the whole loop. A background run of the full
123-cluster batch got killed partway through -- twice -- and each time
silently discarded every LLM call that had already completed, because
nothing had been persisted yet. This directly contradicted the script's
own stated design goal (a docstring comment: "a rerun with unchanged
clusters costs zero LLM calls") -- true for a *clean* rerun, false for a
rerun after an interrupted one, which is exactly the failure mode hit
here. Fixed by writing the label cache after every real LLM call (not
just fallback/heuristic labels, and not the full hierarchy file, which
only matters once at the end) -- confirmed working by observing a second
run correctly pick up at 95/123 clusters already satisfied from the first
run's partial progress, finishing the remaining 28 cleanly.

**Verified** (same jsdom debug-hook pattern, against a freshly rebuilt
`web/index.html`): `REPO_LAYOUT_TARGET` (the LOD-independent full cohort)
has exactly 1019 entries; the LOD system still bounds the *initial*
on-screen/simulated node count to 341 (49 real cluster meta-nodes + 292
repos with no clustering signal shown directly -- matching
`scripts/14`'s own build-time log line exactly), not anywhere near the
full 1019, confirming the level-of-detail design from the earlier
"Level-of-detail clustering" section still holds at 3x the cohort size it
was built and measured against; `tick()` over 60 frames produces no
non-finite positions; `draw()` doesn't throw; expanding a real 263-member
cluster grows the materialized node set correctly (341 -> 353, i.e. by
its 13 immediate children, not its full flattened membership -- expanding
a cluster only ever reveals one level of the hierarchy at a time, as
designed); and all 3765 dependency edges still agree with the corrected
trophic-axis direction fixed in the section above (100%, unaffected by
this data refresh). `npm test` (13/13, `scene3d.js` untouched) and
`build_web_explorer.py` (1019 repos, 49 top-level/123 total clusters, same
shape as this section's own numbers) both clean.

One harness-only mistake caught and fixed while writing this verification
(not an app bug): the debug hook's first draft captured `nodes: nodes` --
a snapshot of the array *reference* at hook-injection time. `template.html`
reassigns the module-scope `nodes` variable itself in a few places
(`nodes = nodes.filter(...)` on removal, same pattern for `edges`), so
after any node removal the live variable points at a new array while the
snapshot still points at the old, now-stale one -- made `expandCluster`
look like a no-op (node count reported unchanged) when it wasn't. Fixed by
exposing `getNodes()`/`getEdges()` accessor functions instead of raw
snapshots, the same pattern the project's own earlier verification scripts
(e.g. `getNodesList` in the cylindrical-coordinates work above) already
used for exactly this reason.

As with every fix in this file, actual WebGL-rendered pixel output (does
the graph *look* right at 3x the node count -- label/avatar/edge density,
frame rate) has not been checked in a real browser; this remains the
standing gap in this project's jsdom-only verification ceiling.

## Cohort inflated again, 1019 to 1983 repos -- and a real GitHub rate-limit incident along the way

The initial ask was 3000 repos (`top_n_source` 800 -> 2800). Before that
run finished, `gh api rate_limit` went from 4990/5000 remaining to 0/5000
in the space of a couple of minutes -- `scripts/10_fetch_new_repo_stats.py`'s
`gh_api_repo()` had no delay between calls, so once the candidate loop
walked past the ~1760 ids already cached from the previous run and started
hitting genuinely new repos, it hammered `gh api repos/{owner}/{repo}`
back-to-back fast enough to blow through the full core budget almost
immediately -- confirmed directly (`X-Ratelimit-Used: 5000`, a plain probe
call to `repos/torvalds/linux` came back 403), not inferred. Killed the
run, waited the ~4 minutes for the window to reset, and added a fixed
`time.sleep(0.4)` after every real (non-cached) `gh api` call in
`scripts/10`, and the same fix to `scripts/12_cache_repo_descriptions.py`
and `scripts/17_fetch_readmes.py`, which make the same kind of unthrottled
sequential call and would have hit the same wall once they ran against
this many newly-added repos. Cache hits are unaffected -- the sleep only
fires on an actual network round-trip -- so reruns over an unchanged
cohort stay fast.

Also scaled the ask down to a more modest ~1000-repo addition
(`top_n_source` 800 -> 1800, not 2800) rather than immediately retrying at
the original 3x-larger size right after a rate-limit incident. Result: 168
library + 1932 source (1968 attempted, 36 dropped for no resolvable
stats) -> 51 + 1932 = **1983 total repos**. Direct evidence the caching
is doing its job: of the 3960 candidates walked this run, only 482 new
files landed in `github_cache/` -- the rest were repos already resolved
by the previous 800-source run and served for free.

Re-ran the same downstream pipeline order as the previous growth. Shared-
person edges (`05`/`07`/`23` over the full 1983-repo list): stargazer
928258 dump lines -> 484 repos with data, 1345 pruned edges (was 676/251);
contributor 20706 lines -> 505 repos, 406 pruned edges (was 301/259);
issue-author 34112 sampled issues across 366 repos, 276 pruned edges (was
215/194). Dependency edges: 21608 raw -> 7632 pruned over 1929 nodes (was
3765/962). Semantic (shared-topic) edges: 5982 raw -> 1331 pruned over 499
nodes (was 745/281). 1702 new owner avatars downloaded (1738 total).
Descriptions: 1983 total, only 12 fetched fresh (rest already cached by
script 10's own resolution pass). READMEs: 964 fetched fresh, 13 repos
confirmed to have none. Text embeddings covered 1965/1983 repos. Leiden
clustering produced 86 top-level clusters (203 total across all levels),
660 top-level entities after collapsing singletons, covering all 1983
repos; cluster-id stabilization matched 37 to a previous stable id, minted
166 fresh (expected -- membership shifted for nearly every cluster once
the cohort grew). `scripts/22_label_clusters.py`'s checkpointing (added
during the previous growth, after that run got killed mid-batch) wasn't
actually needed this time -- the ~200-cluster labeling batch completed in
one pass -- but running unmodified over a batch nearly twice the previous
size without issue is itself a mild confirmation the fix didn't introduce
new failure modes. Trophic levels: 1929/1983 repos placed, incoherence
0.010 (same tight layering as before). Topic circular embedding: 664/1983
repos get a real theta. Final build: 1983 repos, 1345/406/7632/1331/276
edges across the five tiers, 86 top-level/203 total clusters.

**Verified** (same jsdom debug-hook pattern): `REPO_LAYOUT_TARGET` has
1983 entries; the LOD system bounds the initial on-screen/simulated node
count to 660 (matching `scripts/14`'s own "660 top-level entities" build
log line exactly), not anywhere near the full 1983; `tick()` over 60
frames produces no non-finite positions (max speed 2.17); `draw()`
doesn't throw; expanding a real 428-member cluster grows the materialized
node set from 660 to 671 (its 13 immediate children); all 7632 dependency
edges agree with the trophic-axis direction (100%). `npm test` 13/13
clean. The build also exercises `web/scene3d.bundle.js`'s WebGL-unavailable
fallback path (jsdom has no real WebGL context) at this larger node count
without throwing -- expected/designed behavior, not new evidence about
real rendering. Actual WebGL-rendered pixel output at this scale still
hasn't been checked in a real browser -- same standing gap as every
other entry in this file.

## The graph goes static -- an opt-in pull-force slider, fixed WORLD_POS, and real 3D cluster volumes

With trophic height and topic angle now a real, precomputed coordinate for
every repo (Phase 12), the force-directed simulation had turned into
something actively fighting its own data: dependency/semantic edges yanked
nodes off the position their own data already implies, an ongoing
1/distSq repulsion perpetually jittered already-well-placed nodes with no
"settle" state (`tick()` runs every frame forever, not just during a
transition), and zoom-triggered cluster expand/collapse fanned children
out near the collapsing meta-node and let them visibly drift into place
over several frames. None of that is needed anymore now that a position
can just be looked up instead of simulated toward.

**Step 1 -- decouple edge force from edge visibility.** `edgeVisible.
dependency`/`.semantic` used to control both whether a tier drew *and*
whether it pulled connected repos together in `tick()`. Split those apart:
visibility stays a plain checkbox: force is now a separate `edgeForce
Strength` slider (0-1, default 0, new legend row, persisted in the
permalink hash as `#force=`). At 0 -- the default -- dependency/semantic
edges draw exactly as before but exert zero pull. `yDampedSpring()` gained
an optional `yFloor` param: when the slider is raised above 0, these two
tiers use `EDGE_FORCE_Y_DAMPING = 0.02` instead of the other tiers'
`TIER_EDGE_Y_DAMPING = 0.12` -- y is the one axis with a real analytic
answer (trophic height), so even an intentional pull should stay almost
entirely in the xz/topic plane. Global repulsion cut from a flat `2800`
coefficient to `REPULSION_STRENGTH = 120` -- kept nonzero only because
1/distSq still needs *some* coefficient to resolve exactly-coincident
spawns faster than the (now much weaker) target-homing force alone would.

**Step 2 -- freeze positions outright.** `WORLD_POS`: one fixed `{x,y,z}`
per repo/cluster id, computed once at load from `REPO_LAYOUT_TARGET`/
`CLUSTER_LAYOUT_TARGET`. Repos/clusters that fall back to the exact same
no-signal target (`y=0.5, r=0, theta=0` -- a real, common case: no
dependency edges *and* no topic tags) get spread apart with a one-time,
deterministically-ordered (sorted by id, not discovery order) Fibonacci-
sphere fan-out instead of relying on runtime repulsion -- the same repo
now lands at the same point on every load and however it's later
revealed, not wherever it happened to be relative to whatever else was
expanding at that moment. `materializeRepoNode`/`materializeClusterNode`
read straight from `WORLD_POS` instead of calling `fanPosition` live.
`tick()`'s final integrate loop now zeroes velocity and skips the position
update entirely for `repository`/`cluster` nodes whenever
`edgeForceStrength === 0` -- a materialized node spawns at its one true
position and, by default, physically cannot move again. person/issue/
forkedRepo/contributorReference (tier-2 click-to-expand samples, no fixed
coordinate of their own) are unaffected either way. Raising the slider
above 0 deliberately un-freezes repos/clusters too, so it isn't a dead
control.

**Step 3 -- cluster shape, twice.** First pass: a 2D "potatoid" drawn on
`#overlay2d` -- a screen-space convex hull of a cluster's (now-static, so
precomputable even while fully collapsed) member positions, corners
rounded via a quadratic-curve-through-midpoints trick. Shipped, then
discarded once it became clear *why* it was the wrong call: `#overlay2d`
is a separate canvas layered unconditionally on top of the WebGL canvas,
with no notion of depth at all -- it always paints over the 3D scene
regardless of what's actually in front of it from the camera, so two
overlapping clusters (or a cluster and a real node) could never occlude
correctly. Replaced with a genuine `THREE.Mesh` per cluster
(`Scene3D.syncClusterVolumes`, `web/src/scene3d.js`): a real 3D convex
hull (`ConvexGeometry`, `three/examples/jsm`) built from `clusterHullFor`'s
static leaf-repo `WORLD_POS`, `depthWrite: false` + depth test on so
translucent volumes blend instead of self-occluding while still correctly
occluding against/being occluded by real nodes. Geometry is built exactly
once per cluster id and cached forever after (positions never change --
only color/opacity/visibility get touched per frame). Clusters with fewer
than 4 members, or an exactly-coplanar point set (`ConvexGeometry` throws
on that), fall back to a padded sphere at the centroid.

The first version of that mesh had a `LineSegments`/`EdgesGeometry`
wireframe outline and flat-shaded ConvexGeometry facets -- correctly
flagged as "very old school." Fixed by rounding the hull itself:
`TessellateModifier` subdivides every face until no edge exceeds a
threshold scaled to the cluster's own bounding-sphere radius, then a
`mergeVertices` weld (needed for real adjacency -- `TessellateModifier`'s
output is non-indexed) feeds a hand-rolled Laplacian smoothing pass (each
vertex nudged toward the average of its mesh neighbors, a few rounds at a
moderate blend factor) that rounds off the leftover sharp hull corners.
Deliberately a *local* relaxation, not a blend toward a global sphere
radius -- that would have erased the cluster's real elongated/irregular
shape, the entire point of hulling real member positions instead of
drawing an abstract bubble. Measured: a near-tetrahedral 4-point test case
went from a 12-vertex raw hull to 60 after tessellation+smoothing. Timed
against the real cohort's largest cluster (428 members, `cluster/2/290`):
17.5ms; a synthetic 20-cluster mixed-size burst (mimicking a user
expanding several regions at once): 39.7ms total -- both one-time,
cached costs, not per-frame.

**Step 4 -- coloring, also twice.** First attempt tinted a cluster's
volume/ring/marker by hierarchy *level* -- wrong for the actual goal
(distinguishing what's on screen at once): every cluster at a shared
level got the *identical* color, so the common case (86 top-level
clusters, all visible together at initial load) was one indistinguishable
hue, while two clusters that genuinely are on screen together but at
different levels (a collapsed root next to an already-expanded child)
looked the same by design. Replaced with `clusterIdentityColor`, keyed by
sibling *position*: a cluster's hue comes from its index among its real
siblings (`CLUSTERS[parent].children`, or the real-cluster subset of
`CLUSTER_ROOTS` at the top), reusing the existing `clusterColor(index,
total)` scheme already used for the sidebar's cluster swatches. Caught a
real bug building this: both sibling sources legitimately mix cluster ids
with bare repo ids (a repo that never merged with anything at that level),
and leaving those in diluted the hue space real clusters needed --
unfiltered, this cohort's 86 top-level clusters were sharing
`CLUSTER_ROOTS` with 574 singleton repos, spreading hues across 660 slots
instead of 86 and rounding many of them onto the same integer hue (only
47/86 distinct colors, measured). Fixed by filtering both sibling sources
to real cluster ids only. The (index, total) pair is memoized per cluster
id (a cluster's sibling group is static for the session), but the actual
color string is *not* cached -- `clusterColor()` bakes in `isDarkTheme()`,
and caching the finished string would freeze every cluster's color at
whichever theme was active on first render, stale across a theme toggle.

**Verified** (jsdom debug-hook pattern, plus real `scene3d.test.mjs` unit
tests for the parts that need no GPU): `WORLD_POS` covers every repo and
cluster id with zero mismatches between spawn position and the static
lookup; 200-300 `tick()` calls with the slider at 0 produce *zero*
movement across all 660 currently-materialized repo/cluster nodes;
raising the slider to 1 measurably moves all 660 within 30 ticks (the
control still does something); a repo expanded, collapsed, and
re-expanded lands at the bit-identical position both times; `clusterHullFor`'s
positions exactly match `clusterLeafRepoIds`' resolved `WORLD_POS`
lookups; 86/86 top-level clusters get distinct colors post-fix (47/86
before it); a cluster's 12 direct children get 12 distinct colors among
themselves. `scene3d.test.mjs` (19/19, up from 13): a real convex hull
builds a mesh and gets added to the scene; fewer than 4 points or an
exactly-coplanar set falls back to a sphere without throwing; geometry is
built once and reused (same mesh instance) across repeated syncs of the
same cluster; a cluster missing from the next sync call is hidden, not
removed; the smoothed volume has meaningfully more vertices than the raw
hull. `npm test` clean throughout. As with every entry in this file, the
one thing none of this touches is what it actually *looks* like -- hull
shape, color legibility, translucency/depth blending -- since WebGL never
initializes under jsdom; that still needs a real browser.

## Cluster meta-nodes stop being pseudo-nodes; cluster volume readability

Two follow-up rounds on top of the previous entry's real 3D cluster
volumes, both prompted directly by a screenshot of the running app.

**Round 1 -- the ring/badge/label were a leftover, not a feature.** Once a
cluster's real member footprint renders as a translucent volume, its
meta-node was still *also* getting a small solid disc sprite (a marker,
same as a repo gets), a colored ring, a member-count badge, and an
always-on name label drawn every frame regardless of camera motion --
four representations of the same cluster stacked on screen at once, the
ring/badge/label all redundant with what the volume already showed
(shape, and now, hover-revealed count). The always-on label was also a
real per-frame cost: `measureText`/`roundRect`/`fillText` for every
visible cluster, every frame, whether the camera moved or not.

Fix: `_hideMarker`, a flag stamped onto a render-node in `template.html`
and read by `scene3d.js`'s `syncCore` to skip drawing that node's sprite
(the object stays in the pool for position/pick bookkeeping, just
`visible = false`). Set for a collapsed cluster meta-node and an
expanded-cluster halo -- both already have a real volume backing them --
but deliberately *not* for a fading-out LOD-transition ghost, which has
no volume of its own to fade alongside and still needs its own marker for
the whole fade. The ring+badge block was deleted outright. A cluster's
name now only draws while it's the hovered node (matching how its full
stats already only ever showed in the hover tooltip, `tooltipHtml`'s
cluster branch, unchanged) -- not tied to on-screen size or selection
the way a repository's label is, since a cluster is never `selectedNode`
(clicking one force-expands instead, see `selectNode`).

**Round 2 -- overlapping volumes and crowded labels.** With hard-coded
pseudo-nodes gone, a screenshot at a real zoom level showed the volumes
themselves as the actual problem: many overlapping, mostly-spherical-
looking translucent blobs with no readable structure, plus a dense region
showing dozens of overlapping repo name labels. Chasing this with the
jsdom debug-hook harness extended to expose `clusterHullFor`/`WORLD_POS`,
plus a *separate* offline analysis script built on the same real,
unbundled `scene3d.js` module and a real `THREE.PerspectiveCamera` (no
GPU needed for the pure projection math) found three distinct, real
causes, not one:

1. *A handful of clusters are genuinely not spatially coherent.* Real
   clusters are grouped by a graph signal (shared stargazers/deps/topics)
   that doesn't require spatial locality in the trophic/topic embedding.
   Measured against the real cohort: the 15 largest hull radii ranged
   723-853 world units -- comparable to the whole graph's own extent
   (`TROPHIC_Y_RANGE` 900, `TOPIC_R_SCALE` 320) -- while every cluster
   with <=5 members (the large majority of the 203) had a radius entirely
   set by its single farthest point. `clusterHullFor` now trims each
   cluster to its 90th-percentile-by-distance members (never below 4)
   before hulling, so a few far-flung outliers can't balloon the shape:
   the worst cases shrank from 853/842/828 to 266/69/218, while small
   clusters (already <=5 points, so the 90th-percentile point already
   *is* the farthest one) were untouched -- confirmed directly against
   the real data, not just reasoned about.

2. *LOD expand/collapse was judged by the wrong radius.* `checkLodTransitions`
   compared on-screen projected size against `clusterRadius(memberCount)`
   -- a small fixed marker radius (14-40 world units, log-scaled purely by
   member count) with no relationship to the volume actually drawn. A
   cluster whose real footprint spans most of the visible scene could sit
   at a 22px marker projection, nowhere near the old `EXPAND_PX=70`, while
   dominating the screen. Switched both the expand and collapse checks to
   `clusterHullFor(id).radius` (the trimmed volume radius from fix 1).
   Simply swapping the metric under the old 70/40 thresholds would have
   auto-expanded roughly half the cohort simultaneously on first load
   (measured: 70/203 clusters already exceed 70px trimmed-radius
   projection at the initial `fitView()` distance) -- so `EXPAND_PX`/
   `COLLAPSE_PX` were retuned to 150/90 using the same offline camera-math
   script: a median cluster's trimmed hull radius projects to ~53px at
   the initial full-graph view, growing smoothly on zoom (~88px at 60% of
   that distance, ~132px at 40%), and 150px leaves all but ~15 of the 203
   clusters collapsed at first load while still responding to a real,
   moderate zoom into any one region.

3. *Nothing capped how many volumes could render at once.* Even with 1-2
   fixed, up to 86 top-level cluster volumes (or more, once several are
   expanded and their still-faint-but-present halos accumulate) could be
   simultaneously in view. `clusterVolumeList` is now built from a ranked
   candidate list -- every candidate's current `Scene3D.projectedRadius`
   (using its real hull radius) -- and only the top `CLUSTER_VOLUME_MAX_VISIBLE`
   (18) actually get drawn; the rest are simply omitted from the list
   Scene3D is handed, which hides their already-cached mesh rather than
   rebuilding anything. Prominence-ranked, not distance- or count-based,
   so the visible set tracks whatever region the camera is actually
   looking at.

Separately, a real cause of the reported "degenerate flat clusters":
`ConvexGeometry` only throws on *exact* coplanarity: a near-coplanar-but-
technically-3D member set (plausible whenever a topically-tight cluster's
members happen to share nearly the same trophic height) hulls into a
valid, non-throwing, paper-thin pancake -- never hitting the sphere
fallback, just reading as a flat sliver. `scene3d.js` gained
`ensureMinimumSpread`, checked per world axis (not a true PCA "flattest
direction" -- x/y/z here are already meaningful independent axes, not
arbitrary ones) and applied before `inflatePoints3D`/`ConvexGeometry`:
any axis whose extent falls under `MIN_HULL_AXIS_EXTENT` (50) gets its
points pushed further from centroid along just that axis until it
doesn't, leaving already-healthy axes untouched.

Crowded repo labels (the other half of the same screenshot -- dozens of
overlapping repo names once zoomed into a dense region) were diagnosed
but not yet fixed here: repo labels are still drawn unconditionally for
every node crossing the existing on-screen-size/hover/select gate, with
no collision avoidance between them. Left as a known follow-up rather
than folded into this round, which focused on the volume-readability
half of the complaint.

**Verified**: jsdom debug-hook harness, extended this round to expose
`clusterHullFor`/`clusterLeafRepoIds`/`WORLD_POS`/`expandedClusters`/
`checkLodTransitions`, combined with a *second* verification script that
wires the app's fake `Scene3D` to the real `frameNodesCore`/
`projectedRadiusCore`/`projectToScreenCore` from the real `scene3d.js`
module and a real `THREE.PerspectiveCamera` -- so `fitView()`'s actual
initial camera framing and every LOD/volume-cap decision run against real
perspective math, not a stand-in constant. Results: exactly 18 cluster
volumes in the list at the initial view (the cap holding, was unbounded
before); `cluster/2/291`'s production `clusterHullFor` radius measures
265.8, matching the offline analysis exactly; every <=5-member cluster's
hull keeps all its leaf positions; 18 clusters auto-expand after 30 ticks
at the initial view (not a mass expansion); dollying to 25% of the
initial distance grows that to 92 while the volume cap still holds at 18;
the static-map zero-movement property (previous entry) still holds after
all of this LOD churn, checked across 1840 nodes post-churn. `scene3d.test.mjs`
gained a dedicated case for the min-thickness fix: a 6-point near-flat set
(real x/y spread, ~2-unit z jitter) inflates to a 51.7-unit z extent while
its x extent is left within its real ~300-unit spread. `npm test`: 20/20.

## A per-language quota on the "source repo" growth stream

Prompted by a direct question: after three growth rounds (51 -> 319 -> 1019
-> 1983) driven entirely by `scripts/10_fetch_new_repo_stats.py`, how skewed
had the cohort's language mix gotten? Checked directly against
`data/raw/github_cache`: of 5676 cached repos, 83.2% are `Python` and 12.1%
`Jupyter Notebook` -- 95.3% Python-family. The original 51-repo seed (ranked
by language-agnostic `hasTotalStargazers`) was only 69% Python by the same
count, with real spread (C++, Java, TypeScript, Lua, Swift, C).

The mechanism, not just the ranking cutoff, is why: the `usedPackage`
signal this script's "source repo" stream ranks by is PyPI-only (confirmed
in `09_resolve_packages.py`), and per the "Two cohorts that do not overlap"
entry above, the ~21,100-repo dependency cohort it draws from already
"looks paper/research-code linked" -- i.e. it's an ML-research corpus by
construction, not a general GitHub sample. Raising `top_n_source` just cuts
further down the same PyPI-dependency-degree ranking within that corpus, so
it can only ever reinforce the skew, never correct it.

Fix (scope: future growth rounds only -- the existing 1983-repo cohort and
the "library" stream, which is unconditional real dependency edges rather
than a ranked popularity choice, were both left alone): `LANGUAGE_QUOTA_CAP`
(0.5) in `scripts/10_fetch_new_repo_stats.py` caps any single language
bucket at 50% of a round's new source-repo slots; once a bucket is full,
further candidates of that language are skipped (not counted toward
`top_n_source`) and the loop keeps walking the same ranked candidate list
for other languages. `Jupyter Notebook` is folded into the `Python` bucket
(`LANGUAGE_GROUPS`) -- capping them separately would let Python-family
repos still fill ~100% of slots between the two buckets, defeating the
point.

Dry-run (cache-only, no network, no writes -- reran the real
candidate-selection loop standalone against the existing
`data/raw/github_cache` + `repo_package_degree.txt` for `top_n_source=1800`,
matching the current cohort): uncapped reproduces today's 95.4%
Python/1800-of-1800 exactly; capped drops Python-family to 83.7% but only
reaches 1075 of the 1800 target (2847 candidates skipped by the cap). Also
checked the full 21,100-repo pool cache-only (4099 of it already resolved
across prior runs): still 95.5% Python-family, so the shortfall isn't an
artifact of the ~3960-candidate slice this script currently scans -- the
non-Python supply in this specific dependency cohort is thin everywhere,
not just at the top of the ranking. Deliberately did not widen the
candidate-pool scan to chase the full 50% target: the full pool has
~14,953 raw ids never yet resolved, and finding enough non-Python repos
among them to fill a 900-slot quota would cost roughly that many fresh
`gh api` calls (hours, and real risk of retripping the same secondary
rate limit from the 1019->1983 growth commit) for a corpus that may not
even contain 900 non-Python repos. Left the cap as a real ceiling that can
legitimately undershoot `top_n_source` rather than silently paying that
cost by default -- next growth round's operator can choose to widen the
scan (expensive, thorough) or bring in a second, independent candidate
stream off `hasTotalStargazers` (cheap-ish, is what gave the original seed
its better-but-still-ML-skewed mix) if closer to a real 50/50 split is
wanted.

## Crowded repo labels: collision avoidance, not just a per-node gate

The follow-up flagged at the end of the previous entry: a node's own
on-screen-size/hover/select gate decides whether it *wants* a label, but
says nothing about whether there's room for it -- zooming into any dense
region (exactly the other half of the screenshot that prompted the
cluster-volume work) routinely puts dozens of individually-qualifying
labels in the same small area, unreadable regardless of how correct each
one's own gate is.

`draw()`'s label pass is now two steps instead of one. The main node loop
collects candidates instead of drawing immediately, each tagged `forced`
if it represents an explicit point of user focus (selected, hovered,
external, on the highlighted path/compare pair -- a cluster only ever
becomes a candidate at all while hovered, so it's always forced) or not
(a plain repo qualifying by on-screen size alone). After the loop, sorted
forced-first then biggest-on-screen-first, each candidate's estimated box
(`ctx.measureText` width, a fixed height) is greedily placed: a
non-forced candidate that would overlap an already-placed box is simply
skipped, not shrunk or repositioned; a forced one always draws regardless
(but still registers its box, so it in turn crowds out lower-priority
candidates behind it). This is what actually turns "every technically-
qualifying label" into "the ones that fit" -- suppressing the label
doesn't touch the node's fill/ring/volume, only its text.

**Verified**: jsdom harness wired to the real `scene3d.js` projection math
and a real `THREE.PerspectiveCamera` (same technique as the previous
entry), with `ctx.fillText`/`ctx.measureText` spied so drawn labels'
actual screen rects could be reconstructed and checked pairwise. At the
sparse initial view: 4 labels drawn, 0 overlaps (baseline). Expanding a
50+-member cluster and dollying the camera in close to its centroid: 574
repo nodes individually qualified for a label under the old per-node
gate, only 195 were actually drawn, and 0 overlapping pairs among them --
confirming both that this scenario creates real crowding and that the
placement pass resolves it. A hovered node's label still always draws
even amid that same crowding. The static-map zero-movement property still
holds (671 nodes checked). `npm test`: 20/20 (unchanged -- this round
didn't touch `scene3d.js`).

## Rotating-camera video export, and Playwright replaces the no-browser-automation preference

Cohort growth since Phase 18 (319 -> 2983 repos across the two growth
rounds above) made the live view visibly laggy, which prompted a request
for an offline rendering instead of a live-performance fix: a toolbar
button (`btnExportRotation`) that sweeps the camera one full 360 degrees
around the trophic (Y) axis, starting from whatever the camera currently
frames, and downloads a WebM.

**Why this sidesteps the live-lag problem entirely instead of fixing
it.** The live loop's actual cost (unfixed, and out of scope here) is
that `tick()`'s O(n^2) repulsion pass and `draw()`'s `Scene3D.sync()`
edge-geometry rebuild both run unconditionally every animation frame,
even though `WORLD_POS` already freezes repo/cluster positions by
default (see the "graph goes static" entry above) -- pure waste once
the camera is the only thing moving. Rather than touch that hot path,
the export drives its own loop instead of the live one: step the camera
to the next angle via two new pure functions
(`Scene3D.getOrbitState()`/`setOrbitCamera()`, spherical coordinates
around `controls.target`, bypassing `OrbitControls` entirely since it
only ever moves the camera in response to real pointer input), call the
existing `draw()`, composite the WebGL canvas + label-overlay canvas
into one frame, then push that exact frame into a `MediaRecorder` via
`canvas.captureStream(0)` + `track.requestFrame()`. That API only pushes
a frame when explicitly told to, so it doesn't matter whether a given
`draw()` call takes 5ms or 20s -- the output video has zero dropped or
stuttered frames regardless. A slow machine just makes the export take
longer wall-clock time to produce, never a worse-looking result.

**Playwright adopted, replacing the "no browser automation" standing
preference (see the 3D-migration entry above and `[[no_playwright_
verification]]` in memory).** That preference existed because there was
nothing to gain from it: `headless-gl`, Node's only headless WebGL
option, can't provide the WebGL2 context three.js requires, so no
automated tool could touch a rendered pixel either way -- the choice was
never "jsdom vs. Playwright," it was "jsdom vs. nothing." A real headless
Chromium via Playwright has a genuine WebGL2 context, closing that gap
for the first time, and the user asked directly for the convention to
change now that there's something real to gain: `playwright` is a
committed `web/` devDependency, `web/tools/screenshot.mjs` (loads the
built `index.html`, optional `--eval` snippet to drive interaction,
saves a PNG) is the AI's own first-pass verification tool now, instead
of the old pattern of asking the user to eyeball a real browser first.

**It found a real bug immediately.** First attempt: screenshotting
mid-export timed out. Cause: the scripted export loop was running
*concurrently* with the still-running live `requestAnimationFrame(loop)`
-- both were calling `draw()`/`Scene3D.render()` against the same canvas
at once, and the live loop's own `checkLodTransitions()` reading the
moving camera position mid-export could auto-expand/collapse clusters as
the scripted rotation swept past them. Fixed with an `exportInProgress`
flag `loop()` checks before doing any work.

**A second, wrong fix shipped anyway, and this is the more important
lesson from this entry.** The per-frame pacing used `requestAnimationFrame`
to yield between frames; under headless Chromium this looked
near-stalled (a 240-frame run barely reached ~5% in 4 minutes), which
read as rAF's well-known hidden-tab throttling, so the fix was to switch
to `setTimeout(resolve, 0)` -- no visibility-linked throttling, and this
loop only needs to yield back to the event loop, not sync with a paint,
or so the reasoning went. That reasoning under-weighted *why* rAF pacing
matters here: `track.requestFrame()` captures whatever the canvas's last
real *paint* produced, not just its current 2D bitmap on demand. On a
real GPU browser `draw()` can complete in a few milliseconds, so a
`setTimeout(0)`-paced loop can race through many iterations *faster than
the browser actually repaints* -- every `requestFrame()` call ends up
re-capturing the same stale composited image. The user downloaded the
resulting WebM and reported exactly that: opens fine, but "no video, only
a still image" -- a real, valid container (VLC opened it without
complaint) with only one genuine visual frame ever actually inside it.
Headless verification's own screenshot-timeout artifact produced a
plausible-sounding but wrong diagnosis, and shipped a regression that a
human only caught by actually trying the output. Reverted to
`requestAnimationFrame` (the same technique CCapture.js-style canvas
recorders use, for exactly this reason) and accepted the real tradeoff
instead of engineering around it: a backgrounded tab makes the export
slower, which is fine, vs. a foreground tab producing a broken video,
which isn't. Also hardened `captureStream()`'s reliability defensively:
the composite canvas was created via `document.createElement` and never
attached to the DOM, which isn't guaranteed to participate in the
browser's real paint pipeline the same way an attached one does --
attached now (invisibly, `opacity:0`), removed again once the export
finishes.

**Verified end-to-end twice** -- once for the original two bugs, again
after the pacing revert -- with a real download, not just a screenshot:
temporarily shrank `ROTATION_FPS`/`ROTATION_DURATION_SEC` from 30/8 down
to 6/1 (software-rendered WebGL under headless Chromium is dramatically
slower than a real GPU with this scene's translucent cluster-volume
meshes and custom Line2 edge shaders, so 240 real-paced frames is
impractically slow for a quick check) and drove a scratch Playwright
script (not committed) that registered a `page.waitForEvent("download")`
listener *before* clicking the button (a fast export can otherwise finish
and fire the download before a listener attached afterward would ever
catch it -- a second, smaller mistake in the verification script itself,
not the app) and awaited it. The post-revert run produced a valid 30KB
WebM (`ffprobe`: VP9, 1120x790); `ffmpeg` frame extraction plus per-frame
`md5sum` showed 5-6 genuinely distinct captured images across the
sequence (not one hash repeated throughout), and two of those frames
pulled and eyeballed directly confirmed real camera rotation between
them (the trophic-axis label and cluster-blob screen positions are
completely different frame-to-frame). Restored the 30/8 defaults and
rebuilt `index.html` afterward -- the shrunk constants were
verification-only, never meant to ship. `npm test`: 23/23 (3 new cases
for `getOrbitState`/`setOrbitCamera`, the rest unchanged) -- unaffected
by any of this, since the pacing bug was never reachable from pure math.

**Two more real bugs, found by the user's next real download -- and a
gap in how the previous verification pass read its own evidence.** The
user tried the pacing-reverted export and reported it back precisely:
"now it rotates, but frames are stacking and the video end time is still
not computed."

*Frames stacking.* `Scene3D.initCore` renders the WebGL canvas with a
transparent clear color (`alpha:true`, `setClearColor(0x000000, 0)`) so
the page background shows through empty space in the live view. The
export's per-frame composite step (`compositeCtx.drawImage(canvas, 0,
0)`) never cleared the composite canvas first -- so every frame only
*added* opaque pixels on top of every previous frame's, accumulating
into one long-exposure trail across the whole rotation instead of 240
separate images. Fixed with a `compositeCtx.clearRect(...)` before each
frame's draws. In hindsight, the previous entry's own verification screenshot
(the very first one, before any of these fixes) already showed this --
a fan of duplicated axis end-cap lines and a doubled "consumers" label --
and it was misread as evidence of rotation rather than a stacking
artifact. The `md5sum`-per-frame check that "confirmed" 5-6 distinct
images was a real check that genuinely passed, but distinctness alone
doesn't rule out cumulative stacking: a trail that keeps growing also
produces a new hash every frame. Re-verified this round by actually
looking at extracted frame *content* (not just hashing it) and
confirming the background outside the graph shapes was clean black in
every frame, not an accumulating wash.

*Video duration not computed.* A known, well-documented `MediaRecorder`
limitation, not specific to this app: WebM output has no duration in its
container metadata at all (the format is designed around live streaming,
where duration isn't knowable upfront), so players show an unknown/NaN
duration until fully loaded, if ever. This was visible in this session's
own earlier `ffprobe` output the whole time (`duration=N/A`,
`nb_frames=N/A`) without being connected to the cause. Fixed with
`fix-webm-duration` (npm, zero dependencies, MIT, exists specifically
for this exact `getUserMedia`/`MediaRecorder` pitfall): added as a real
dependency in `web/package.json`, but delivered the way the library's
own docs recommend for a plain page -- its single self-contained file
copied as a committed sibling asset (`web/fix-webm-duration.js`, copied
from `node_modules/` by the `build` npm script, same "build output
committed, referenced via a literal `<script src>`" pattern as
`scene3d.bundle.js`) rather than routed through esbuild, since it
already registers `window.ysFixWebmDuration` on direct inclusion and
needs no bundling. The real wall-clock time actually spent recording
(`performance.now()` delta around `recorder.start()`/`stop()`) is passed
in -- not the nominal `ROTATION_DURATION_SEC` target, since headless/
slow-machine runs take measurably longer in practice and the container
should reflect what actually happened, not the requested length.

**Re-verified end-to-end a third time**, same shrunk-constants headless
technique: `ffprobe`'s `[FORMAT] duration=` field went from absent to a
real value (7.35s for a 1-second-target/6-frame headless run -- correctly
reflecting how long headless software rendering actually took, not the
nominal target), and extracted frames were individually inspected (not
just hashed) to confirm each one shows only its own camera angle's
content against a clean black background, no residue from earlier
angles. Restored 30/8 defaults, rebuilt `index.html`, `npm test`: 23/23
unchanged.

**A fifth real bug, and a real bug introduced by my own diagnosis of
it.** The user's next report: "now it rotates, but frames are stacking
and the video end time is still not computed" -- i.e. both of the
"fixed" bugs above were still present in the version they'd actually
downloaded. Re-read carefully: this download happened *before* the
stacking/duration fixes existed to be tested, not after -- the user's
report crossed with the fix in flight. (Worth noting explicitly since it
looked, for a moment, like the stacking/duration fixes hadn't taken;
they had, this was a stale build being described.)

The next report, on the actually-fixed build: "video starts but gets
still at some random point." This is the real fifth bug. The export
loop advanced one output frame per `requestAnimationFrame` tick, but rAF
fires at the *display's* refresh rate (60/120/144Hz on plenty of real
monitors) -- not at `ROTATION_FPS`. So the loop was silently pushing
`requestFrame()` calls 2-5x faster than the declared 30fps on any such
display. A sustained `requestFrame()` burst well above what
`CanvasCaptureMediaStreamTrack`'s encoder can sustain in real time is a
known way for it to quietly stop delivering new frame content while the
recording's timeline keeps advancing -- exactly "plays fine, then
freezes, but the video is still the right length."

First attempted fix: gate the per-frame wait on real elapsed time
instead of raw tick count --
```js
nextFrameAt += frameIntervalMs;
while (performance.now() < nextFrameAt) { await rAF-tick; }
```
This was **itself a new, worse bug**, caught only because this session's
own headless re-verification produces a near-empty 110-byte file instead
of the expected ~30KB (`chunks.length === 1`, `sizes: [110]` -- a bare
EBML header, no real Cluster/frame data at all, confirmed by adding
temporary debug logging rather than guessing). Mechanism: a `while`
loop's body doesn't run at all once `performance.now()` is already past
`nextFrameAt` -- which is *immediately* true on every iteration after the
first, since a single real rAF tick (~1.2s under this session's headless
software rendering, and generally: any tick slower than
`1000/ROTATION_FPS`) already overshoots the schedule, and the schedule
never recovers because `nextFrameAt` keeps advancing by a fixed
`frameIntervalMs` regardless of actual elapsed time. Net effect: after
frame 1, every subsequent `requestFrame()` call happened back-to-back
with *zero* real paint yields between them -- the exact "no real paint
between captures" failure mode the rAF-pacing fix existed to prevent in
the first place, just reintroduced through the back door, and this time
severe enough to produce no encoded data at all rather than one
repeated frame. Fixed with `do { await rAF-tick; } while
(performance.now() < nextFrameAt);` -- a `do-while` guarantees at least
one real paint between every capture unconditionally (correctness), while
still capping the push rate to `frameIntervalMs` when the machine is fast
enough to get ahead of it (the actual point of this fix). Re-verified
with the same debug-logging technique: `chunks` back to real encoded
size (35KB for the 6-frame case, up from 110 bytes), 5-6 genuinely
distinct frames again, clean backgrounds, no stacking.

Same honest caveat as the earlier pacing bug applies doubly here:
headless/software-rendered Chromium is *slow enough that it cannot
reproduce the failure condition at all* (a real tick under headless
already exceeds `frameIntervalMs` by 5-10x, so even the broken `while`
version "worked" in the sense of not obviously erroring -- it just
silently produced a near-empty file, which is why this needed debug
logging to actually see, not just a visual check). The fix is justified
by reasoning about the mechanism, verified not to *break* anything
headlessly, but whether it actually resolves the freeze on the user's
real (fast) machine is something only their next real download can
confirm -- flagged to them explicitly rather than claimed as proven.

## Making the top-level view actually usable: an ~8x measured frame-cost cut, and a camera angle that was quietly hiding the whole point of Phase 18

The user's report on the live app at its current (2983-repo) scale: "the
top level is borderline useless: very lagged, and there is almost no
labeled information to see." Both halves turned out to be real,
diagnosable bugs, not just "there's a lot of data now" -- found by
measuring rather than guessing, same standing convention as everything
else in this file.

**Diagnosing "very lagged" needed a new profiling technique.** Naively
timing `requestAnimationFrame` cadence in headless Chromium is
meaningless -- rAF is throttled hard on a backgrounded/headless tab (the
same confound already documented above for the rotation-export work), so
wall-clock rAF gaps mostly measure headless's own throttling, not the
app's real per-frame cost. Fix: `web/tools/profile.mjs` intercepts the
app's *first* `requestAnimationFrame` registration (its `loop` callback)
via `page.addInitScript()` before any page script runs, then calls that
captured function directly in a tight synchronous loop with
`performance.now()` around each call -- a true CPU-bound measurement,
independent of display-refresh scheduling entirely. `web/tools/
cpuprofile.mjs` does the same capture but wraps it in a CDP
`Profiler.start()/stop()` session for a full self-time breakdown by
function. Both are committed as general-purpose perf tooling, same
precedent as `web/tools/screenshot.mjs`.

Baseline: `loop()` (one `tick()` + `draw()` + `checkLodTransitions()`)
cost 450-1000ms per call at the default ~660-materialized-node top-level
view -- roughly 1-2fps. Two real, independent, additive causes:

1. **`scene3d.js`'s `syncEdgeTier()` rebuilt every edge's WebGL geometry
   every single frame, unconditionally** -- full `CatmullRomCurve3`
   construction + sampling for every bundled dependency edge, plus a
   `LineGeometry.setPositions()`/`computeLineDistances()` buffer upload
   for every edge in every tier, regardless of whether anything about the
   scene had actually changed since the last frame. The graph is static
   by default (repo/cluster nodes are frozen once `edgeForceStrength`
   is 0 -- see "The graph goes static" above), so with nothing expanded
   and nothing dragged, *none* of this needs to happen more than once,
   ever -- only the camera orbits. Fixed by caching each pooled `Line2`'s
   last-synced route (endpoint/waypoint coordinates, stored in
   `lineObj.userData._routeSig`) and skipping the geometry rebuild when
   it's byte-for-byte unchanged; material color/opacity/linewidth still
   update every frame unconditionally (hover/selection dimming is
   legitimately per-frame). The comparison is on real coordinates, not a
   node-type allowlist, so a genuinely-moving edge (a dragged node, an
   individual-sample node still settling under real physics) keeps
   updating exactly as before -- verified directly: CPU-profiled
   `getPoint`/`initNonuniformCatmullRom` (curve sampling) dropped out of
   the top-25 self-time list entirely, and wall-clock median `loop()`
   cost fell from ~455ms to ~144ms (profile.mjs, median of 20 calls) from
   this change alone.
2. **`tick()`'s O(n²) repulsion loop, and the `contribEdges`/
   `issueAuthorEdges` spring loops, computed forces between every pair of
   nodes every frame -- including pairs where *both* nodes are
   permanently frozen repo/cluster nodes**, whose velocity gets
   unconditionally zeroed at the bottom of the same function whenever the
   pull-force slider is 0. That's ~217,000 wasted pair computations per
   frame at this cohort's ~660-node top-level scale, forever, even fully
   settled, for a value guaranteed to be thrown away on both sides.
   Fixed by partitioning `nodes` once per tick into `dynamicNodes`
   (anything that isn't a frozen, non-dragged repo/cluster node) and
   `staticNodes`, then only computing dynamic-dynamic and dynamic-static
   pairs (skipping static-static entirely) -- `isFrozenNode(n)` names the
   exact freeze condition already used at the bottom of `tick()`, so the
   two can't drift out of sync. Same "skip if both sides are guaranteed
   to discard the result" logic applied to the two edge-spring loops that
   run unconditionally regardless of the slider. At the default view (no
   samples expanded, nothing dragged), `dynamicNodes` is empty and the
   entire O(n²) loop now costs zero; verified two ways: (a) `tick()`
   disappeared from the CPU profile's top-25 self-time list entirely
   (previously ~50ms/call); (b) a direct before/after position check
   (temporarily exposing `nodeById` on `window`, since `tick()`'s
   internals aren't reachable from outside its closure) confirmed a
   node's position is still bit-for-bit identical across 5 manually-driven
   ticks with the slider at 0, and genuinely changes once the slider is
   raised above 0 -- the exact same freeze/unfreeze behavior as before,
   just without the wasted computation.

Combined: wall-clock median `loop()` cost fell from ~455ms to ~55ms
(profile.mjs), roughly 8x, measured under headless Chromium's SwiftShader
software renderer -- itself a real caveat worth stating plainly: SwiftShader
dominates the *absolute* numbers here (85-92% of CPU-profiled time sits in
an unattributable native `(program)` bucket, confirmed via
`WEBGL_debug_renderer_info` to be `SwiftShader Device`, not a real GPU), so
these headless numbers are not a stand-in for the user's actual frame rate
on real hardware. What *is* representative regardless of GPU: the JS/CPU
work eliminated by both fixes above (curve math, buffer uploads, O(n²)
physics) is real work that was happening on every render path, hardware or
not -- cutting it is a genuine improvement on the user's own machine too,
just not one this environment can put an absolute fps number on.

**Diagnosing "almost no labeled information" found two compounding
causes, one cosmetic-looking but load-bearing.**

First: `draw()`'s label-candidate loop only ever pushed a cluster
meta-node's name onto the label pass when that exact cluster was the
currently-hovered node (`n.type === "cluster" && n === hoveredNode`).
With nothing hovered -- the default state of a freshly-loaded page --
*zero* cluster names rendered, while the large translucent cluster
*volumes* (up to `CLUSTER_VOLUME_MAX_VISIBLE` = 18 of them, easily the
single most visually dominant shapes on screen at this cohort's scale)
rendered fully unlabeled. Fixed by reusing the same real screen-space
prominence (`_screenPx`, each candidate's hull radius projected to
pixels) that already decides which cluster volumes get drawn at all:
every cluster with an actual rendered volume becomes a label candidate,
sorted into the same greedy overlap-avoidance pass plain repo labels
already go through (see "Crowded repo labels" above) rather than forced
to always draw -- the first version of this fix *did* mark them forced
(same treatment as a selected/hovered repo), which was wrong: a
screenshot of the "consumers" trophic band, which has several clusters'
volumes on screen simultaneously, showed 4-5 cluster names stacked
directly on top of each other, illegible. Switching to priority-by-real-
prominence instead of an overlap bypass fixed it -- confirmed by
screenshot: "Pose and Anomaly Detection," "Large Language Models," and
"Instance Segmentation" now render as three distinct, non-overlapping
labels in what used to be one unlabeled blob.

Second, and the more consequential one: **the default camera position
was `(0, 0, 1400)` -- looking straight down the -Z axis.** The topic-
circle embedding's depth component (`r*sin(theta)`) runs along Z; it's
the exact coordinate Phase 18's whole migration to three.js/WebGL exists
to expose (see "Migrating to a real 3D view" above -- the old 2D
renderer computed this value and never used it). A camera that starts
staring straight down that axis collapses it to zero apparent spread on
screen, and `fitView()`/`frameNodesCore()` only ever change camera
*distance*, deliberately preserving whatever direction the camera
already has (see their "Both preserve the camera's current azimuth/
elevation" comment) -- so an axis-aligned start stayed axis-aligned
forever, on every load, unless a visitor happened to manually drag-orbit
first. In effect: the default view of the new *3D* graph still looked
almost exactly like the old 2D projection's flat, dumbbell-shaped
silhouette (two trophic bands connected by a straight vertical column),
because the one dimension that would have shown otherwise was pointed
directly at the viewer. Fixed with a one-line change to `initCore()`'s
initial `camera.position.set(...)`, from `(0, 0, 1400)` to a three-
quarter angle (`(790, 435, 1147)`, ~35 degrees azimuth / ~18 degrees
elevation, same distance) -- confirmed by screenshot: the dependency-edge
bundle now visibly fans out in real 3D (curved, not a flat vertical
band), and previously-coincident node clusters at the bottom trophic
band show genuine depth separation from each other instead of
overlapping in a single screen-space column.

**A smaller, related fix with a more modest measured effect:**
`frameNodesCore()`'s bounding sphere (used by `fitView()`) was computed
from materialized *node* positions only -- for a collapsed cluster
meta-node, that's one small representative marker point, not the true
spread of its real members, which `clusterHullFor()` already computes
separately (and larger) for the volume mesh itself. Added
`frameBoundingSphereCore()`/`Scene3D.frameBoundingSphere(points, margin)`,
a variant that takes raw `{x,y,z}` points instead of node ids, and
changed `fitView()` to pass every visible cluster's real (outlier-
trimmed) hull leaf positions instead of just its marker point. Measured
effect was real but modest at this cohort's actual geometry -- the
computed bounding-sphere radius grew from ~517 to ~559 world units
(~8%), not the dramatic under-framing originally hypothesized reading
the screenshot; a repo node materialized at each rendered cluster's
approximate extent already did most of the work of defining the
overall bounding box on its own. Kept anyway: it's strictly more correct
(frames what's actually drawn, not a proxy for it), and matters more as
the cohort grows and cluster hulls make up a larger fraction of what's
on screen at once.

**A real, honest data-completeness gap found along the way, deliberately
not fixed here.** `scripts/24_fetch_js_ecosystem_repos.py`
(`a34c676`, "cap PyPI-stream language dominance, add 1000 JS-ecosystem
repos") added 1000 repos to `dependency_repo_aggregates.json`, which
`build_web_explorer.py` merges into the graph's node set -- but that
commit's own message says plainly it did not rerun the downstream
pipeline (descriptions, embeddings, clustering, trophic/topic
coordinates). Checked directly against the current build: of 2983 total
repos in `AGGREGATES`, exactly 1000 are absent from
`repo_cluster_hierarchy.json` entirely -- neither a cluster's child nor
in `topLevelIds` -- and have no entry in `repo_trophic_levels.json` or
`repo_topic_circular.json` either, since neither of those was rerun
since the 1983-repo snapshot (`d3bf165`). These 1000 repos have real
aggregate stats (stars/forks/language) and are addressable by id, but
sit outside the materialized top-level node set the LOD system walks --
in practice, invisible in the default render rather than incorrectly
placed. Left as-is rather than guessed at (assigning them a synthetic
trophic/topic position would violate this project's standing "everything
shown is a real, derived value, never a guessed one" principle -- see
Phase 10/15's own framing of the same issue for the pre-existing 574
no-clustering-signal repos) -- the real fix is rerunning the pipeline
steps `a34c676` already named as deferred (`scripts/12`, `17`, `18`,
`13`, `14`, `21`, `22`, `15`, `16`), not something to patch around in the
frontend.

Full regression check: `npm test` (23/23, unchanged -- none of these
functions are covered by the jsdom suite, which deliberately excludes
`init()`/`render()`/`sync()` and everything inside `template.html`'s
closure; see that suite's own header comment), plus manual
Playwright-driven checks of search-to-select (`django/django` --
confirmed edge dimming/highlighting and camera focus both still update
correctly frame-to-frame despite the new geometry caching) and the
pull-force slider (confirmed frozen-at-0 / unfrozen-above-0 behavior
matches pre-change exactly, via the temporary `window.__debugNodeById`
probe described above, removed before committing).

## Making the dense point-blobs actually mean something: piles, and a hull trim that was quietly hiding real clusters inside giant empty-looking ones

Follow-up feedback on the previous pass: "we have 6 very obvious and very
dense clusters of points. However they are nowhere to be found visually,
why? Also having clusters that are all circles is not the best format
maybe. Basically, when we can see more than 3 layers of clusters/nodes at
once, it unreadable." Same diagnostic approach as before -- a temporary
`window.__debugNodes`/`window.__debugNodeById`/`window.__debugClusterHullFor`
probe (removed before committing, same as last time) plus small one-off
Node scripts under `web/tools/` (also removed once their findings were
folded in here) to get real numbers instead of guessing from a screenshot.

**Root cause, part one: 574 of 660 top-level entries are singleton repos,
and most of them pile on top of a handful of real clusters.**
`scripts/14_cluster_hierarchy.py` clusters on a co-star/topic/text
similarity *graph*; a repo with no edges in that graph (no shared
stargazers, no topic tags, no readable text signal) stays a singleton no
matter how close it sits to other repos in the completely separate
trophic/topic *embedding* that actually decides its world position.
Checked directly: of the 574 top-level singleton repos, 502 have zero
topic-tag signal (which collapses their x/z to exactly 0 regardless of
theta), and of those, 475 land within one narrow trophic-height band
(y in [0.9, 1.0) -- heavy consumers, depended on by nothing else in this
cohort). Hundreds of individually-real repos, each with no *cluster* of
its own, packed into the same visual neighborhood as several of the
cohort's biggest real clusters ("Large Language Models" 417 members,
"Generative Models" 280, "Image Video Generation" 68, all within ~20
world units of each other) -- exactly the "obviously dense, but nothing
names it" gap being described.

First attempt, reverted: widen the *existing* exact-position-tie
fan-out (`resolveWorldPositions`/`fanPosition`, previously a fixed
46-unit radius for repos that round to the exact same point -- itself
already too small for a tie group in the hundreds, checked directly:
the largest exact-tie group across the full 2983-repo cohort is 1025
members, fanned across a 46-unit sphere) to catch *near* ties too, with
the fan radius scaled by `sqrt(group size)`. This does spread individual
dots apart -- but `WORLD_POS` is the single source of truth every real
cluster's hull is also built from, at *every* level, not just top-level
ids. Spreading a repo further from its real computed point to fix
top-level legibility also spreads it further from whichever real cluster
it's actually a *member* of deeper in the tree: checked directly, the
biggest real clusters' hull radii grew 15-80% (one nearly doubling)
instead of shrinking, and dependency edges between now-far-apart
former-neighbors turned into a "dandelion" spray instead of a clean
bundle. Reverted in full -- the two problems (top-level dot legibility,
real-cluster hull accuracy) need two different fixes operating on two
different, non-overlapping populations, not one shared position tweak.

**Fix, part one: declutter piles.** A new pass
(`buildDeclutterPiles`, `web/template.html`, runs once at load right
after `REPO_LAYOUT_TARGET` is built) grid-bins *only* the top-level
singleton repo ids by their raw layout-target position (not the
tie-broken `WORLD_POS` -- deliberately, so this can't leak into any real
cluster's hull the way the reverted attempt did) into 30-world-unit
cells, and any cell with >= 4 members becomes a synthetic pile: same
`{id, level, parent, children, hub, label, memberCount, stargazers,
forks}` shape `scripts/14` already produces for a real cluster, spliced
into `CLUSTERS`/`CLUSTER_ROOTS` before the rest of the file's existing
machinery runs. Every downstream consumer -- hull/volume rendering,
expand/collapse, halos, color-by-cluster, the detail panel -- needed zero
special-casing, since a pile is structurally indistinguishable from a
real cluster to all of them. Labeled honestly by count alone ("474
repos"), never a topic guess: unlike a real Leiden cluster, a pile's only
claim is spatial proximity, not any actual shared signal, and implying
otherwise would break the same "real, derived, never guessed" rule
`scripts/22`'s LLM-generated cluster labels already follow (`hub` is
picked the same honest way real clusters do: highest-stargazer member,
not anything cluster-specific). Result: this cohort's 574 top-level
singletons collapse into 4 piles (474/57/21/14 members) plus 2 repos
too isolated to qualify, cutting default-view materialized top-level
entities from 660 to 93. Verified live: hovering the 474-member pile
shows "474 repos, 106,125 combined stargazers, 13,076 combined forks" --
real aggregate stats, same tooltip a real cluster gets.

**Root cause, part two, and fix: the hull trim fraction was undertrimming
exactly the clusters most in need of it.** Piling didn't fully fix the
"dense area, no visible boundary" read -- the cohort's biggest real
clusters were *already* labeled, with a hull already drawn, but the hull
was so large and diffuse it rendered as a huge, mostly-empty translucent
wash (previous pass's fix made cluster labels visible at all; this pass
found the shape behind that label was itself the remaining problem).
`clusterHullFor` already trimmed to the closest 90% of members by
distance before hulling (added in the previous performance/labeling
pass specifically to fight this), but checked directly against the 4
biggest real top-level clusters, the hull radius doesn't fall roughly
linearly as trim tightens -- it falls off a cliff between keeping 90%
and keeping 50%: 664->260, 230->92, 222->99, 254->67 world units. A
genuinely tight core sits well inside a long, sparse tail of outlier
members that 90% still mostly included; 50% cuts past the cliff.
`CLUSTER_HULL_TRIM_FRACTION` dropped from 0.9 to 0.5 (`web/template.html`,
right above `clusterHullFor`) -- a smaller cluster with <= 4 members is
still fully protected by the existing `Math.min(4, ...)` floor either
way. Verified visually (Playwright screenshot of the default view,
before/after): the single dominant flat-pink wash is now several
distinct, individually-sized, individually-colored, individually-labeled
volumes -- "Deep Learning Detection", "Preference-Based Reinforcement
Learning", "Emotional Language Analysis" and others all visible and
readable in the same region that used to be one shape.

**"Clusters that are all circles" / "more than 3 layers is unreadable":**
the cluster volumes were never literal circles -- `ConvexGeometry`
already builds a real (smoothed) 3D hull from each cluster's trimmed
member positions, see `web/src/scene3d.js`'s `syncClusterVolumes`. What
read as "just a circle" was the same oversized/diffuse-hull problem
above: a convex hull of a very spread-out point cloud approximates a
smooth blob from most angles, and the trim fix directly shrinks that
effect too (a tighter, more true-to-the-real-core point set hulls into a
more distinctly-shaped volume). Checked the layers complaint with a
scripted 2-level drilldown (`web/tools/drilldown_test.mjs`, not kept --
expand a top-level cluster via a simulated click, then expand its
biggest newly-revealed child): still shows an ancestor halo plus 2-3
sibling volumes overlapping in the busiest region, but each is now small
and light enough (same trim fix) to stay individually legible rather
than blurring into a wash. No dedicated halo-suppression/layer-limiting
mechanism added on top of that -- the trim fix was the effective lever
here too, in testing. Left as a known area to revisit if specific
drilldown paths still read as cluttered after this.

Full regression check: `npm test` (23/23, unchanged -- same reasoning as
above, none of `buildDeclutterPiles`/`clusterHullFor` are covered by the
jsdom suite), plus the live hover check on the new pile node and repeated
Playwright screenshots of the default view and a 2-level drilldown.

## A stale side panel, and why cluster volumes read as uniform spheres regardless of real shape

Immediate follow-up feedback: "The cluster are not idenfiable at glance:
the right table is static (misleading), and they have no labels (not
linkable). Also, why are they all sphere even for contained objects that
are obviously not spheres? That is screen space lost." Two genuinely
separate bugs, both confirmed by reading the actual code/data rather than
guessing from a screenshot.

**The CLUSTERS side panel was a one-time snapshot dressed up as live
data.** `renderClustersPanel()` read `repoClusterIds`, a `var` computed
*once* at load from the full 203-cluster hierarchy, completely
independent of which clusters are actually expanded/collapsed/visible.
Confirmed via `git log -p`: the panel's own heading ("auto-detected, over
the currently visible repo-repo edges") describes an *older* version of
this function that really was recomputed from live-toggled edge
visibility -- that implementation was replaced (`now sourced from the
same precomputed hierarchy the LOD system itself uses`, per a nearby
comment) but the heading text was never updated to match, so the panel
had been silently lying about its own behavior for a while. Fixed by
rewriting it to list whatever cluster meta-nodes are actually
materialized right now (`nodes.filter(n => n.type === "cluster")`),
re-run from `rebuildTierEdges()` -- the single existing choke point every
cluster expand/collapse/initial-materialize/permalink-reveal already
funnels through, so no new call-site wiring was needed to keep it live.
Each row is now also a real link, not just text: clicking one calls the
same `focusNode()` search-to-jump/permalink already uses. Swatch colors
switched from the old flat whole-hierarchy index to `clusterIdentityColor`
-- the same function the 3D volumes themselves use -- so a panel entry's
color now actually matches what's on screen.

Hit one real bug getting this live: `rebuildTierEdges()`'s *first* call
happens during initial page setup, before `clusterIdentityColor`'s own
`clusterSiblingIndexCache` (a plain `var`, unlike every function
declaration in this file, which only takes its real value when execution
reaches that line) has actually run -- calling the new panel code that
early threw `Cannot read properties of undefined`. Fixed with a
`clustersPanelReady` guard flag, flipped true once initial setup
completes (same point the old one-time `renderClustersPanel()` call used
to sit), not by restructuring the file's initialization order.

**Cluster volumes reading as uniform spheres, "screen space lost": two
real, independent causes, of increasing depth.**

1. `Scene3D`'s cluster-volume geometry pads every real point outward by a
   flat 40 world units before hulling (`inflatePoints3D`), and separately
   forces every axis open to at least 50 units if it's thinner
   (`ensureMinimumSpread`/`MIN_HULL_AXIS_EXTENT`) -- both meant to stop a
   near-degenerate point set from hulling into an invisible paper-thin
   sliver, both applied identically regardless of the cluster's own real
   size. For this cohort's *large majority* of small clusters (2-5
   members is typical), a flat 40-50 unit inflation completely swamps
   whatever real spread those members had, so nearly every small cluster
   rendered as a near-identical ~40-90-unit blob no matter how tight or
   how oddly-shaped its actual members were. Fixed: `padding` is now
   `clusterVolumePadding(hullRadius) = max(8, hullRadius * 0.15)`,
   computed per-cluster in `web/template.html` and passed through to
   Scene3D (previously always the unset default of 40); `scene3d.js`'s
   `MIN_HULL_AXIS_EXTENT` dropped from 50 to 18 to match (the existing
   `syncClusterVolumes` unit test that exercises this floor was updated
   for the new number, still passing). A big diffuse cluster's padding
   stays roughly what it always was (~15% of an already-large radius); a
   tight one gets a small, proportionate amount instead of a fixed 40.

2. The deeper cause, found while trying to verify (1) actually helped:
   `resolveWorldPositions`' exact-position-tie fan-out (see the section
   above) is flat and *cluster-agnostic* -- every id sharing a raw
   rounded point gets a Fibonacci-sphere slot purely by global sorted-id
   order, with no awareness that some of those ids are members of the
   very same real cluster. Checked directly: 6 raw tie groups (repos with
   zero topic-tag signal, so x/z collapse to exactly 0 regardless of
   theta, sharing one rounded trophic height) cover 2994 of this cohort's
   3191 repo+cluster ids -- one single group has 867 members spanning
   dozens of unrelated real clusters. A real cluster with, say, 10
   members caught in that group doesn't get a small, honest 10-point fan;
   each member lands wherever its *global* sort position happens to fall
   among all 867, scattered across the full 46-unit sphere from its own
   actual siblings, not from unrelated repos. This is the direct, literal
   explanation for "why sphere": for the ~half of this cohort's small
   real clusters where every member shares one exact raw tie (checked:
   42 of 86 top-level clusters), the rendered shape *is* this fan --
   points placed on a sphere surface by construction, not an incidentally
   round hull of real spread.

   Tried a fix -- a two-level fan, splitting each raw tie group into
   per-real-cluster "cohesion" groups (via `repoParentCluster`, already
   computed earlier in the file) before fanning, so same-cluster members
   land near each other instead of scattered by unrelated global sort
   order -- and reverted it. It's safe in the one way that mattered (the
   outer-level radius still only ever shrinks relative to the old flat
   46, same non-regression property as the earlier reverted attempt), but
   measured *worse* for several real clusters rather than better: Python
   Environment Setup's hull grew 54->67, Chinese Computer Vision 78->90,
   Deep Learning Detection 256->269. Root cause of the regression: a
   cluster whose members straddle a *rounding* boundary (two members at
   raw trophic height 392.49 and 392.51 round to different integers)
   still land in two entirely separate raw tie groups with independent,
   unrelated Fibonacci layouts -- a two-level fan can only tighten members
   that already share one raw group, it can't reunite ones split across a
   rounding edge. Deep Learning Detection's small hull increase also
   happened to cross the zoom-driven auto-expand threshold (`EXPAND_PX`)
   at the *default* camera position, silently expanding it and changing
   what the default view shows before any user interaction -- an
   unintended, hard-to-predict side effect for a change that wasn't even
   a clean net improvement to begin with. Reverted rather than push a
   third iteration in the same session; (1) above stays as the real,
   verified fix, and this remains a real, documented, not-yet-fixed root
   cause for about half this cohort's small clusters -- the honest fix
   likely needs tie-breaking that's aware of real cluster membership
   *before* the raw-position rounding step, not after it, which is a
   bigger change than fit safely in this pass.

Full regression check: `npm test` (23/23, one test's numeric threshold
updated to match the new `MIN_HULL_AXIS_EXTENT`, described inline in that
test), plus Playwright checks of panel dynamism (row count and labels
change correctly after a real canvas-driven cluster expand) and click-to-
focus (confirms the URL hash updates to the clicked cluster's id).

## Re-tuning default-view legibility for the 2983-repo cohort: cluster hulls with no upper bound, not the sphere-fan or pile mechanism suspected first

Prompted by a fresh review of the default view after the two JS-ecosystem
commits (a34c676, ec3818c) grew the cohort from 1983 to 2983 repos without
re-running any of the legibility tuning done at the smaller scale: a real
screenshot showed two giant, heavily overlapping translucent volumes
dominating the entire canvas, with a dozen cluster labels crushed together
illegibly in the overlap zone -- clearly worse than before that tuning work
shipped, not just "still imperfect."

**Two plausible root causes were checked directly and ruled out before
finding the real one -- worth recording, since both looked likely at
first.** (1) The `buildDeclutterPiles` grid (`DECLUTTER_GRID = 30`) looked
too coarse for a much larger singleton population -- measured directly
(the `topLevelIds` in `data/processed/repo_cluster_hierarchy.json`: 804
top-level entities, 696 of them singleton repos, 524 of which sit within a
trophic-height band just 1.7 world units wide) and a single pile really
does have 467 members. But instrumenting the live app (a temporary
`window.__debug` hook exposing `WORLD_POS`/`CLUSTERS`, same pattern as
every other phase's jsdom-harness verification) showed that pile's actual
hull radius was only 65 world units -- not visually dominant at all. (2)
The already-diagnosed "flat, cluster-agnostic Fibonacci fan" sphere problem
(previous section) looked like the same mechanism at bigger scale --
checked directly too, and the dominant raw-position-tie group within the
467-member pile turned out to have exactly 1 member per group (the
members' trophic-height spread, while narrow, is just wide enough that
`Math.round()` doesn't collide them after all). Neither hypothesis
survived contact with real measurement.

**The real cause, found by ranking every rendered cluster's actual hull
radius:** the two giant on-screen blobs were real Leiden clusters (level-2
coarsened ones, e.g. `OpenMMLab Computer Vision`, `Typing Test Tools`,
`LLM Serving Platforms`), not declutter piles at all -- with hull radii up
to 459 world units, bigger than the entire 320-unit topic-circle radius.
`CLUSTER_HULL_TRIM_FRACTION` (the existing "keep the closest 50% of
members" fix from the previous large-cluster pass) turned out to have a
blind spot its own `Math.min(4, ...)` floor was specifically designed to
protect: a cluster with 2, 3, or 4 real members gets *zero* trimming no
matter how far apart those members are, and Phase 13's clusters are
grouped by a non-spatial similarity signal (co-star/topic/text), so a
real 2-member cluster can have its two members sitting on opposite sides
of the trophic/topic embedding. Checked directly: `OpenMMLab Computer
Vision` has exactly 2 members, hull radius 459 -- one of the biggest
volumes on screen, representing nothing but two points a long way apart.
A member-count floor that made sense when the assumption was "small
clusters are small because they're tight" breaks down once "small" and
"tight" turn out to be independent properties.

A second, compounding bug surfaced while investigating the first: the
existing trim already measured member distance against `WORLD_POS[cid]`
(the cluster's own separately-computed layout position) rather than the
members' own centroid -- and `WORLD_POS[cid]` can itself have been
arbitrarily fanned out by `resolveWorldPositions`' tie-breaking if the
cluster's nominal position happened to collide with something else. Using
it as the trim reference produced skewed, asymmetric per-member distances
that didn't match what would actually get rendered (`scene3d.js`'s
`buildFallbackVolumeGeometry` computes its own centroid from whatever
`positions` array it receives, ignoring any externally-passed center
entirely) -- simulated first against real data before touching the app
(same "measure the fix against real numbers before shipping it" discipline
as every other constant in this file): trimming by distance-to-`WORLD_POS`
left the worst 2-member offenders stuck at 353-400 radius even under an
aggressive cap, because a symmetric pair's *individual* distances to their
own true midpoint don't shrink just by discarding the farther one relative
to a *different*, external reference point.

**Fix, verified against real data before and after:** `clusterHullFor`
now runs a second, iterative trim on top of the existing fraction-based
one -- repeatedly recompute the *current* surviving set's own centroid,
find its farthest member, and drop it, until the max distance clears
`CLUSTER_HULL_MAX_RADIUS` or the fraction-trim floor is hit. 130 was
picked empirically the same way `CLUSTER_HULL_TRIM_FRACTION` was: measured
across this cohort's real 244 clusters, it drops the worst-case radius
459 -> 126 and the population with any cluster past 100 units from 73/244
to 5/244 (mean 93 -> 40), while still preserving real size differentiation
for clusters that are both large *and* spatially coherent (a real
217-member cluster still renders at 83, clearly bigger than a tight
2-member one collapsed to the 24-unit floor). A 2-member pair that's
simply far apart in this coordinate system now renders as a small,
single-point-sized marker instead of a scene-spanning sphere -- still 100%
real (literally one of the cluster's own members' actual position), just
no longer implying a shared "territory" the two members don't occupy
together in space. Verified three ways: the live implementation's actual
measured radius distribution (via the same `window.__debug` hook, rebuilt
against the real edited `template.html`) matched the pre-implementation
simulation exactly; `npm test` stayed 23/23 (this logic lives in
`template.html`, outside the committed `scene3d.js` suite, consistent with
every other template-level fix in this file); and a real Playwright
screenshot of the default view before/after shows the two dominant blobs
replaced by several smaller, mostly non-overlapping volumes with legible,
separated cluster labels, plus a real search-to-`facebook/react` pass
confirming reveal/select/cluster-panel-live-update still work with zero
new console errors.

**Not addressed here, deliberately.** The declutter-pile grid (30 world
units) and the raw-position-tie Fibonacci fan (previous section) were both
checked and found not to be the cause of *this* regression, but neither is
proven correct at this larger scale either -- they simply weren't
responsible for the specific symptom investigated this time. Worth
re-checking on its own if a future growth pass reintroduces a giant-blob
symptom that this fix's `window.__debug` instrumentation doesn't trace
back to an oversized 2-4-member cluster.

While investigating, also confirmed a second, separate, real bug: opening
`web/index.html` via `file://` (the app's documented primary distribution
method) throws a CORS "origin 'null'" error from `THREE.TextureLoader` for
every repo-avatar texture actually requested at the current default view
(checked: the flagged logo PNGs are valid, non-corrupt files, so this is a
loader/origin issue, not a data one) -- meaning avatars likely silently
fall back to flat color for anyone using the app the documented way.
Flagged, not fixed in this pass -- out of scope for the legibility work
above, worth its own dedicated look.

## Selection focuses the sidebar, and a README reader over the graph

Asked for three things at once: clicking a repo should actually show it in
the right panel, panels other than "Selected node" should collapse, and the
repo's README should be easily readable.

**The first two are one change.** `showDetail()` already un-collapsed its
own panel (see "Collapsible sidebar panels" above), which was enough when
that was the only competing content -- but "Selected node" is the fourth of
six panels, so with Clusters open by default above it (plus anything the
user had opened themselves), a graph click could update a panel sitting
entirely below the fold and look like nothing happened. `focusDetailPanel()`
now collapses every *other* panel and `scrollIntoView({block:"nearest"})`s
the detail one. Collapsing the rest usually brings it into view on its own;
the scroll covers the rest.

One exemption, via a `fromPanel` argument threaded through `focusNode(n,
{fromPanel})`: the Clusters list sets its own selections, and folding that
list away after the first row would make it unusable to walk. Canvas clicks
and search-to-jump pass nothing and collapse everything.

**The README needed a different surface.** The sidebar is 320px -- narrower
than most code blocks and tables in a README -- so it opens over the canvas
pane (not over the sidebar, so the Selected node panel it was launched from
stays visible beside it) at `max-width: 760px`, dismissed by Esc, the ✕, a
backdrop click, or selecting a different node.

Where the content comes from: **not** the pipeline's own README cache. That
cache exists (`scripts/17_fetch_readmes.py`, all 7051 repos) but it's 96MB
of markdown -- eight times the whole page -- and it's only there as
embedding input. So the reader fetches the one repo asked for, live, from
the same unauthenticated GitHub API and the same 60/hour/IP budget as the
existing live star/fork counts. That budget is exactly why it's a button
rather than something that loads with the selection: auto-fetching would
halve how many repos you can inspect in an hour, and most clicks are about
the graph, not the prose.

`Accept: application/vnd.github.html` returns GitHub's own rendered,
sanitized HTML, so there's no markdown parser to write and maintain. Checked
the real response before building on it (`gin-gonic/gin`, then
`nlohmann/json`): no `<script>`, no `<style>`, no `on*` handlers, no
`javascript:` URLs, and all image srcs already absolute
(raw.githubusercontent/camo). It's still third-party HTML, so it renders in
a sandboxed `srcdoc` iframe -- no `allow-scripts`, opaque origin, and its
CSS and the app's can't reach each other. The only tokens granted are
`allow-popups allow-popups-to-escape-sandbox`, which is what makes a link
open a new tab instead of navigating the frame itself to github.com with no
way back. Scripts/styles/iframes/forms are stripped in
`prepareReadmeHtml()` anyway, so the reader doesn't depend on both GitHub's
sanitizer and the sandbox staying correct.

**Two real bugs found by measuring, both fixed.**

*Dead tables of contents.* GitHub prefixes every heading id with
`user-content-` (on a hidden permalink `<a class="anchor">` beside the
heading) while leaving the README's own TOC links pointing at the
unprefixed fragment. `nlohmann/json` has 34 such links, all broken on
arrival. `prepareReadmeHtml()` moves the stripped id onto the heading
wrapper and drops the permalink anchors (a github.com affordance -- an
octicon linking back to the page you're already reading). The first test
repo, `gin-gonic/gin`, has 18 fragment links and *all 18* are those
permalinks, i.e. zero real TOC links -- so the initial verification proved
nothing about the case the code exists for, and only re-testing against a
README that actually has a TOC did.

*A README link loading a second copy of the whole app.* A `srcdoc` document
has no URL of its own, so it resolves URLs -- including a bare `#install`
-- against the **parent** page. Clicking a TOC link navigated the reader
frame to `index.html#install`, i.e. loaded another full copy of this 11.8MB
explorer inside itself (visible in the console as four repeated "Blocked
script execution in 'http://localhost:8123/index.html#examples'" lines, and
as the frame's styling vanishing). Probed four variants directly rather
than guessing:

| variant | result |
| --- | --- |
| `srcdoc`, no base | navigates frame to `index.html#examples` (the bug) |
| `srcdoc` + `<base href="about:srcdoc">` | scrolls in-frame, 0 -> 2789 |
| `srcdoc`, hrefs rewritten to `about:srcdoc#...` | also works, needs rewriting every link |
| `blob:` URL + sandbox | fragment ignored entirely, no navigation at all |

Took the `<base>`. Every non-fragment URL is absolutized by
`prepareReadmeHtml()` before this point, so nothing else depends on the
base value.

Link absolutization follows github.com's own resolution rather than the
browser's: a leading `/` in a README means "from the repo root" to the
author, not "the github.com root", so the slash is stripped before
resolving against `https://github.com/{slug}/blob/HEAD/`.

**Verified** (Playwright, against the built page): panels go
`path=OPEN about=OPEN clusters=OPEN` -> only `detail=OPEN` on select, with
the detail panel measurably inside the sidebar's visible box; a Clusters-row
click leaves Clusters open; the reader opens with the right title and
GitHub link, `sandbox="allow-popups allow-popups-to-escape-sandbox"`,
renders `gin-gonic/gin`'s README (7761 chars, first image actually loaded)
and `nlohmann/json`'s (749 links, 34/34 TOC fragments resolving to a real
target in-frame, 0 given `target=_blank`, 0 leftover `user-content-` ids, 0
scripts, 0 styles beyond the injected one); a TOC click scrolls the frame
0 -> 3582 with zero new tabs and the frame still alive and styled; Esc,
backdrop click, and selecting another repo all close it; reopening the same
repo costs no second request. `npm test` 23/23, `build_web_explorer.py`
re-run clean (same counts).

**Two non-issues confirmed as not ours.** A `logos/flagalpha.png` 404 fires
before any reader is opened -- a pre-existing repo-logo download gap. And
one `camo.githubusercontent.com` badge in `nlohmann/json` is blocked by
Chrome's ORB; curling it directly returns `502 text/plain`, i.e. a badge
that's dead on GitHub's side and would be broken on github.com too.

## The two console items from the README reader, chased to ground

Both were flagged rather than fixed when the reader landed. One was a real
bug with a real fix; the other measured out as not ours.

### `logos/flagalpha.png` 404 -- a renamed owner, fixed

Exactly one owner of 5828 had no avatar file. Not a download failure:
`github.com/flagalpha.png` is a hard 404 because the org renamed itself to
`LlamaChinese` after this cohort captured its repo ids. The vanity avatar
URL is keyed on the *current* login, so it can never resolve an old one --
and the file still has to be saved as `flagalpha.png`, since that's the name
the graph nodes carry.

`gh api repos/flagalpha/llama2-chinese` does follow the rename and reports
the current `owner.avatar_url`, so `08_download_repo_logos.py` now falls
back to that on a vanity-URL failure. Resolved per repo rather than per
owner, deliberately: only a repo id survives a rename, a bare owner name
doesn't. Recovered the avatar for real (verified: the node renders it, and
a full load + 14-position hover sweep + selecting that repo produces zero
failed requests).

Then the durable half. The gap only became visible as a console 404 because
the renderer learns a logo is missing by requesting it -- `loadAvatarTexture`
caches the failure but the request still happens once per absent owner per
page load. `build_web_explorer.py` now diffs the cohort's owners against
`web/logos/*.png` at build time and ships the absent ones as
`MISSING_LOGO_OWNERS`, handed to `Scene3D.init` as `opts.avatarlessOwners`
and seeded straight into the same `"error"` state a failed load produces.
Same flat-color fallback, no dead request. The list is empty today; it stops
being empty every time the cohort grows ahead of an `08` re-run (which this
project has done six times), and permanently for any owner whose account is
deleted rather than renamed -- `08` can follow a rename, but a deleted
account has no avatar left anywhere.

Unit-tested rather than spot-checked, since an empty list proves nothing
about the mechanism: `loadAvatarTextureCore` is now exported and three tests
drive it against a hand-seeded cache with a counting `TextureLoader.load`
stub -- a seeded-avatar-less owner issues zero requests and stays `"error"`
(not `"pending"`), an unseeded owner still issues exactly
`logos/{owner}.png`, and a second call for an in-flight owner doesn't
re-request. 26/26.

### The ORB-blocked README badge -- measured, not ours

One `camo.githubusercontent.com` badge in `nlohmann/json`'s README failed
with `net::ERR_BLOCKED_BY_ORB`. The obvious suspicion was our own sandbox:
an opaque-origin frame makes every image request `no-cors`, which is
exactly the situation Chrome's Opaque Response Blocking governs. If the
sandbox were blocking images a normal page renders, that would be a real
regression in the reader worth fixing.

Measured it directly instead of reasoning about it. Harvested all 85
distinct image URLs from eight real cohort READMEs (`nlohmann/json`,
`gin-gonic/gin`, `fmtlib/fmt`, `spf13/cobra`, `psf/requests`,
`rclone/rclone`, `gohugoio/hugo`, `pallets/flask`) via authenticated
`gh api`, then rendered the same set twice in the live page -- once in a
frame with our exact `sandbox` attribute, once in a plain frame -- and
compared `naturalWidth > 0` per image:

| | images loaded |
| --- | --- |
| plain frame | 81 / 85 |
| sandboxed frame | 82 / 85 |
| **blocked only by the sandbox** | **0** |

Zero. The three failures fail identically both ways and are all dead
third-party services: `api.cirrus-ci.com` (TLS connect failure, curl exit
35 -- the service is gone), `api.star-history.com`, and
`git.fsfe.org/reuse/...`. The camo URL that started this returns `502
text/plain` on three consecutive tries, which is *why* ORB blocks it --
a 502 error page is not an image. Without the sandbox the same request
logs "Failed to load resource: 502" instead; github.com renders that
README with the same broken badge.

So there is nothing on our side to fix. Suppressing it would mean either
not rendering README images at all, or HEAD-probing all 85 before display
-- both strictly worse than a browser logging that a dead image is dead.
Recorded here so the next person who sees `ERR_BLOCKED_BY_ORB` in this
reader doesn't re-derive the sandbox hypothesis from scratch.

## Reactive search, and a vanity card for a repo the pipeline never saw

Asked for two things: search should suggest real repos even when they
haven't been fetched, and a looked-up repo should get its card *computed*
like a cohort repo -- position, dependencies and the rest.

### Search

The suggestion list only ever knew the 7051 repos already in `AGGREGATES`,
so anything else required typing an exact `owner/repo` slug from memory.
Now a query of 3+ characters also hits `/search/repositories`, debounced
400ms and cached per query for the session. That endpoint has its own
quota -- **10/minute unauthenticated, a separate pool from the 60/hour**
the rest of the page spends -- so typeahead here doesn't compete with the
card's own budget. Cohort matches still render instantly and first; live
rows arrive underneath with their star counts. An exact-looking slug is
still offered immediately with no wait, since the user already knows what
they want.

### The card

The old behavior was a placeholder: a random position and dashed
"same GitHub owner" edges standing in for a real relationship. Everything
below is the pipeline's own computation, re-run in the browser on live
data, against tables the build step now ships:

| axis | how | cost |
| --- | --- | --- |
| theta / r | the repo's GitHub topics through scripts/16's exact TF-IDF circular mean, against the shipped per-topic angles | free, topics ride along in `/repos` |
| shared-tag edges | literal tag overlap vs. `TOPIC_REPOS`, same >=2 rule as scripts/13 | free |
| dependency edges | its own manifests, parsed with the same direct-runtime-only rules as scripts/29/31/33/36/38/41, resolved through the shipped coordinate tables | 1 root listing + 1 per manifest present |
| y | those dependency edges, see below | free |

Four new payloads (+1.0MB, page 11.8 -> 12.8MB): per-topic angles, a
topic -> cohort-repo index (which doubles as the TF-IDF document counts, so
no separate counts map), the six ecosystems' coordinate->repo tables, and
the trophic solve's raw scale. `13_semantic_edges.py` now also emits
`repo_topics.json` -- `data/raw/github_cache` is gitignored, so the only
committed record of who is tagged what was the *pruned* edge list, which
has already dropped every tag below min-shared.

**Why shipped tables and no live registry calls.** Measured before
building: across non-cohort repos' manifests, the shipped tables captured
**100%** of the dependency edges that reach this cohort at all, and live
npm/PyPI/crates lookups added **zero**. What limits a new repo's edge count
is that most of its dependencies simply aren't cohort repos -- not
resolution coverage. So the card spends no requests on registries.

**Trophic height is an exact solve, not an estimate.** scripts/15
minimizes `(h_a - h_b - 1)^2` over dependency edges. Holding every cohort
height fixed, that objective's stationary condition for one new node is
closed-form: `h = mean over its dependencies of (h_dep + 1 level)`. So the
height is comparable to the cohort's rather than living on its own scale.
"One level" is 1.0 in the raw units of that solve, which is why
`trophic_scale.json` now ships. The incoming half of the sum is
structurally always empty and the panel says so: scripts/11 only kept edges
whose target resolved to a cohort repo, so a repo outside the cohort can
never be the target of one -- the card knows what it depends on and cannot
know what depends on it.

The computed edges are appended to the same `DEPENDENCY_EDGES` /
`SEMANTIC_EDGES` arrays the cohort's edges live in, not pushed in as a
special tier. From `rebuildTierEdges()` down a vanity edge simply *is* a
dependency or shared-tag edge -- bundling, arrowheads, tooltips, layout
forces and legend toggles all work with no special-casing. Removal splices
back out exactly what was appended.

Person tiers stay unavailable and are labelled as such. Real
shared-stargazer/contributor/issue-poster overlap needs per-person lists
for both sides of every pair -- one call per cohort repo, 7051 of them,
against 60/hour. The old same-owner edge was a cheaper thing wearing that
name; it's gone.

### Verified against an independent computation, not against itself

`psf/black`, with the expected card computed separately in Python from the
committed processed data:

| | browser | Python |
| --- | --- | --- |
| trophic y | 0.60 | 0.5969 |
| topic r | 0.60 | 0.6038 |
| topic theta | 259° | 259° |
| dependency targets | click, packaging, platformdirs, tomli | same 4 |
| shared-tag targets | rustfmt, mongoengine, maturin, TheAlgorithms/Python | same 4 |

Raw theta agreed to the last float digit (-1.7568019921053752 vs
...45/...46). Also checked `denoland/fresh` (6 topics, r=1.00, theta=269°,
and correctly *no* dependency edges -- Deno's `deno.json` isn't a manifest
this pipeline reads, and the panel says which files it looked for).
Removal leaves no trace; no console errors; `npm test` 26/26.

### Three real findings along the way

**A misleading count I had written myself.** The panel first read "4
dependency edges … 6 other declared dependencies do not resolve", implying
10 parsed. `black` actually parses 13 and resolves **7** -- three resolved
cohort repos were being silently dropped by the top-4 prune and folded into
what read like a resolution failure. Found by lifting the shipped parser
out of the template and running it against the real `pyproject.toml`, which
is also how the parser itself was confirmed to pick up exactly the 8 core
plus 5 extras and no junk. The panel now separates "resolved but pruned"
from "does not reach the cohort".

**Two wrong diagnoses of one hang, both corrected by measuring.** The card
first sat on "computing…" forever with a 200 in the network log and a clean
console. First guess: two identical in-flight `/repos/{slug}` GETs (the
card's and `fetchLiveData`'s) being coalesced, leaving the loser's
`res.json()` unsettled. Deduplicating them did not fix it -- so that
explanation was wrong, though the dedup is kept on its own merit (one of
every 60 requests an hour, spent on a response already in hand). Second
guess: the synchronous `for (60) tick()` sweep. Removing it did not fix it
either. What actually did: instrumenting frame times. The lookup handler
costs **8ms**; the frames around it cost **2-6.7 seconds each**, so
`res.json()` waited 28s purely to be *handled*. Serial round trips each pay
a frame, so the root listing now goes out in parallel with the repo fetch.

**That frame cost is pre-existing, not this feature's.** The control
matters: a plain *cohort* search-to-jump degrades frames identically
(median 2807ms, max 7398ms) to a vanity lookup (4808ms / 7222ms), and idle
frames are already ~235ms at 7051 nodes. This is a headless software-GL
environment, so the absolute numbers say little about real hardware -- but
whatever it is, it is the zoom/LOD path, and it predates this work. A card
takes ~11-46s to settle here against ~2s of actual network time.

## Java's real gap was never the parser: root-only scoping, and a 403 that got cached as truth

Asked what languages the atlas covers, the honest answer needed measuring
rather than listing scripts. Six manifest ecosystems are wired up (Go,
Rust, JS/TS, Java, C/C++, Python) plus the original PyPI/SemRepo path, but
coverage across them is wildly uneven, and the interesting number is not
"does a parser exist" but "does a repo of this language end up with a real
outgoing dependency edge":

    Python              1701 repos   87.5%     (mostly the legacy PyPI path)
    Jupyter Notebook     243          96.7%
    Go                  1000          74.1%
    Rust                1003          67.5%
    TypeScript           560          39.1%
    JavaScript           449          34.5%
    C++                  620          24.7%
    Java                1003          12.3%   <-- second-largest bucket, worst coverage
    C                    429           7.2%

C's 7.2% is structural -- C projects declare dependencies in Makefiles and
vendored trees, and there is no manifest to read. Java's 12.3% was not.

### The diagnosis: aggregator roots

Running 33's own parsers over all 811 cached root manifests, only 192 yielded
a single dependency. The files that yielded none say why:

    185 of 196 zero-dependency pom.xml  contain <modules>      (aggregator POM)
    331 of 423 zero-dependency gradle   contain allprojects    (multi-project root)

That is the normal shape of a Maven/Gradle multi-project build: the root file
lists and configures children, and every real `implementation` /
`<dependency>` line lives one directory down. Reading only the root is the
same root-only scoping 31 accepts for JS workspaces -- except that in Java it
is the majority case, not a minority one. Nothing was wrong with the parsers.

A 25-repo random sample of the zero-dependency population, parsed with
submodules included, put 24 of 25 above zero before any code was written.

### Discovery and fetch: one tree, then aliased GraphQL

Two candidate ways to find submodules. Reading the *declared* module list (a
POM's `<modules>`, a settings.gradle `include`) costs nothing extra for Maven
but only sees one level, and a settings.gradle can build its include list
dynamically. A recursive git tree costs one request, finds nested modules, and
cannot be defeated by any of that. The tree won.

Fetching what it finds is where the cost lived: median 5 module manifests per
repo, but apache/camel has 524 and quarkus 1789. One contents call each would
have been ~30k requests. GraphQL aliases collapse that -- checked directly at
the batch size actually used, **a query with 50 aliased blobs costs 1
rate-limit point**, the same as a single-blob query. camel drops from 525
requests to ~11. The two halves don't even share a pool: tree reads are REST
core, blob reads are GraphQL.

Which made the sweep latency-bound, not rate-limit-bound: timed end to end, a
repo costs ~9s of which 0.8s is the throttle. Serially that is 2.6 hours for
1000 repos while using barely a third of either limit. Six threads, one cache
file per repo, no shared state -- this is the one fetch pass in the project
that runs threaded, and the docstring says why so the next reader doesn't
"fix" it back.

Final sweep: 1000/1000 repos, 31085 module manifests, 8 trees truncated by
GitHub (quarkus, camel, elasticsearch, eugenp/tutorials and four others --
reported as under-covered rather than silently treated as complete; only
Kodezi/Chronos lost everything, and it has no root manifest either).

### A silent-failure hole, caught at 309 repos

The first cache format was a plain `{path: text}`. A repo with one GraphQL
batch that failed after all its retries would write `{}` -- indistinguishable
from an honest "this repo has no submodules", and frozen in forever. Caught it
partway through the first full run, changed the format to record the
discovered `paths` alongside the `texts` (so a short read is detectable),
made failure non-cacheable, then **wiped the 309 already-swept repos and
restarted** rather than trust a cache that could not be audited.

### Two things the sweep's own output revealed

Both found by listing what actually got cached, not by guessing up front.

**94 of 2465 module manifests are not project modules.** 61 test fixtures
(`jib-cli/src/integration-test/resources/.../build.gradle`), 20 build logic
(`buildSrc/`), 6 under `gradle/`, 5 vendored trees, 2 Maven archetype
templates. The test-fixture case is the same distinction the parsers already
make dropping `scope=test` and `testImplementation`. Excluded at *parse* time,
not fetch time, so the choice stays measurable and reversible without
re-fetching a single file.

**21% of all (repo, coordinate) pairs are a repo naming a module it publishes
itself.** Modules in a multi-module build depend on each other by coordinate,
and those are not repo-to-repo edges. Reading what each POM declares it
publishes (with `<parent><groupId>` inheritance, which Maven itself applies
and without which most module coordinates come back half-empty) drops 8129
pairs before resolution. That halved the Maven lookups -- 10784 to 5810 --
and it is also *safer*: an internal artifact whose Maven Central entry has
since moved to a different repo would otherwise resolve into a real-looking
edge that never existed.

### The 403 that got cached as truth

Resolution ran, reported **355 of 8810 coordinates resolved**, and that number
was a lie -- one this change caused.

Spot-checking why `com.google.code.gson:gson` (declared by 158 repos) came
back unresolved, search.maven.org answered **HTTP 403**. Running 7789 lookups
in one pass had got the client blocked, and `_maven_get` returned `None` for a
403 exactly as it did for a genuine 404, so `fetch_maven_scm` wrote every
blocked lookup to disk as a permanent "this coordinate has no GitHub repo".
Thousands of false negatives, cached, indistinguishable from real answers.

The latent bug predates this work; the volume is what fired it. Two fixes:

- `FAILED` is now a distinct sentinel from `None`. A lookup that did not
  complete is never cached, so it simply retries. A completed lookup -- 404
  included, because "Maven Central does not have this" is a real answer --
  still caches.
- search.maven.org is gone. The latest version now comes from the artifact's
  own `maven-metadata.xml` on repo1.maven.org, a static file on the same CDN
  host the `.pom` already came from. One host, one failure mode, no Solr
  service to be throttled by. Measured at 0.3-0.5s per request, and the
  re-run completed **8810 of 8810 lookups with zero failures**.

Purging the 8456 poisoned negatives (the 355 positives were trustworthy) and
re-resolving took the resolved count from 355 to **3512**, a 10x difference
that was entirely an artifact of the block.

### Result

    Java dependency edges         243  ->  3376     (zero lost: strict superset)
    Java source repos             120  ->   579
    distinct target repos          45  ->   223
    repos declaring a dependency  192  ->   819     (626 reachable only via submodules)
    Java out-edge coverage      12.3%  ->  58.0%
    Java repos touched by any edge          681/1003
    cohort repos with a trophic level 4551 ->  5076
    page                        12.8MB ->  13.5MB

No other language moved, which is the expected shape -- nothing shared changed.
Targets land where a Java graph should: jackson-databind (217 incoming),
guava (215), spring-boot (200), spring-framework (162), lombok (142).

`junit-team/junit4` picking up 84 incoming edges looked like a test-scope leak
and is not one: Alluxio's `microbench/pom.xml` declares junit at
`<scope>compile</scope>` explicitly. Real data, sloppily declared upstream.

### What is still missing, measured

5298 of 8810 coordinates remain unresolved, and the biggest are ordinary
libraries -- slf4j-api (declared by 249 repos), commons-lang3 (191), gson
(158), commons-io (150). These are *not* more 403s; the re-run had none:

- gson genuinely declares no `<scm>` in its own POM **or** its parent. Its
  GitHub repo is simply not in its Maven metadata.
- Apache-foundation libraries declare scm on gitbox.apache.org, already
  documented in 33.
- 423 unresolved coordinates are `com.github.<owner>:<artifact>` -- JitPack
  coordinates, which encode the GitHub owner directly in the groupId. That is
  an untested lever, not a claim; nobody has measured how many resolve.
- Chasing the parent POM one hop for a missing `<scm>` is another. It would
  not have helped gson.

Unchanged and still root-only: the vanity card's client-side Java parsing,
which also has no Gradle parser at all. It works under a 60-requests/hour
unauthenticated cap with no GraphQL available, and raw.githubusercontent.com
answered a probe with 429 during this work, so a partial, frequently-wrong
card would be worse than an honestly root-only one.

### Are they actually *in the view*?

Placement is a separate question from having an edge, and worth checking
rather than assuming. Every repo in AGGREGATES gets a `REPO_LAYOUT_TARGET`,
so all 1003 Java repos were always drawn -- but a repo with no dependency
edge has no trophic height and falls back to `y = 0.5`, the mid-plane. It is
rendered, just parked on a default rather than positioned by the data.

    Java repos with a real (data-derived) y      161  ->  681   (16.1% -> 67.9%)
    whole cohort with a real y                  4551  -> 5076

C++ ticked up too, 210 -> 214: four C++ repos are now depended on by a Java
repo, which is enough to give them a height they didn't have. Nothing else
moved.

Clustering was never the problem -- 863 of 1003 Java repos already sat in a
level-1 cluster, and the labels are real subject matter rather than a
language bucket: Android Material Design (151 Java members), Spring Boot
Platforms (110), JVM Messaging Systems (106), Android Video Streaming (69).

## Zoom-in sluggishness: what was actually slow, and the measurement trap that nearly hid it

The complaint was that zooming in gets sluggish "as it tries to display many
more nodes." That turned out to be literally true, and also not the part that
was fixable.

### Where the frame actually goes

Driving the app's own rAF callback synchronously (the harness
`tools/profile.mjs` already established, so headless rAF throttling can't
confound it) and dollying the camera in nine steps:

    distance  nodes   dep edges  sem edges   frame    sync   render   rest
      1939      386      1408        314    20.6ms   3.3ms   15.6ms   1.6ms
       745     1545      5245       2708    88.7      10.8    73.8     4.0
       462     5344     13887      11533   487.5      59.5   387.6    40.3
        42     7446     18613      12899   302.8      41.2   235.1    26.3

Two things fall out. `Scene3D.render()` is **78-80% of the frame** at every
zoom level, and the whole cohort ends up materialised: dollying in takes the
scene from 386 nodes to 7446 and from 1408 dependency edges to 18613. Almost
none of that is on screen.

A CPU profile at the zoomed-in state, split by source rather than read as one
list (half of it is `(program)`, which here is SwiftShader rasterising in
software -- a headless artifact, not something a real GPU pays):

    app JS (index.html)  10.1%   of which  clusterCentroidNow  35.5%
    three.js bundle      33.5%             bundled path rebuild 34.3%
    (program)/GC/native  56.5%             label placement      13.3%

### The app-side waste, all of it invisible to the user

`clusterCentroidNow` is an O(children) sum, and `controlPointPosition()` asks
for one per waypoint of every bundled dependency edge -- a large expanded
cluster is on the path of hundreds of edges, so the same sum ran thousands of
times a frame. Memoised for the duration of a frame; within one frame nothing
can move, so it is exact, not approximate.

`bundledControlPoints()` allocated two arrays and a fresh point object per
waypoint per edge per frame. The renderer already compares routes by
coordinate and keeps its own flat numeric copy (`routeSignature`), so all
those objects existed purely to prove nothing had moved. It now writes into
the edge's previous `_path` and `controlPointPosition()` returns a borrowed
{x,y,z} rather than a copy.

`measureText` re-shaped every visible label every frame for a fixed font and
an immutable label. Cached on the node.

In the renderer, every edge's material had its colour, opacity and linewidth
rewritten every frame -- with one material per edge that is tens of thousands
of redundant uniform writes, and `Color.set(string)` re-parses the string
each time. Now only written when the value actually changed. And
`syncArrowheads` built a **fresh ConeGeometry per arrowhead**, so a zoomed-in
view held ~15k identical geometries; they share one now.

### The two spikes, and two wrong guesses about them

The `max` column showed frames of 3-6 seconds. I guessed the expand/collapse
pre-settle burst first -- wrong, that was removed in an earlier phase. Then
pool growth -- real but minor (14598 new Line2 objects cost 1434ms, while the
worst frames created none). Timing the three phases of `loop()` separately
found it:

    total    tick    draw     lod
    6777ms     7ms   432ms   6339ms
    4115       6    1276     2833
    1449      11    1438        1

`checkLodTransitions` was the spike. `expandCluster()` ends with
`rebuildTierEdges()`, which walks every raw edge in every tier and recomputes
`lcaPath()` for each dependency edge -- so a frame where a dozen clusters
crossed the threshold together paid for a dozen full rebuilds. The result
depends only on the *final* materialised frontier, so batching them into one
rebuild per LOD pass is exact, not an approximation.

The second spike source is genuine pool growth, and pools also only ever grew:
zooming in and back out left tens of thousands of invisible Line2 objects in
the scene. `shrinkPool()` releases them with enough hysteresis that ordinary
zoom jitter cannot trigger a rebuild -- measured at **43,430 objects released**
on a single zoom-out.

### The LOD gate, and the flap it caused

`EXPAND_PX` is a pure *size* test, and projected size grows for every cluster
in the cohort as the camera dollies in, wherever it sits -- which is why the
whole graph expanded. Adding an on-screen requirement is the obvious fix and
the first version made things dramatically worse: 1433ms frames.

The reason is worth recording. The expand test reads a collapsed meta-node's
own position; the collapse test reads the live centroid of its materialised
children. Those are different points, and whenever they fell on opposite
sides of the margin the cluster flapped every frame -- measured at **153
expands and 156 collapses in 8 frames**. Both directions now read the
cluster's *static* hull centre (`clusterHullFor().center`, a cached centroid
over fixed WORLD_POS, identical whether expanded or not), which cannot
oscillate whatever the margins are. Churn went to zero.

### The measurement trap

This machine gets progressively slower over a session. Measuring the baseline
fully and then the optimised build showed the optimised build *losing* at deep
zoom -- purely because it ran second. Every number above that compares two
builds comes from an interleaved A/B/A/B (`tools/_ab.mjs`, three alternating
rounds, medians), not from sequential runs.

Honest result:

    metric                    baseline   optimised   change
    worst frame during zoom     3585ms      1727ms    -52%    (no overlap across rounds)
    far view, idle                21ms        18ms    -14%
    deep zoom, steady state      450ms       414ms     -8%    (within noise)
    after a zoom round trip       36ms        39ms     +8%    (within noise)

So: the jank is halved and idle is a little cheaper, and **the steady-state
cost of being zoomed in is unchanged**. An earlier, non-interleaved run had
suggested a 34ms -> 18ms recovery win; that did not survive interleaving and
is withdrawn.

### What did not work, and why it is not in the tree

**Screen-space culling of edges.** The obvious next move, and measuring it
first killed it: at full zoom only 41% of *nodes* are on screen, but **95-99%
of dependency edges still cross the viewport**. The graph is a hairball of
long edges through the middle, which is exactly where the camera is when you
zoom in. Culling nodes alone would remove ~11% of scene objects, for real
popping risk.

**An arrowhead size LOD.** ~15k arrowhead meshes is a third of everything in
the scene and almost all of them are sub-pixel, so gating them on projected
edge length looked like the biggest single win available. Built it, measured
it interleaved, and it changed deep-zoom frame time by nothing detectable
(372/414/450ms against a baseline of 377/450/453). Reverted rather than kept:
it is a visual change, and this harness cannot show it buying anything.
The likely reason it measures flat is that SwiftShader is fill-bound, so
removing draw calls for sub-pixel geometry removes almost no work -- on real
GPU hardware, where draw-call submission is the CPU-side cost, it might well
help. That is a hypothesis, not a result, which is why it is not shipped.

### What is still slow

Steady-state deep zoom is draw-call bound: ~6400 nodes + 15042 dependency
lines + 11186 semantic lines + 15042 arrowheads is roughly 47k objects, one
draw call each. None of the changes above reduce that count, which is why
`deep_med` barely moved. The real lever is merging each tier into a small
number of batched geometries (`LineSegments2` supports per-segment colour),
which needs per-edge width and opacity bucketed into a handful of materials
instead of varying continuously -- a genuine renderer change with real
regression risk, deliberately not started here.

One caveat on every absolute number in this section: they come from headless
SwiftShader, where rasterisation is on the CPU and dominates. Ratios between
builds measured back-to-back are meaningful; the milliseconds are not what
real hardware sees.

## Band filters (depth and angle)

Two knobs on a linear track for the trophic-height band, two on a dial for the
topic-angle band. Both are hand-built: `<input type="range">` has exactly one
knob, and the angle band is circular and wraps through 0°, which no native
control expresses.

Deliberately a visibility gate only. `tick()`'s forces and every node's
`WORLD_POS` are untouched, so narrowing a band never reflows the graph — the
axes only mean something if a repo stays where it was while you filter around
it.

**A repo with no measured value on a filtered axis is hidden, not matched.**
1975 of the 7051 repos have no solved trophic height and sit at y=0.5 by
convention; 2455 have no topic signal and sit on the central axis with
theta=0. Those are placeholders, not positions, so once a band is narrowed
they drop out rather than being tested against a coordinate nothing
established. The panel prints both counts whenever the corresponding band is
active, so the omission is stated rather than silent.

A cluster meta-node passes when *any* of its members does, not when its own
averaged target does — a cluster spanning the whole stack averages to the
middle, which would hide clusters full of matches and show ones with none.
Height coverage is an exact [min,max] interval per cluster; angle coverage is
a 32-bucket (11.25°) occupancy mask, which can only over-include at the edges.
That is the safe direction for a stand-in whose real members are one click
away.

Counts verified against `data/processed/` directly rather than trusted from
the page: height 0.60–0.90 gives 1539, the wrapping wedge 300°–60° gives 383,
and the two together (0.55–0.85 with 90°–200°) give 17 — each matching the
page's badge exactly.

Cost: at the default view (386 materialized nodes) the per-frame pass that
stamps every node with its band membership is below timer resolution; with
every cluster force-expanded to the full 7051 nodes — more than any real view
reaches — it is 0.5 ms. With no band narrowed the whole thing is one
short-circuited boolean per edge.
