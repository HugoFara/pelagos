# Roadmap

Where `web/index.html` goes next. Phases are roughly ordered by
stars-per-effort, not strict sequence — see the discussion this was
distilled from for the full reasoning on each pick.

## Phase 1 — Foundations: search + shareable links (done)

The graph currently only has one entry point (visually scanning the canvas)
and one way to point someone at a specific view (a screenshot). Both are
cheap to fix and every later phase benefits from them:

- **Search-to-jump**: fuzzy substring search over the 51-repo cohort,
  keyboard-navigable, `/` to focus it.
- **Permalinks**: selected node + active edge/type toggles encoded in the
  URL hash, restored on load; a "copy link" button. Camera position is
  deliberately *not* part of the encoded state — node positions are
  reseeded with jitter on every load, so raw x/y wouldn't reproduce the
  intended view anyway; re-focusing the node does.

## Phase 2 — Path finder ("six degrees") (done)

Pick two repos, highlight the shortest path between them over the
shared-contributor (and/or shared-stargazer) graph — e.g. "how is
`torvalds/linux` connected to `pytorch/pytorch`?". Pure graph search over
data already in `data/processed/`, no new data pipeline needed. Inherently
shareable via Phase 1's permalinks.

## Phase 3 — Live repo lookup (done, scoped down from "find yourself")

Let a visitor type any `owner/repo` — not just the curated 51 — and
live-fetch it via the GitHub API (reusing the existing click-to-fetch path)
to drop it into the graph. Scoped down from the original "type your GitHub
username and see your real neighbors" idea: computing genuine
shared-contributor/shared-stargazer overlap for an arbitrary repo would
need one extra API call per cohort repo to check, which isn't viable under
the unauthenticated 60 req/hour cap. Shipped instead: a same-GitHub-owner
edge to any cohort repos under that owner (a real signal, zero extra
requests) and the live description/stars/forks panel, both wired into the
same permalink/search system as Phases 1-2. A per-user "find yourself"
(fetch someone's own repos/contributions) would need either an
authenticated token or a server component to stay within rate limits —
left for a future phase if this project grows a backend.

## Phase 4 — Embeddable README badge

A small static SVG "constellation" of a repo's nearest neighbors,
generatable for any repo in the cohort, meant to be dropped into that
repo's own README and link back to the full explorer. Each embed is free
distribution. Needs a render-to-SVG script (likely reusing
`compute_shared_edges.py`'s output) and somewhere to host the generated
images.

## Phase 5 — Auto-detected clusters (done)

Run community detection over the currently-visible repo-repo edges (a
single-level greedy modularity pass, i.e. Louvain's local-moving phase,
implemented client-side with no dependency) and color the resulting
clusters, listed in a new sidebar panel. Toggling stargazer vs
shared-contributor edges recomputes clusters live, so it doubles as a way
to see the difference between audience-driven grouping and real
technical/organizational coupling. Clusters are labeled by their
highest-degree member ("pytorch/pytorch cluster") rather than an invented
name like "PyTorch ecosystem" — everything shown in this app is a real,
derived value, never a guessed one.

## Phase 6 — Compare mode (done)

Select two repos and show their overlap as text/stats (shared contributors,
shared stargazers) rather than just an edge weight. Went a step further
than originally scoped: the build now also embeds the full, unpruned
overlap data (1275 shared-stargazer / 64 shared-contributor edges, vs the
151/59 that make the top-4-per-node pruning cut used for the main graph),
so Compare mode can report a real number for pairs that don't have a
visible edge at all — e.g. `torvalds/linux` and `openai/gpt-3` share 117
stargazers, pruned out of the main view but surfaced honestly here, next
to each repo's real aggregate stats side by side.

## Phase 7 — Real dependency edges (done)

The two repo-repo edge types up to this point (shared-stargazer, shared-
contributor) are both "linked by a common person" proxies — audience or
maintainer overlap, not an actual technical relationship. This phase adds a
third, genuinely structural signal: real `repo --depends on--> repo` edges,
resolved from the dataset's `usedPackage` triples (a repo → PyPI package
name) by tracing each package name back to the GitHub repo that publishes
it. That also grew the cohort past the original 51 — every newly-added node
either publishes a package another repo in this dataset actually imports
(168 "library" repos, e.g. `huggingface/transformers`, `explosion/spaCy`,
`google/jax`), or is one of the real repos doing that importing (a curated
top-100 slice of a ~21,100-repo "dependency cohort", ranked by distinct-
dependency count). Directed and arrowed, unlike the other two tiers — see
`NOTES.md` for the full resolution pipeline and a data quirk it surfaced
along the way (some dependency-cohort repo ids turned out to be stored
backwards).

## Phase 8 — Dependency edges as the default lens, cached descriptions (done)

Two follow-ups once the cohort had grown past 51 repos with a real
structural edge type available: first, `edgeVisible` now defaults to
dependency-only (`{ stargazer: false, contributor: false, dependency: true }`)
instead of shared-stargazer — the graph opens on the one relationship that
isn't a "linked via a shared person" proxy, matching what this project is
actually about. Second, `scripts/12_cache_repo_descriptions.py` pre-fetches
every cohort repo's GitHub description once (via `gh api`, same cache as
scripts 10/11) and ships it inline per-repo in `repo_aggregates.json`, so
the explorer's hover tooltip shows it instantly with zero network requests
instead of spending part of the unauthenticated 60-req/hour API budget on
every node the mouse passes over — at 319 repos, a normal browsing sweep
could exhaust that budget before a visitor even clicked anything. Clicking
a node still live-fetches (for the star/fork counts and language, which
actually do change); only the hover path was cacheable.

## Phase 9 — Semantic edges from shared tags (done, crude version)

A fourth repo-repo edge type: two repos sharing GitHub topic tags (e.g.
both tagged `diffusion-models`), a first cut at a subject-matter
relationship independent of the dependency/audience/organizational signals
the first three tiers capture. `scripts/13_semantic_edges.py` builds it
from the `topics` field already sitting in the cached GitHub API responses
from scripts 10-12 (184/319 cohort repos have at least one tag; the
SemRepo dump's own `foaf:topic` predicate only covers 55/319, so this uses
the cache instead — no new network calls needed either way). Same overlap
+ top-K-pruning shape as the contributor tier (min 2 shared, top-4 per
node, actual shared tags shown on hover): 348 edges over 134 nodes.
Off by default, its own legend row/color/toggle, included in six-degrees
pathfinding, Compare mode's overlap section, and cluster detection like
the other three tiers. Deliberately the cheap version — literal tag-string
overlap, not real semantic similarity — see `NOTES.md` for the planned
follow-up (NLP/embedding similarity over repo descriptions or READMEs,
both already available in this dataset).

## Phase 10 — Level-of-detail clustering (done)

The cohort can now keep growing past 319 without the graph becoming
unreadable or the browser choking on an all-pairs O(n²) force simulation:
`scripts/14_cluster_hierarchy.py` precomputes a multi-level cluster
hierarchy (hand-rolled multi-level Louvain — see `NOTES.md` for why not a
new Python dependency, and for the modularity-resolution tuning it took to
avoid a few giant blobs swallowing most of the cohort). The frontend
renders/simulates only the top-level cluster meta-nodes by default (28 for
the current 319-repo cohort) and lazily expands one into its real children
on click or zoom-in — `expandCluster`/`collapseCluster`, generalized from
the existing tier-2 click-to-expand machinery but with *replace* rather
than *coexist* semantics, so the materialized node count stays bounded
regardless of total cohort size. The old runtime single-level Louvain
("color by cluster" toggle) is retired in favor of reading the same
precomputed hierarchy. `revealRepo()` expands whatever ancestor chain
blocks a specific repo, wired into search, six-degrees pathfinding,
Compare mode, and permalink restore, so picking a repo by id still works
regardless of how collapsed the graph currently is. Verified with a
temporary headless Node+jsdom harness driving the app's real init and
interaction code end to end (not committed — see `NOTES.md`), which caught
a real bug a syntax/placeholder check alone would have missed: the
zoom-driven auto-collapse check was originally tick()-driven and could
undo its own just-triggered expansion mid-burst, permanently stalling
multi-level reveals.

## Phases 11-17 — from external review

An outside reviewer was asked specifically about originality; the answer
("well-trodden concept, but here's what isn't") plus two technical
follow-ups reshape most of what comes next, replacing several previously
loose ideas (NLP-similarity-as-future-work in Phase 9, PMI-weighting the
topic edges as a standalone fix) with one coherent design. Phases below are
sequenced by dependency, not stars-per-effort like the rest of this doc.

## Phase 11 — Name the differentiation (done)

No architecture change. README's "Why" section now states explicitly why
this isn't redundant with `deps.dev` (Google's Open Source Insights) — the
closest prior art to this project's backend and the most direct comparison
the review raised. `deps.dev` reconstructs the transitive *package*
dependency graph from registries; its node is a package/version, its one
relation is "depends on." This project's node is a *repo*, and
`shortestPath()` (`web/template.html:1951`) already searches a real
four-tier multigraph (dependency, shared-contributor, shared-stargazer,
shared-topic) as one unified adjacency. Also stated plainly:
`scripts/09_resolve_packages.py`'s package→repo resolution (PyPI-only
today) is exactly the "nobody does this cleanly" gap the review named as
the one clearly unsaturated idea in this space.

The live proof: two seeded example buttons in the path finder panel
(`.path-examples` in `web/template.html`), found by running this same
`shortestPath()` offline over the committed `data/processed/*.json` and
searching for real chains between well-known repos that cross at least two
edge tiers. Both picks turned out sharper than planned — not just
multi-tier, but pairs with **zero direct edge in the dataset across any of
the four tiers**, only a real 2-hop bridge: `pytorch/pytorch` ↔
`pytorch/vision` (same GitHub org, no direct edge; connected via 809
shared stargazers with `dmlc/dgl`, which genuinely depends on the
`torchvision` package) and `google/sentencepiece` ↔
`huggingface/transformers` (a real-world dependency everyone assumes is a
direct edge; connected via 1053 shared stargazers with
`nvidia/deeplearningexamples`, which shares 4 topic tags with
`transformers`). Clicking a button sets `pathFrom`/`pathTo`, reuses the
existing `computePath()`/`updateHash()` wiring, so the result is
immediately a real permalink. Verified end-to-end with headless Chromium
via Playwright against the actual built `web/index.html` (script not
committed): both buttons produce the expected hop breakdown, the
highlighted path draws and auto-fits on canvas, and reloading a seeded
permalink's hash restores the same result from scratch. Note: this
diverges from the jsdom+Canvas-stub harness `NOTES.md` documents for
Phase 10's verification specifically to avoid Playwright/interactive
browser use — flagged, not reconciled, see `NOTES.md`.

**Update, post-Phase-13 data backfill:** both original picks turned out to
be an artifact of a data-collection gap, not a real structural finding —
the raw shared-stargazer extraction only ever queried the dump for the
original top-50 cohort, so `pytorch/vision` (added later via dependency
expansion) had never actually been checked for a direct stargazer edge to
`pytorch/pytorch`. Once the co-star/contributor backfill (see Phase 13)
re-queried the full 319-repo cohort, both pairs turned out to have a real
direct edge after all (1052 and 956 shared stargazers respectively) — a
genuine finding, but the opposite of what the example was supposed to
demonstrate. Re-picked and re-verified against the current full data:
`dmlc/xgboost` ↔ `psf/requests` (a gradient-boosting library and the
most-used HTTP client; connected via 723 shared stargazers with
`dmlc/dgl`, which genuinely depends on `requests`) and `Kludex/starlette`
↔ `NVIDIA-NeMo/Speech` (an ASGI web framework and NVIDIA's speech toolkit;
connected via `PaddlePaddle/PaddleSpeech`, which depends on `starlette`
and shares 4 topic tags — `asr`, `speech-synthesis`, `speech-translation`,
`tts` — with NeMo). Both confirmed to have zero direct edge across all
four tiers, including shared-contributor, against the current data.

## Phase 12 — Coordinate system v2: trophic height + circular topic angle (done)

Replaces the free 2D force-directed layout with a constrained one, and
replaces "PMI-weight the topic edges" (the interim fix Phase 11's draft
floated) with a more complete design that removes the clique problem
structurally instead of by reweighting.

- **y = trophic level, not DAG depth (done).** `scripts/15_trophic_levels.py`
  solves `Λh = v` (Λ = Laplacian of the undirected dependency graph,
  `v_k = outdeg(k) - indeg(k)`, derived directly from the "consumer one
  level above what it depends on" objective rather than copied from a
  food-web paper's edge-direction convention — see `NOTES.md`) via
  `numpy.linalg.lstsq`, normalized to [0,1], with an empirical sign
  self-check (flip if real edges don't come out pointing the right way).
  Real finding: this cohort's dependency graph turns out to be exactly
  bipartite (0 chains longer than one hop, checked directly), so trophic
  incoherence comes out at exactly 0.000 — correct and meaningful, but
  today closer to a two-band split than the rich gradient the design
  targets. See `NOTES.md` for what would actually earn more levels.
- **θ = circular topic embedding, not a picked topic (done).**
  `scripts/16_topic_circular_embedding.py`: PMI-weighted topic
  co-occurrence graph (min 2 supporting repos per topic and per pair,
  positive PMI only) — verified as a single connected component before
  trusting a 2D spectral embedding of it (175/908 topics, 545 edges,
  eigenvalue gap `[0, 0.013, 0.117, ...]`). `θ_repo` is the TF-IDF-weighted
  circular mean of its topics' angles; 169/319 cohort repos get a real
  theta.
- **radius, for free (done).** The circular mean's resultant length R
  (0.211–1.000, mean 0.917 over this cohort) is exactly what ships as each
  repo's radius from the central axis.
- **Co-star and shared-contributor stop being drawn edges (done).** Both
  tiers are permanently-true internal flags now (unconditional gentle
  attraction force, excluded from the permalink hash) rather than a legend
  checkbox; `draw()` only renders one when it touches the currently
  hovered or selected node (`edgeInFocus()` in `web/template.html`) —
  verified with a real screenshot: selecting `pytorch/pytorch` surfaces a
  legible burst of its actual stargazer/contributor neighbors with
  everything else dimmed.
- **Tuning finding, not fully resolved.** The first pass (target-force
  pull toward (y, θ, r), full-strength tier-edge springs otherwise
  untouched) produced much weaker visual stratification than the trophic
  numbers implied — a dependency edge's spring wants short euclidean
  distance regardless of direction, which was fighting the y-constraint
  about as hard as the constraint itself. Fixed by damping every repo-repo
  tier edge's *vertical* force component to 12% of normal
  (`TIER_EDGE_Y_DAMPING`), leaving horizontal at full strength — matches
  the design's own framing (edges resolve local/horizontal jitter, not the
  hard y/θ constraints). Improved separation meaningfully (pytorch/pytorch
  vs. pytorch/vision y-gap: ~427 → ~635 world-px) but wasn't tuned past
  that one pass; `TROPHIC_Y_RANGE`, `TOPIC_R_SCALE`, the 0.006 target-force
  stiffness, and `TIER_EDGE_Y_DAMPING` are all real constants in
  `web/template.html` if a future pass wants to push further.

`numpy` (dense `lstsq` + `eigh`, no `scipy.sparse` needed at this cohort
size) is now this project's first tracked Python dependency, as planned.
Verified with a headless Node+jsdom harness (positions converge to target,
sign convention checked with real data, no thrown errors) plus one
deliberate real-Chromium screenshot pass for the parts that are
fundamentally about what the layout looks like — flagged as a departure
from the jsdom-only standing preference (see `NOTES.md`), not a new
default.

## Phase 13 — Cluster the semantic/social multiplex, not the dependency graph (done)

Pulled dependency edges out of the clustering substrate entirely (still
drives Phase 12's y-axis only) and reclustered on co-star PMI + topic PMI
instead of the old four-tier union. Confirms the review's concern was
real: at resolution 1.0 clustering the old union produced communities that
separated mostly by ecosystem/package manager; the new substrate produces
12 clusters instead, 9 of them large enough to read as clearly thematic
categories — web-frameworks/async, generative-AI/LLM-chat, classic-ML/
data-science, computer-vision/deep-learning, NLP/transformers, core-
PyTorch, notebook/interactive tooling, general-purpose libraries, and
Pallets-adjacent micro-libs (plus 3 small real pairs) — not one of them an
npm/PyPI boundary. See `NOTES.md` for the actual member lists and the
resolution search (1.0-3.0) that found this.

- **Sparsified first.** Mutual-kNN (`k=20`, from the reviewer's suggested
  15-30 range) before clustering — real pruning on this cohort, not a
  no-op (1543 → 757 edges).
- **Leiden, not Louvain** (`leidenalg`/`python-igraph`, this project's
  second tracked Python dependency after Phase 12's numpy) — Louvain's
  known internally-disconnected-community bug (Traag, Waltman & van Eck
  2019) is why this overrides the earlier deliberate choice to hand-roll
  Louvain rather than take a dependency (see `NOTES.md`). Hierarchy built
  via recursive Leiden, reusing Phase 10's coarsen-and-repeat scaffold
  unchanged.
- **Supernodes inherit the coordinate system** — already true before this
  phase started: `web/template.html`'s `clusterLayoutTargetFor()` was
  built during Phase 12 specifically anticipating this (`y_cluster` = mean
  member trophic level, `θ_cluster`/`R_cluster` = circular mean of member
  θs), and "meta-edges = summed crossing dependencies" was already true
  via the frontend's existing `buildTierEdges`/`materializedAncestorOf`
  aggregation, independent of anything this script computes. Only the
  visual-radius part needed a real change: `clusterRadius` is now
  proportional to `log(memberCount)` rather than `sqrt(normalized count)`,
  since this substrate's cluster sizes (2-25) span a much wider relative
  range than the old one's (2-123 but mostly bunched near the top).
- **Real, honest finding, not fixed:** the raw co-star bipartite only
  covers the original top-50 cohort, and only 184/319 repos have any
  topic tag at all — so 166 of 319 repos get zero clustering signal and
  correctly render as standalone repos rather than a guessed grouping,
  same principle as the earlier singleton-cluster fix. Net effect: 178
  top-level entities (12 clusters + 166 repos) versus the old union's 28 —
  a real decluttering regression, left as-is rather than papered over by
  loosening PMI support thresholds or smuggling dependency back into the
  substrate. See `NOTES.md`.

**Update, co-star/contributor data backfill:** the "raw co-star bipartite
only covers top-50" finding above turned out to be a fixable data-
collection gap, not a structural fact about the dump — `05_shared_stargazers.sh`/
`07_shared_contributors.sh` had only ever been *queried* against the
top-50 list, not against the full 319-repo cohort, even though the
underlying dump has real hasStargazer/hasContributor coverage well beyond
those 51 repos. Re-running both against the full cohort recovered real
signal for 88 repos (stargazer, up from 51) and 89 repos (contributor, up
from ~31), shrinking the zero-signal population from 166 to 155 and
growing the cluster count from 12 to 14 — still zero ecosystem-boundary
blobs, now including a distinct gradient-boosting cluster (`catboost`,
`xgboost`, `LightGBM`, `prophet`) that didn't separate out before. This
also surfaced a real direct `pytorch/pytorch` ↔ `pytorch/vision`
shared-stargazer edge that invalidated Phase 11's original path-finder
examples — see that phase's update note.

## Phase 14 — Real similarity signal (coverage, not noise) (done)

Embeds `description + topics + a cleaned README first paragraph` per repo
with `BAAI/bge-small-en-v1.5` (`fastembed`, ONNX runtime — this project's
third tracked Python dependency, no PyTorch/CUDA needed) and fuses the
result into Phase 13's clustering substrate alongside co-star PMI and
topic PMI. New pipeline steps: `scripts/17_fetch_readmes.py` (README raw
markdown, cached, `gh api` — 319/319 fetched, only 1 repo has none),
`scripts/18_text_embeddings.py` (cleaning + embedding — 317/319 repos end
up embeddable, versus the ~155/319 the two PMI signals alone could reach).
Real coverage win: the "no clustering signal" population drops from 166
(Phase 13) to 84 — see `NOTES.md` for the two real problems found and
fixed along the way (badge/RST-image false paragraphs, and a "generic
academic-paper-README" mega-cluster that needed a text-tier-specific
mutual-kNN, not just a cosine threshold, to break apart into genuinely
distinct research sub-themes).

**The contrarian-claim test, run and answered.** `scripts/
19_costar_circular_embedding.py` builds a co-star-PMI-driven θ (spectral-
embeds the repo-repo co-star graph directly, no aggregation step needed
unlike the topic version) and `scripts/20_compare_theta_sources.py`
compares it against Phase 12's topic-driven θ using within-cluster
circular concentration against Phase 14's own real Leiden clusters as
ground truth. Result, on the 53 repos both sources cover: co-star-driven θ
is genuinely more precise (weighted-mean R 0.959 vs. topic's 0.928) —
the review's intuition holds up empirically. But co-star only reaches
79/319 repos (the raw stargazer data's top-50 scope, even after the
Phase-13-adjacent backfill) against topic's 169/319 — cutting real θ
coverage by more than half for a few points of precision isn't a good
trade given this phase's own "coverage, not noise" framing, so topic-
driven θ stays the shipped axis. Not a wasted test: it's a real, checked
answer (co-star *would* be the better signal at greater scale/coverage),
just not one this cohort's current co-star data can afford to act on yet.

## Phase 15 — Cluster naming and cross-snapshot stability (done)

Two follow-ups that only become necessary once Phase 13 ships Leiden: it
isn't deterministic run-to-run, unlike today's hand-rolled Louvain, so
re-running the pipeline on refreshed data would otherwise reshuffle
cluster membership and IDs wholesale — silently breaking every permalink
that points at a cluster. Match clusters across snapshots by Jaccard
similarity on membership (Hungarian matching for the assignment) and
propagate IDs/labels forward instead of recomputing from scratch.
Labeling itself: c-TF-IDF over member descriptions to get top terms, then
a single LLM call per cluster to turn those terms into a readable label —
one offline pass, cached permanently, not a runtime dependency.

Both shipped as written. `scripts/21_stabilize_cluster_ids.py` keeps a
`repo_cluster_hierarchy_prev.json` snapshot, matches each run's clusters
against it per level via Jaccard-on-flattened-membership solved with
`scipy.optimize.linear_sum_assignment` (already installed transitively
via leidenalg/fastembed, no new dependency), and renames matched clusters
back to their old id (children/parent/topLevelIds/edgesByLevel references
all rewritten consistently); an unmatched cluster mints a fresh id from a
counter that never collides with anything either snapshot has used. This
cohort's real data hasn't produced two genuinely different clustering
runs yet (a small `FIRST_PASS_RESOLUTION` perturbation reproduced the
identical partition), so the matching logic was verified against a
hand-built synthetic before/after pair instead, covering same-membership-
different-index, ~67% overlap (still matches), ~20% overlap (correctly
does *not* match, mints fresh), and a wholly new cluster (fresh id, no
collision) — all passed. The 0.5 Jaccard match threshold itself is picked
from general dynamic-community-matching practice, not tuned against this
project's own before/after data, for the obvious reason that no such data
existed until this script started producing it; worth revisiting once a
few real refreshes have accumulated.

`scripts/22_label_clusters.py` handles naming, and runs into a real
tension with an earlier decision worth naming honestly: `web/template.html`
carried a comment since Phase 10 justifying hub-name labels specifically
*against* an invented one ("everything shown in this app is a real,
derived value, not a guessed one"). This phase overturns that, but tries
to keep faith with the underlying principle rather than abandon it: the
label is derived from real member text (the same cleaned description/
topics/README paragraph `scripts/18_text_embeddings.py` already produces
per repo), reduced to each cluster's most distinctive terms via c-TF-IDF
(Grootendorst 2022 — a term common to every cluster scores low
automatically, no hand-maintained stopword list needed beyond ordinary
English function words), and only then handed to a single `claude -p`
call (this project's first non-Python-library dependency — the `claude`
CLI itself, invoked offline with `--tools ""`) whose job is turning
already-real terms into readable phrasing, not inventing a theme. Falls
back to a plain terms-only heuristic label if that call fails or times
out (one of the 20 real clusters did, on the first run — succeeded on a
retry), so the pipeline degrades instead of hard-failing. Labels are
cached permanently by cluster id + a membership-signature check
(`data/processed/cluster_labels.json`, committed), so a rerun with
unchanged clusters costs zero LLM calls — confirmed directly: a second
run over the same 20 clusters reused 18 from cache and only recomputed
the 2 that had been deliberately invalidated, finishing in 18s versus the
first run's 5+ minutes. Full label set and the sidebar/tooltip/canvas
wiring: see `NOTES.md`.

## Phase 16 — Hierarchical edge bundling (done)

A direct consequence of Phase 12's θ becoming meaningful: dependency edges
will now visibly cross topic sectors in bulk (a Python web repo depending
on `requests`, which lives in a different sector) instead of being
visually incidental like today. Needs real hierarchical edge bundling
once that's live, or the zoomed-out view hairballs right back after all
this work to avoid exactly that.

Routes each dependency edge (the one tier drawn edge-to-edge across the
whole graph by default) through its two endpoints' shared ancestor in the
existing cluster hierarchy (`CLUSTERS`, the same Leiden/co-star+topic tree
Phases 10/13 already compute and Phase 15 stabilizes) rather than drawing
a dead-straight chord across the topic circle: two edges that share an
ancestor now share a real waypoint and visually converge, the classic
Holten (2006) technique, generalized here to 3D since the waypoints are
just world positions like anything else in this view.

- **Path computed once, resolved every frame.** `lcaPath(sourceId,
  targetId)` walks each endpoint's static ancestor chain
  (`ancestorChain`/`staticParentOf`, the same tree `materializedAncestorOf`
  already used) to find the lowest common ancestor, stamped onto each edge
  as `e._pathIds` inside `rebuildTierEdges()` -- cheap, and only needs
  redoing when the materialized frontier itself changes (expand/collapse),
  not every tick. `draw()` resolves those ids to actual world positions
  every frame via `controlPointPosition()` (live node position if
  materialized, real centroid of an expanded cluster's current children if
  it's mid-subtree, precomputed static layout target as a last resort) and
  blends them toward the straight line by `EDGE_BUNDLING_STRENGTH` via
  `bundledControlPoints()` -- positions move every tick, the tree route
  between two given nodes doesn't, so splitting the work this way avoids
  redoing the tree search for something that isn't changing.
- **Real, honest finding that changed the design mid-build:** measured
  directly (not assumed) that only 13 of the 863 real dependency edges
  share a genuine cluster ancestor at full expansion -- expected in
  hindsight, since Phase 13 deliberately pulled dependency edges *out* of
  the clustering substrate entirely, so the two structures mostly don't
  overlap. Shipping bundling as originally scoped (skip anything without a
  real shared ancestor) would have left 98% of dependency edges completely
  unchanged, not solving the hairball problem this phase exists to fix.
  Fixed by rooting the whole `CLUSTERS` forest at one synthetic hub sitting
  at the exact world origin -- the standard fix for hierarchical edge
  bundling over a forest rather than one tree (Holten's own examples
  always assume a single root), and still a real, derived waypoint (the
  literal center of the coordinate system the trophic axis already runs
  through), not an invented graph node. After that fix, all 863 dependency
  edges bundle through at least one real waypoint.
- **Bundling strength tuned down from Holten's own value.** His ~0.85
  assumes a deep multi-level hierarchy sharing the pull across several
  waypoints; most routes here bottom out at a single hop to the synthetic
  root, so 0.85 would bow nearly every cross-cluster edge in hard toward
  one exact point. Shipped at 0.55 -- a visible but not overwhelming pull;
  not run through a tuning pass beyond that one judgment call, same
  caveat as Phase 12's `TIER_EDGE_Y_DAMPING`.
- **Rendering, picking, and arrowheads all generalized to a multi-point
  route**, not just the bundling math: `scene3d.js`'s `syncEdgeTier` now
  smooths a `>2`-point route into a `CatmullRomCurve3` instead of a flat
  2-point `Line2`; `pickCore`'s edge fallback tests every segment of the
  actual drawn route instead of just the raw source/target chord (a
  dedicated unit test in `scene3d.test.mjs` places a screen point on the
  old straight chord and confirms it now correctly misses); dependency
  arrowheads orient off the route's *final* segment so they still back off
  the target surface along the real approach direction instead of
  pointing through a detour.
- Verified with the same jsdom+debug-hook harness pattern as Phase 18
  (not committed -- see `NOTES.md`): full-expansion path-id sanity checks,
  a measured before/after bundled-edge count, endpoint-exactness and
  real-deviation-from-straight-line assertions per bundled edge, and a
  full re-run of the Phase 18 regression pass (search/six-degrees/compare/
  external-lookup/toggles/theme/permalink) against the changed file.
  Visual judgment of how the bundled curves actually look is, same as
  Phase 18, a manual real-browser check rather than something jsdom can
  confirm.

## Phase 17 — Full-dump scale-out (flagged, not scheduled)

The review's scale argument — dedup fork families onto their upstream and
drop zero-signal nodes (0 stars, 0 dependents, 0 topics, no release)
rather than sampling, because that's cleaning, not subsetting — is sound
and worth doing at whatever scale this project actually reaches. But its
"10⁸ → 10⁶ nodes" framing assumes a full-GitHub crawl this project doesn't
have: the SemRepo dump this pipeline reads from contains **108,283**
`hasPublicRepository` triples total (`data/processed/predicate_counts.txt`),
not 10⁸. Applying the same cleaning logic to the full dump would land
somewhere well under 10⁵ repos with any real signal, not 10⁶ — genuinely
reaching 10⁶ would mean a new raw data source (a live GitHub API/GH
Archive crawl), which is a separate, much larger project decision, not a
rendering upgrade.

If and when a larger cohort is worth pursuing (dump-wide, short of a new
crawl): mutual-kNN sparsification before clustering (Phase 13 needs this
regardless of scale), WebGL/instanced rendering (deck.gl or regl — canvas
plus the current hand-rolled `tick()` simulation caps out well under this)
replacing the current renderer, offline-precomputed tile-pyramid LOD (top
level first, each cluster laid out inside its parent's cell, matching
Phase 12/13's already-constrained y/θ — never a global force-directed
pass) replacing today's client-side simulation entirely, and a
self-contained `web/index.html` stops being viable as the delivery format
(a single inlined-JSON file doesn't scale to tiles) — the biggest single
architectural break from everything shipped so far. Worth a dedicated
decision before starting, not a default extension of Phase 10's LOD work.

Phase 18 below moves to WebGL well ahead of this phase's own trigger and
at a small fraction of the node count that motivated floating it here —
a deliberate call at the current ~319-node scale, not evidence this phase
has quietly started. See Phase 18 for why that's not a contradiction: it
keeps the single-*delivery-model* property in spirit (a static sibling
asset via `<script src>`, zero `build_web_explorer.py` changes) even
though it's no longer literally one file, which is a materially smaller
break than the tile-pyramid/inlined-JSON rework described above.

## Phase 18 — Real 3D view (three.js/WebGL) (done)

The layout already computed three real spatial components per repo
(trophic height `y`, circular topic embedding `theta`/`r`), but
`layoutWorldPos()` only ever projected two of them onto a 2D canvas —
`r*sin(theta)`, the natural depth axis, was computed nowhere and used
nowhere; the topic "circle" was literally flattened onto a line. The
user's own call, made explicitly over a lighter alternative (a hand-rolled
perspective projection kept inside the existing dependency-free Canvas 2D
context, which would have stayed fully jsdom-testable and added zero
dependencies): a real Node/npm project (`web/package.json`, first in this
repo) using `three` + WebGL instead, rendered through a new `web/src/
scene3d.js` module bundled via esbuild to a committed `web/scene3d.bundle.js`
(~520KB minified — a real, conscious size jump from this project's
previous zero-dependency page).

Hybrid rendering, not "everything in WebGL": node fills/avatar textures,
edges (one `THREE.Line2` per edge, not a merged per-tier geometry, so
selection/hover/dim state can still be per-edge like it always was), and
dependency-tier arrowheads go through WebGL, since those are the parts
that actually need real depth/occlusion. Rings, badges, dashed borders,
and labels stay Canvas-2D on a second `#overlay2d` canvas layered on top,
positioned via screen-space projection instead of the old world-space
transform — reimplementing all of `draw()`'s existing stateful decoration
as raw WebGL materials would have been high-risk, high-effort, and
pointless at this node count (this phase's own trigger condition above
pins WebGL/instancing value at 10⁵-10⁶ nodes). Picking, node dragging
(constrained to a screen-parallel plane frozen at drag-start), LOD
auto-expand/collapse, and camera framing all moved off the old 2D
`transform`/`findNodeAt` machinery onto `OrbitControls` and real
perspective-aware screen-space queries.

`headless-gl` — the only real option for a WebGL context in plain Node —
turned out to only implement WebGL 1.0, and three.js's `WebGLRenderer`
requires WebGL2 unconditionally; it's also unmaintained upstream with
known vulnerabilities, so not worth adding even if it did work. Real WebGL
pixel-level verification is manual (a human in a real browser), not
Playwright — jsdom/Node verification stays exactly as capable as it
already was for pure math and logic (physics, and now also picking/
projection/camera-framing, which turned out to be substantial and
directly unit-testable with no GPU needed — `web/src/scene3d.test.mjs`,
this repo's first committed test file). Full build/verification history,
including a real `this`-binding bug the test suite's own construction
initially masked, is in `NOTES.md`.

## Phase 19 — Issue-poster edges + issue-text semantics (done)

Both follow-ups floated when this phase was first flagged: a fifth
repo-repo edge tier (shared issue posters) and issue titles folded into
the Phase 14 text-embedding signal. Unblocked by picking a concrete
sampling design — the thing that kept this "flagged, not scheduled": a
capped, first-encountered-in-file-order sample of 300 issues per repo,
resolved via a two-hop join (`scripts/23_shared_issue_authors.py`) since
`hasIssueAuthor`'s subject is the *issue*, not the repo. 300 was picked by
measuring, not guessing — 40 (an arbitrary first guess) yielded exactly
one edge after pruning; 100/150/200/300 were each measured directly, and
300 was where coverage (59/74 of the issue-covered repos retain a pruned
edge) stopped improving fast enough to justify the extra grep cost
(~53s/run over the 12GB dump either way, cheap regardless).

- **Real, honest coverage finding, matching this pipeline's other gaps:**
  only 74 of the current 319-repo cohort have *any* `hasIssue` data in
  this dump at all — measured directly, not assumed. Of those 74, real
  issue counts range from single digits into the tens of thousands
  (`pytorch/pytorch` alone has 20000, the dump's own per-repo cap), so the
  300-cap sample is a thin slice for the busiest repos and exhaustive for
  the quietest ones — same "sample, not full neighborhood" idiom
  `04_repo_expansions.sh` already uses for individual-level issue nodes.
- **Real data-quality fix, found by measuring before shipping:** an
  uncapped first pass surfaced `"ghost"` (GitHub's shared placeholder for
  a deleted account — every repo with any issue from a since-deleted user
  gets the *same* value) and bot accounts recorded as a full nested URI
  under `person/` (`<person/https://semrepo.org/bot/{name}[bot]>`, a real
  quirk specific to this predicate, not seen in `hasStargazer`/
  `hasContributor`) polluting the overlap graph — both would fabricate a
  shared-person edge between otherwise-unrelated repos. Filtered out
  before computing overlap; re-measuring after the fix confirmed it
  mattered (a cap that looked adequate pre-filter needed raising post-filter
  to reach the same real edge count).
- **A fifth repo-repo edge tier: shared issue posters,** same family as
  Phase 7's shared-stargazer/shared-contributor "linked by a common
  person" proxies and Phase 12's demotion of those two — not a legend
  toggle, always contributing a gentle attraction force, only ever drawn
  when it touches the currently hovered or selected repo
  (`palette.edgeIssueAuthor` reuses `--n-issue`, the existing color for
  individual "issue" nodes, same dual-purpose convention `--n-contrib`
  already established). 137 edges over 59/74 covered repos after the same
  min-2-shared/top-4-pruned shape the contributor tier uses. Included in
  Compare mode, six-degrees pathfinding, and hover tooltips exactly like
  the other four tiers.
  - **Correction (Phase 20).** This phase reported its top edge as
    `rwightman/pytorch-image-models` ↔ `huggingface/pytorch-image-models`,
    106 shared issue posters, "the real `timm` library rename, not a
    coincidence." The rename was real; reading it as an edge was not.
    Those are two slugs of **one repository**, so the edge was that repo
    linked to itself and the 106 shared posters were just its own issue
    filers counted twice. The same pair also led the shared-stargazer tier
    (at the maximum weight the cohort can express) and the semantic tier.
    Phase 20 collapses it, along with 24 other duplicate slugs. The tier
    itself is unaffected: re-measured, its strongest real edge is
    `flashlight/wav2letter` ↔ `flashlight/flashlight` at 26 shared issue
    posters — two genuinely distinct repos in one org, which is the kind of
    relationship this tier was meant to surface. Three self-loops were
    removed from the tier in total.
- **A real, checked finding from wiring this into pathfinding:** exhaustive
  search over every repo touched by this tier confirms real cohort data
  never actually routes a six-degrees shortest path through it — every
  issue-poster-linked pair already has an equally-short or shorter
  connection via another tier. The tier is real, additive display/Compare
  signal, not a new pathfinding shortcut, in this cohort as it stands
  today. The pathfinding wiring itself (`tierLabel` map, hop rendering) is
  still real and separately verified by isolating the other four tiers and
  confirming an issue-poster-only pair renders correctly rather than
  `undefined` — the exhaustive search on live data couldn't exercise that
  path, so it needed its own targeted check.
- **Issue titles folded into Phase 14's text-embedding input,** capped
  shorter than the README paragraph (20 titles, 400 chars) since issue
  titles are individually short incident-report phrasing ("crash on
  startup") rather than prose about the project as a whole —
  `load_issue_titles()` in `scripts/18_text_embeddings.py`. Only thickens
  signal for the 74 repos that have any (this doesn't close a coverage
  gap the way the README paragraph did for Phase 14). Re-running the full
  downstream chain (`18` → `14` → `21` → `22`) measured a controlled,
  bounded perturbation: exactly the 74 affected repos' embedding vectors
  moved (avg cosine similarity 0.92 against the pre-change vectors), the
  other 243 embedded repos stayed bit-for-bit identical (cosine 1.0).
  Cluster count held at 20; Phase 15's stabilization machinery — until now
  only verified against a hand-built synthetic before/after pair, since
  this cohort's data had never actually produced two different real
  clustering runs — matched 19 of 20 clusters to their previous stable id
  on this first real test, re-minting only one (the `openmoss/moss`-hub
  cluster reshaped into a more thematically coherent speech/LLM-chat/
  diffusion grouping, re-labeled "Speech and Language AI" by
  `scripts/22_label_clusters.py`).
- Still true, and still worth naming honestly: the dump has a per-issue
  **title**, not a body/description field — no `hasIssueBody` or similar
  predicate shows up in `data/processed/predicate_counts.txt`, so
  "title+description" as originally floated is really just "title".
- Verified with the same jsdom+debug-hook harness pattern as Phases 16/18
  (not committed — see `NOTES.md`): edge-tier construction, `edgeInFocus`
  gating, Compare mode's real overlap count, the exhaustive pathfinding
  search above, the isolated-tier hop-rendering check, and a full re-run
  of the Phase 18 regression pass (cluster expand to 319 repos, six-
  degrees, compare, external lookup, toggles, theme, permalink) against
  the changed file — all pass. Visual judgment of the new edge tier's
  color/rendering is, same as Phases 16/18, a manual real-browser check
  rather than something jsdom can confirm.

## Phase 20 — One repo, many ecosystems; one node, many origins (done)

Two limits that had been baked in since the cohort was first collected, both
in the same place: what the pipeline thinks a repo *is*.

**A repo is not one language.** Six dependency sources each read one
`{lang}_ecosystem_repos.txt` and probed one fixed manifest path at repo root.
That list came from GitHub's `language:` facet, which reports a repo's
dominant language by bytes — one value per repo. Measured on a random 240-repo
sample by reading full git trees: 20% carry a root manifest for an ecosystem
other than their own, 41% carry one at some depth, 11% have their own
ecosystem's manifest only nested, and 10% have no root manifest at all while
shipping real nested ones — reported as dependency-free when they were not.
`scripts/42_scan_repo_manifests.py` replaces the six blind probes with one
tree read per repo; `scripts/manifests.py` is the shared read side all six
edge scripts now use. Vendored trees and byte-identical copies of other
projects' manifests are excluded at read time, the second by content hash
because a directory-name rule cannot see a checked-in `transformers/`.

**A node is a repository, not a GitHub slug.** The shipped 7,051-slug cohort
held 5 rename pairs as separate nodes and 62 fork nodes, 18 shadowing an
upstream that was also a node. Identity is decided by intrinsic git object ids
(`scripts/43_repo_refs.py`, `scripts/44_repo_identity.py`) — a hash of an
object's content, so two origins serving one repository agree by construction
— layered over GitHub's own rename and fork metadata.
`scripts/45_apply_identity.py` collapses the duplicates out of
`data/processed/`. 7,051 slugs → **7,026 repositories**.

### Measured outcome

The multi-ecosystem sweep found **1,936 repos (32.1% of those with any
manifest) declaring dependencies in more than one ecosystem** — 17 of them in
all six — and 3,021 repos whose manifests live only below the root, where no
fixed-path probe could reach. Per ecosystem, edges before → after:

| | edges | source repos |
|---|---|---|
| python | 372 → **21,085** | 37 → **1,805** |
| js | 2,233 → **16,026** | 366 → **1,524** |
| rust | 7,905 → **15,484** | 676 → **1,163** |
| go | 7,140 → **9,532** | 741 → **954** |
| java | 3,376 → **3,527** | 579 → **680** |
| cpp | 407 → **622** | 150 → **247** |

Combined: **42,846 → 80,792** dependency edges (24,812 after pruning), and
repos with a real trophic height went from 72% to **84.8%** of the cohort.

Python's 57× is not a fluke of method: PyPI dependencies were supposed to
arrive from the SemRepo dump's `usedPackage` triples, so the GitHub-search
stream never grew a real Python cohort — its language list had 68 repos in it.
2,335 repos turn out to carry a Python manifest.

**The number this was actually aimed at: dependency edges crossing an
ecosystem boundary went from 3.2% to 18.6%** (1,368 → 15,038). And the trophic
axis stopped being one band per ecosystem — Rust's median height was 0.365
against Java's 0.684, each ecosystem floating in its own disconnected
component; every ecosystem now lands between 0.57 and 0.67, on a shared scale.
Python's 1,626 repos had been pinned at a single flat height (p10 0.5654, p90
0.5685) and now have real spread.

Spot-checked rather than assumed, with the declaring file identified for each:
`jupyter/notebook` → `jupyterlab/jupyterlab` via 76 `@jupyterlab/*` packages in
`app/package.json`; `react/react-native` → `babel/babel` (React Native's
dominant language is C++ by bytes, so it could never contribute npm edges);
`halo-dev/halo` (Java) → `ueberdosis/tiptap` via `ui/packages/editor/package.json`.

76 non-GitHub origins verified and recorded, so `torvalds/linux` is now marked
as a mirror of git.kernel.org rather than presented as the kernel's origin —
the point being that the bottom of the trophic axis was populated by mirrors
standing in for repositories this dataset does not contain. The repo panel
shows both facts: which slugs a node absorbed, and which other forges serve
it, with the shared-object-id count behind each claim.

- Software Heritage's argument is right and its API is the wrong tool for it.
  SWHIDs for a git origin *are* the git object ids (verified: 940/940 of
  kernel.org's Linux tags), so `git ls-remote` gets the same identifiers with
  no rate limit and no crawl-coverage dependency. What SWH could uniquely add
  is *discovery* of sibling origins, which its public REST API does not
  expose — that needs the graph dataset on AWS Open Data, a separate project.
  Until then candidate upstreams are curated and **verified**: 8 of 84 were
  rejected on the evidence, including `golang/go` against go.googlesource.com
  at 0.087 containment.
- Merging uses containment, not Jaccard. The Linux pair is 1,881 shared ids
  at containment 1.000 and Jaccard 0.42 — a mirror is *expected* to be a
  strict subset, and Jaccard punishes exactly that.
- Layer 3 found two merges GitHub's metadata structurally could not:
  `Rust-GPU/rust-gpu` ← `EmbarkStudios/rust-gpu` (845 shared ids, an
  unrecorded project transfer) and `catboost/catboost` ← `vj-thakur/catboost`.
- Phase 19's headline finding is corrected above: its top shared-issue-poster
  edge was one repository linked to itself.
- Two bugs caught by disbelieving a number rather than by a test: a transient
  GitHub throttle cached as a permanent "no refs" for 45% of the cohort, and
  a duplicate's stats overwriting the real repo's across the two aggregate
  files. Both are written up in `NOTES.md`.

**Still open, and worth naming.** Fixing the *identity* of the bottom of the
trophic axis is not the same as filling it. The most-depended-on repos in this
cohort are still all language-level libraries — `pytorch/pytorch`, `tqdm/tqdm`,
`python-pillow/Pillow`, `psf/requests` — because no language registry crosses
the C boundary: `Pillow` has in-degree 1239 and `madler/zlib`, which it
actually depends on, has effectively none. The cross-language edges that would
fix that exist only in distro package sets (Nixpkgs derivations being the
cleanest to evaluate), and seeding real nodes from kernel.org, sourceware,
freedesktop and Savannah is the other half. Both are deliberately out of scope
here: this phase makes those additions a data change rather than a schema
change, which is what had to come first.

## Phase 21 — Fill the bottom: cross-language edges from a distribution (done)

Phase 20 fixed the *identity* of the bottom of the trophic axis. It did not
fill it. The most depended-on repos were still all language-level libraries —
`pytorch/pytorch`, `tqdm/tqdm`, `python-pillow/Pillow`, `psf/requests` — with
nothing underneath, because no language registry packages C libraries. npm
knows `sharp` needs `libvips` only as an opaque string; PyPI knows `Pillow`
needs libjpeg not at all. That is a structural gap in every registry, not a
coverage gap in this pipeline.

Distributions are the only place those edges exist, because a distribution is
the thing that has to resolve them. `scripts/46_debian_dependency_edges.py`
reads Debian's binary `Depends` — 68,750 binary packages, 37,588 source
packages, two static files behind no auth — and turns it into repo→repo edges
in the same tier as every other ecosystem.

**The example that motivated all of this now exists:**

```
python-pillow/Pillow          y=0.4607
    -> libjpeg-turbo/libjpeg-turbo  y=0.3460   via libjpeg62-turbo
    -> madler/zlib                  y=0.3366   via zlib1g
    -> bminor/glibc                 y=0.3255   via libc6
```

Dependency in-degree for the foundational libraries, all of which were
effectively zero before:

| | dependents | trophic height |
|---|---|---|
| `bminor/glibc` | **947** | 0.3255 |
| `python/cpython` | 583 | 0.3193 |
| `gcc-mirror/gcc` | 421 | 0.3448 |
| `madler/zlib` | **194** | 0.3366 |
| `GNOME/glib` | 138 | 0.3701 |
| `openssl/openssl` | 112 | 0.3555 |
| `pnggroup/libpng` | 51 | 0.3562 |
| `libjpeg-turbo/libjpeg-turbo` | 44 | 0.3460 |

**895 nodes now sit below the median language-library height.** The cohort
grew from 7,026 to 8,253 repositories, dependency edges from 80,792 to 87,695,
and repos with a real computed height from 84.8% to 86.8%.

### Resolving a Debian package to a repository

This is the whole difficulty, and the distribution of it is the finding:
**42.3% of Debian source packages carry a github.com URL, but those cover only
11.2% of all reverse-dependency mass.** Auto-resolution finds the leaves and
misses every root — glibc (23,246 reverse-deps), gcc (16,285), zlib (2,787),
openssl (1,108) and ncurses (982) publish on gnu.org, zlib.net and
invisible-island.net. Three layers, in increasing order of how much they can
be trusted on their own:

1. **The package's own declared URL** — 15,872 packages. Not a guess: the
   package is naming its own homepage.
2. **A curated file** (`data/repo-lists/distro_upstreams.txt`) for the top
   unresolved packages by reverse-dependency count — 106 entries, each
   corroborated before use.
3. **Name matching against the cohort** — 364 entries. Debian's `pillow` is
   `python-pillow/Pillow`, but its Homepage is `python-pillow.github.io`, a
   Pages URL the regex skips. This layer is what actually produced the
   cross-language edges, since it connects the repos already on the map.

Layers 2 and 3 are candidate generators, never evidence, and are corroborated
by **version-tag match**: the upstream version Debian ships has to appear
among the repository's own git tags. The check earns its keep —
**239 of 603 name matches were refused by it**, including Debian's `glance`
(OpenStack's image service) against the cohort's unrelated `glanceapp/glance`.
Of the curated file, 20 candidates were dropped outright during development
because the repo did not exist or was a stale mirror whose tags never matched.

- **Runtime `Depends` only, never `Build-Depends`.** Build dependencies are
  the toolchain, which is a different relation; including them would put gcc
  *above* everything rather than beneath it. Same call this codebase already
  makes for npm `devDependencies`, Maven `scope=test` and Go's `// indirect`.
- **Debian rather than Nixpkgs**, which is the cleaner graph and the better
  long-term source but needs `nix` on the machine to evaluate. Debian
  publishes the same relation as two static files. Nixpkgs remains the right
  upgrade when a `nix` dependency is acceptable.
- **Node admission is capped.** 14,714 auto-resolved repos are outside the
  cohort; admitting all of them would be exactly the "200,000 nodes of which
  80% is noise" failure. A repo becomes a node only if already in the cohort
  or carrying ≥5 reverse-dependencies — 1,227 added, median 362 stars.

**Still open.** cairo, dbus, fontconfig, libdrm, libX11, libxcb, mesa,
wayland, eigen, binutils, readline, ncurses, giflib, libcap, x264, GNU gsl and
libssh are all absent, and each was *tried and rejected* by corroboration:
they publish only on gitlab.freedesktop.org, sourceware or savannah with no
GitHub repository, and the GitHub mirrors that do exist are stale forks whose
tags do not match what Debian ships. They cannot be nodes until a node id can
be something other than a GitHub slug — the remaining half of Phase 20's work.
Guessing a plausible-looking mirror for them would put fabricated edges under
the most load-bearing part of the graph, which is the one place this project
can least afford them.
