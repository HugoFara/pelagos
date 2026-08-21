# `scripts/`

The pipeline that produces `data/processed/` and `web/index.html`, grouped by
what a stage does. **Run order is not the directory listing** -- it is the
*Pipeline* section of [`../docs/PIPELINE.md`](../docs/PIPELINE.md), which
gives every stage in order with the exact command and the reasoning behind it.

| Directory | What lives here |
|---|---|
| `lib/` | the modules stages import: N-Triples parsing, repo identity, the manifest inventory, shared-person edge building |
| `extract/` | single-pass `grep`/`awk` passes over the SemRepo `.nt` dump, plus the parsers that turn their output into `data/processed/` |
| `cohort/` | which repositories are nodes at all: the streams that grow `data/repo-lists/` and fetch each new node's aggregate row |
| `fetch/` | GitHub API caches under `data/raw/`: descriptions, READMEs, owner avatars, and every ecosystem's dependency manifests |
| `edges/` | the edge tiers: one dependency builder per ecosystem, the combined tier they fold into, shared topics, shared issue authors |
| `layout/` | where a node sits: the trophic height solve, the circular topic embedding, and the text-embedding signal behind it |
| `clusters/` | the Leiden hierarchy, ids kept stable across reruns, and readable labels |
| `identity/` | one node per repository rather than per GitHub slug: read the origins' git object ids, derive the identity, apply it |
| `build/` | renders `web/index.html` from `web/template.html` + `data/processed/` |

## Conventions

Every script runs **from the repository root**, not from its own directory:

```bash
python3 scripts/layout/trophic_levels.py
SEMREPO_NT=data/raw/SemRepo_2025-05-11.nt ./scripts/extract/predicate_counts.sh
```

Each one derives its own paths from `__file__`, so the working directory only
matters for the handful of stages that take output paths as arguments (their
`Usage:` lines are written relative to the root).

Shared code is imported as a package: a stage puts `scripts/` on `sys.path` and
does `from lib.identity import canonical_lookup`. Nothing imports a *stage* --
the one exception is `clusters/labels.py`, which reuses
`layout/text_embeddings.py`'s embedding-text builder rather than duplicating it.

Stages are individually re-runnable and cached. A fetch stage that is
interrupted resumes; an edge stage leaves a coordinate it genuinely cannot
resolve edge-less rather than guessing a repository for it.

Files here were numbered by creation order until they were regrouped; the
old-name table is the appendix of [`../docs/PIPELINE.md`](../docs/PIPELINE.md).
