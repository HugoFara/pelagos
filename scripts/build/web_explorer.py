#!/usr/bin/env python3
"""Render web/template.html into web/index.html, inlining the processed data
as JS literals (file:// pages can't fetch() JSON due to CORS, so the graph
explorer stays a single self-contained file instead of loading them at
runtime).

Usage: python3 scripts/build/web_explorer.py [processed_dir] [template] [out]
Defaults: data/processed, web/template.html, web/index.html
"""
import json
import sys
from pathlib import Path


def main(processed_dir="data/processed", template_path="web/template.html", out_path="web/index.html"):
    processed_dir = Path(processed_dir)
    aggregates = json.loads((processed_dir / "repo_aggregates.json").read_text())
    edges = json.loads((processed_dir / "repo_shared_edges_pruned.json").read_text())
    contrib_edges = json.loads((processed_dir / "repo_shared_contributor_edges_pruned.json").read_text())
    expansions = json.loads((processed_dir / "repo_expansions.json").read_text())
    # Full (unpruned) overlap data -- not used by the main graph, which stays
    # on the top-4-pruned edges above for readability, but Compare mode looks
    # up the real weight between an arbitrary pair even when it didn't make
    # the pruning cut (1275 stargazer / 64 contributor edges vs 151/59 pruned).
    full_edges = json.loads((processed_dir / "repo_shared_edges.json").read_text())
    full_contrib_edges = json.loads((processed_dir / "repo_shared_contributor_edges.json").read_text())

    # Real repo-depends-on-repo edges (scripts/cohort/dependency_repos.py /
    # scripts/edges/dependency_edges.py) -- grows the 51-repo cohort with the GitHub
    # repos that publish the PyPI packages a "dependency cohort" of academic
    # repos actually imports, plus a curated slice of those academic repos
    # themselves. Unlike the shared-stargazer/contributor edges, these are
    # directed and structural (A really does import B), not an audience proxy.
    dependency_aggregates = json.loads((processed_dir / "dependency_repo_aggregates.json").read_text())
    aggregates.update(dependency_aggregates)
    dependency_edges = json.loads((processed_dir / "repo_dependency_edges_pruned.json").read_text())
    full_dependency_edges = json.loads((processed_dir / "repo_dependency_edges.json").read_text())

    # Pre-fetched GitHub descriptions (scripts/fetch/repo_descriptions.py), inlined
    # per-repo so the frontend can show a repo's description on hover for
    # free instead of spending part of the unauthenticated 60-req/hour
    # GitHub API budget on every node the visitor's mouse passes over --
    # only a click still live-fetches (for the star/fork counts and
    # language, which actually do change). A repo missing here (fetch
    # failed entirely, e.g. renamed/deleted) just falls back to the old
    # hover-fetch behavior.
    descriptions = json.loads((processed_dir / "repo_descriptions.json").read_text())
    for repo, desc in descriptions.items():
        if repo in aggregates:
            aggregates[repo]["description"] = desc

    # Semantic edges (scripts/edges/topic_edges.py) -- two repos sharing GitHub topic
    # tags (e.g. both tagged "diffusion-models"), a first cut at a semantic
    # relationship independent of dependency/audience overlap. See NOTES.md.
    semantic_edges = json.loads((processed_dir / "repo_semantic_edges_pruned.json").read_text())
    full_semantic_edges = json.loads((processed_dir / "repo_semantic_edges.json").read_text())

    # A fifth "linked by a common person" repo-repo edge tier (ROADMAP.md
    # Phase 19): two repos whose issues were filed by the same people, from
    # a capped per-repo issue sample (scripts/edges/shared_issue_authors.py) --
    # same family/shape as the contributor tier above, resolved the same way.
    issue_author_edges = json.loads((processed_dir / "repo_shared_issue_author_edges_pruned.json").read_text())
    full_issue_author_edges = json.loads((processed_dir / "repo_shared_issue_author_edges.json").read_text())

    # Precomputed multi-level cluster hierarchy (scripts/clusters/hierarchy.py) --
    # lets the frontend render/simulate a bounded number of cluster
    # meta-nodes instead of every repo at once, expanding a cluster into its
    # real members lazily on zoom-in/click. See NOTES.md.
    cluster_hierarchy = json.loads((processed_dir / "repo_cluster_hierarchy.json").read_text())

    # Phase 12 coordinate system: trophic height (scripts/layout/trophic_levels.py, the
    # y-axis) and circular topic embedding (scripts/layout/topic_theta.py,
    # the theta-axis + free coherence radius) -- replaces the fully free
    # force-directed layout with a constrained one. See ROADMAP.md.
    # Repo identity (scripts/identity/repo_identity.py): which duplicate slugs each node
    # absorbed, and which non-GitHub origins serve it. Only the records that
    # actually have something to say are shipped -- a repo with one GitHub
    # origin and no aliases would be pure weight in a file that is already
    # mostly edge tuples. Optional, like every other post-Phase-7 input, so an
    # older checkout still builds.
    identity_path = processed_dir / "repo_identity.json"
    repo_identity = {}
    if identity_path.exists():
        for repo, record in json.loads(identity_path.read_text())["repos"].items():
            elsewhere = [o for o in record.get("origins", []) if o.get("forge") != "github"]
            if record.get("aliases") or elsewhere:
                repo_identity[repo] = {"aliases": record.get("aliases", []), "origins": elsewhere}

    trophic_levels = json.loads((processed_dir / "repo_trophic_levels.json").read_text())
    topic_circular = json.loads((processed_dir / "repo_topic_circular.json").read_text())

    # repo_expansions.json covers 15 repos, one of which (compvis/stable-diffusion)
    # isn't part of the 51-repo aggregate cohort -- see NOTES.md. Only expose
    # expand-in-place for repos that exist in both.
    expandable = sorted(k for k in expansions if k in aggregates)
    dropped = sorted(k for k in expansions if k not in aggregates)

    # Intern the two repo ids in every edge tuple as integer indices into a
    # single REPO_IDS table (which is just AGGREGATES' own key order, so it
    # costs nothing extra to ship). The same ~6000 id strings repeat across
    # ~190k edge tuples across the ten blobs below, and inlining them
    # literally was ~7MB of a 17MB page -- by far its largest single cost,
    # and one that is pure redundancy rather than data. The frontend
    # rehydrates each list in place at startup, so every consumer there still
    # sees plain [sourceId, targetId, weight, [members]] tuples.
    #
    # An id that somehow isn't in aggregates is passed through as the raw
    # string rather than dropped; rehydrateEdges() in the template only
    # substitutes numbers, so such an edge still resolves correctly.
    repo_index = {repo: i for i, repo in enumerate(aggregates)}

    def intern_edges(edge_list):
        return [[repo_index.get(e[0], e[0]), repo_index.get(e[1], e[1]), *e[2:]]
                for e in edge_list]

    uninterned = sum(1 for blob in (edges, contrib_edges, dependency_edges, semantic_edges,
                                    issue_author_edges, full_edges, full_contrib_edges,
                                    full_dependency_edges, full_semantic_edges,
                                    full_issue_author_edges)
                     for e in blob for endpoint in (e[0], e[1]) if endpoint not in repo_index)

    # Owners with no web/logos/{owner}.png on disk right now. The renderer
    # loads avatars lazily by owner name (scene3d.js loadAvatarTexture) and
    # already falls back to flat color on error, but it learns of a missing
    # file by requesting it -- one console 404 per absent owner, forever.
    # Shipping the absent list lets it skip the request instead. Usually
    # empty; it stops being empty every time the cohort grows before
    # scripts/fetch/repo_logos.py is re-run, and permanently for any owner
    # whose account is gone (repo_logos.py recovers renames via the API, not deletions).
    # ---- The four inputs the explorer needs to place a repo this pipeline
    # has never seen (a "vanity card"), reproducing the same computations
    # topic_edges.py/trophic_levels.py/topic_theta.py run over the cohort
    # rather than approximating them.
    #
    # 1. topic -> angle, straight from scripts/layout/topic_theta.py's
    #    spectral embedding. A new repo's theta is the TF-IDF-weighted
    #    circular mean of its own topics' angles -- the identical formula
    #    repo_circular_mean() applies to cohort repos, so a looked-up repo
    #    lands on the same axis, not a parallel invented one.
    # 2. topic -> the cohort repos carrying it, as REPO_IDS indices. Doubles
    #    as the shared-tag (semantic) edge index and as the document counts
    #    the TF-IDF weights above need (idf = log(n_docs / len(list))), so
    #    it replaces a separate counts map rather than adding to it.
    # 3. dependency coordinate -> repo, per ecosystem: the same resolution
    #    tables the per-ecosystem edges/*_deps.py scripts built from real
    #    registry lookups. Shipping them means a looked-up repo's manifest
    #    resolves with zero extra network calls. Measured on non-cohort repos: these captured
    #    100% of the dependency edges that reach this cohort at all, with
    #    live registry lookups adding none -- what limits a new repo's edge
    #    count is that most dependencies simply aren't cohort repos, not
    #    resolution coverage.
    # 4. the trophic solve's raw scale, so "one level up" means the same
    #    thing off-cohort as on it.
    topic_theta = json.loads((processed_dir / "topic_circular_embedding.json").read_text())
    repo_topics = json.loads((processed_dir / "repo_topics.json").read_text())
    trophic_scale = json.loads((processed_dir / "trophic_scale.json").read_text())

    topic_repos = {}
    for repo, topics in repo_topics.items():
        i = repo_index.get(repo)
        if i is None:
            continue
        for topic in topics:
            topic_repos.setdefault(topic, []).append(i)

    cpp_maps = json.loads((processed_dir / "cpp_port_to_repo.json").read_text())
    dep_resolution = {
        "npm": json.loads((processed_dir / "js_package_to_repo.json").read_text()),
        "pypi": json.loads((processed_dir / "python_package_to_repo.json").read_text()),
        "crates": json.loads((processed_dir / "crate_to_repo.json").read_text()),
        "maven": json.loads((processed_dir / "java_coord_to_repo.json").read_text()),
        "go": json.loads((processed_dir / "go_module_to_repo.json").read_text()),
        "vcpkg": cpp_maps.get("vcpkg", {}),
        "conan": cpp_maps.get("conan", {}),
    }
    # The PyPI table scripts/edges/resolve_pypi_packages.py built from the dump's usedPackage
    # triples covers names scripts/edges/python_deps.py never had to resolve;
    # they answer the same question, so fold it in rather than ship two.
    for name, repo in json.loads((processed_dir / "package_to_repo.json").read_text()).items():
        dep_resolution["pypi"].setdefault(name, repo)

    logo_dir = Path(out_path).parent / "logos"
    have_logos = {p.stem for p in logo_dir.glob("*.png")} if logo_dir.is_dir() else set()
    missing_logos = sorted({r.split("/")[0] for r in aggregates} - have_logos)

    template = Path(template_path).read_text()
    out = (template
           .replace("__MISSING_LOGO_OWNERS_JSON__", json.dumps(missing_logos, separators=(",", ":")))
           .replace("__TOPIC_THETA_JSON__", json.dumps(topic_theta, separators=(",", ":")))
           .replace("__TOPIC_REPOS_JSON__", json.dumps(topic_repos, separators=(",", ":")))
           .replace("__DEP_RESOLUTION_JSON__", json.dumps(dep_resolution, separators=(",", ":")))
           .replace("__TROPHIC_SCALE_JSON__", json.dumps(trophic_scale, separators=(",", ":")))
           .replace("__REPO_COUNT__", str(len(aggregates)))
           .replace("__EDGE_COUNT__", str(len(edges)))
           .replace("__CONTRIB_EDGE_COUNT__", str(len(contrib_edges)))
           .replace("__DEPENDENCY_EDGE_COUNT__", str(len(dependency_edges)))
           .replace("__SEMANTIC_EDGE_COUNT__", str(len(semantic_edges)))
           .replace("__ISSUE_AUTHOR_EDGE_COUNT__", str(len(issue_author_edges)))
           .replace("__EXPANDABLE_COUNT__", str(len(expandable)))
           .replace("__AGGREGATES_JSON__", json.dumps(aggregates, separators=(",", ":")))
           .replace("__REPO_IDS_JSON__", json.dumps(list(aggregates), separators=(",", ":")))
           .replace("__REPO_IDENTITY_JSON__", json.dumps(repo_identity, separators=(",", ":")))
           .replace("__EDGES_JSON__", json.dumps(intern_edges(edges), separators=(",", ":")))
           .replace("__CONTRIB_EDGES_JSON__", json.dumps(intern_edges(contrib_edges), separators=(",", ":")))
           .replace("__DEPENDENCY_EDGES_JSON__", json.dumps(intern_edges(dependency_edges), separators=(",", ":")))
           .replace("__SEMANTIC_EDGES_JSON__", json.dumps(intern_edges(semantic_edges), separators=(",", ":")))
           .replace("__ISSUE_AUTHOR_EDGES_JSON__", json.dumps(intern_edges(issue_author_edges), separators=(",", ":")))
           .replace("__FULL_EDGES_JSON__", json.dumps(intern_edges(full_edges), separators=(",", ":")))
           .replace("__FULL_CONTRIB_EDGES_JSON__", json.dumps(intern_edges(full_contrib_edges), separators=(",", ":")))
           .replace("__FULL_DEPENDENCY_EDGES_JSON__", json.dumps(intern_edges(full_dependency_edges), separators=(",", ":")))
           .replace("__FULL_SEMANTIC_EDGES_JSON__", json.dumps(intern_edges(full_semantic_edges), separators=(",", ":")))
           .replace("__FULL_ISSUE_AUTHOR_EDGES_JSON__", json.dumps(intern_edges(full_issue_author_edges), separators=(",", ":")))
           .replace("__CLUSTER_HIERARCHY_JSON__", json.dumps(cluster_hierarchy, separators=(",", ":")))
           .replace("__TROPHIC_LEVELS_JSON__", json.dumps(trophic_levels, separators=(",", ":")))
           .replace("__TOPIC_CIRCULAR_JSON__", json.dumps(topic_circular, separators=(",", ":")))
           .replace("__EXPANSIONS_JSON__", json.dumps({k: expansions[k] for k in expandable}, separators=(",", ":")))
           .replace("__EXPANDABLE_JSON__", json.dumps(expandable, separators=(",", ":"))))

    Path(out_path).write_text(out)
    top_level_clusters = sum(1 for c in cluster_hierarchy["clusters"].values() if c["parent"] is None)
    print(f"{len(repo_index)} repo ids interned across the edge blobs, "
          f"{uninterned} endpoint(s) left as raw strings (want 0 -- a nonzero count means some "
          f"edge names a repo that isn't in the aggregates, which is real but unexpected); "
          f"page is {len(out) / 1e6:.1f} MB", file=sys.stderr)
    if missing_logos:
        print(f"{len(missing_logos)} owner(s) have no web/logos/*.png and are marked "
              f"avatar-less so the renderer never requests one -- re-run "
              f"scripts/fetch/repo_logos.py to close real gaps: {missing_logos[:10]}", file=sys.stderr)
    print(f"wrote {out_path}: {len(aggregates)} repos, {len(edges)} shared-stargazer edges, "
          f"{len(contrib_edges)} shared-contributor edges, {len(dependency_edges)} dependency edges, "
          f"{len(semantic_edges)} semantic edges, {len(issue_author_edges)} shared-issue-poster edges, "
          f"{len(expandable)} expandable, "
          f"{top_level_clusters} top-level clusters ({len(cluster_hierarchy['clusters'])} total)", file=sys.stderr)
    if dropped:
        print(f"expansion data present but skipped (not in aggregate cohort): {dropped}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
