# Pelagos

**A 3D view of the open-source dependency ecosystem.** 7,051 GitHub
repositories, placed by what they actually depend on and what they are
actually about — then you fly through it.

### [→ Explore it live](https://hugofara.github.io/pelagos/)

![The Pelagos explorer: a 3D graph of 7,051 GitHub repositories, with cluster
volumes, repo labels and a sidebar listing auto-detected clusters](docs/img/pelagos.png)

## What you're looking at

Every repo sits at a real, computed position in a cylinder. Nothing here is
decorative — each axis is derived from data, and a repo with no data for an
axis is left unplaced on it rather than guessed.

| Axis | Means | Comes from |
|---|---|---|
| **Height** | how deep in the dependency stack — things sit *above* what they depend on | a trophic solve over the dependency graph |
| **Angle** | subject matter — nearby angles share GitHub topic tags | a spectral embedding of topic co-occurrence |
| **Distance from the axis** | how focused that subject is — vague projects drift to the centre | the spread of the repo's own topics |

Repos are linked by five kinds of relationship, each measured, none inferred:
**dependencies** (parsed from real manifests), **shared topics**, **shared
stargazers**, **shared contributors**, and **shared issue posters**. Dependency
and topic edges are on by default; the rest you switch on in the legend.

## Things to try

- **Search any repo** — including ones not in the set. Type `owner/name` and
  it's fetched from GitHub and positioned live, on the same axes, by the same
  formulas as everything else.
- **Zoom into a cluster.** Groups open into their members as they fill the
  screen, and close again as you pull back.
- **Click a repo, then "Read README"** — it opens over the graph, rendered.
- **Path Finder** — the shortest chain of real dependencies between two repos.
- **Compare** — two repos side by side, with what they share.
- **"copy link"** — shares the exact view you're looking at.

![The detail panel for apache/dubbo, showing its position on all three axes
with a plain-language reading of each](docs/img/panel.png)

## What's in it, honestly

7,051 repositories — a **curated cohort, not all of GitHub**. It was grown per
language with quotas, so it holds roughly a thousand each of Java, Rust and Go,
1,701 Python, and exactly one C#. Treat it as a large sample of well-known
open source, not a census.

Coverage is uneven and the explorer doesn't hide it. A repo whose dependencies
couldn't be resolved has no height and floats at the mid-plane, and the panel
says so in as many words rather than showing a number that looks measured.
Roughly 72% of the cohort has a real height; the rest is honest absence.

Built from the **GitHub API** and **[SemRepo](https://semrepo.org)**, a large
RDF dump of GitHub activity released under CC0. SemRepo supplies 31% of the
drawn edges and is the only source for the three person-based tiers; the cohort
itself, descriptions, topics, READMEs, avatars and five of the six
dependency-manifest ecosystems come from the GitHub API.

**Privacy.** The two person-based edge tiers are built from overlap *counts*.
Every person identifier is replaced with a salted pseudonym before anything is
written to disk, and the salt is never committed. No individual is named
anywhere in this repository or in the published page.

## Run it locally

The data is committed, so there's nothing to build:

```bash
git clone https://github.com/HugoFara/pelagos.git
cd pelagos/web
python3 -m http.server 8000        # then open http://localhost:8000
```

Serving it over HTTP rather than opening the file directly matters — the page
loads owner avatars, which browsers block from `file://`.

To work on the renderer:

```bash
cd web && npm install
npm run dev      # rebuilds and live-reloads on changes to src/
npm test
```

## Why "Pelagos"

The pelagic zone is the open water column, read by depth rather than drawn as
a map. The vertical axis here is a trophic solve — the same mathematics
ecologists use on food webs, where predators sit above their prey — and it
averages rather than counting path length for the same reason they do: real
dependency graphs, like real food webs, contain cycles.

## Going deeper

- **[docs/PIPELINE.md](docs/PIPELINE.md)** — how every file in `data/processed/`
  is produced, and how to rebuild it from the source dump.
- **[NOTES.md](NOTES.md)** — the working log: what was measured, what the
  measurements changed, and the things that turned out to be wrong.

## License

MIT — see [LICENSE](LICENSE). The upstream SemRepo dataset is CC0 (data) and
MIT (pipeline).
