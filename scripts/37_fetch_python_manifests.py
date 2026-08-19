#!/usr/bin/env python3
"""Pre-fetch and cache each Python-ecosystem repo's dependency manifest.

The 68 repos from 26_fetch_python_ecosystem_repos.py are the last cohort
sitting at exactly zero dependency edges. That looks odd for a project whose
original dependency signal is PyPI-based, but it's structural: the
usedPackage triples 11_dependency_edges.py reads come from the SemRepo dump,
which only covers the *original* 51+dependency-expansion cohort. Repos added
later by a GitHub-search stream were never in that dump, so Python needs the
same read-the-manifest treatment Go (28/29), JS (30/31), Java (32/33) and
Rust (35/36) got.

Two candidate paths, measured by probing all 68 repos directly before
writing this:

    pyproject.toml      34 repos (modern, PEP 621 or poetry)
    requirements.txt     5 repos (no pyproject at root)
    setup.py only        2 repos (d2l-ai/d2l-zh, ytdl-org/youtube-dl)
    no manifest at all  27 repos

BOTH are fetched when both exist, rather than stopping at the first hit.
That costs one extra request for the repos that have both, and it is not
optional: a pyproject.toml declaring `dynamic = ["dependencies"]` (or one
that is nothing but ruff/black config) carries no dependency list at all,
and the real list is sitting in the requirements.txt next to it. Checked
against the parsed output -- 3b1b/manim, AUTOMATIC1111/stable-diffusion-webui,
Comfy-Org/ComfyUI, ansible/ansible, vllm-project/vllm and
hacksider/Deep-Live-Cam all parse to zero deps from pyproject.toml alone
while shipping a populated requirements.txt. 38_python_dependency_edges.py
prefers pyproject and falls back only when it yields nothing.

setup.py is deliberately NOT probed. Reading it means either executing
arbitrary repo code (never) or AST-walking for an `install_requires=[...]`
literal that in practice is often built from variables, file reads or
f-strings -- real work and real fragility to reach exactly two repos. They
are left edge-less instead, the same "real signal or none" call the
golang.org/x/* and gitbox.apache.org gaps got.

The 27 with nothing at root are mostly not Python packages at all --
curated lists and courseware (awesome-python, free-programming-books,
system-design-primer, project-based-learning, public-apis,
PayloadsAllTheThings, devops-exercises) -- plus monorepos whose real
manifest lives one directory down (langchain-ai/langchain,
OpenBB-finance/OpenBB, Significant-Gravitas/AutoGPT, microsoft/markitdown).
Same root-only scoping precedent as the JS-workspace and Gradle-submodule
gaps; not worked around here.

Cached raw to data/raw/python_manifest_cache/{owner}__{name}.{kind}, where
kind is whichever file existed, so 38_python_dependency_edges.py knows how to
parse it without re-probing. A repo with neither gets a zero-byte
{owner}__{name}.none marker -- same convention as every other manifest cache.

Usage: python3 scripts/37_fetch_python_manifests.py
"""
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON_MANIFEST_CACHE = ROOT / "data/raw/python_manifest_cache"
GH_API_THROTTLE_S = 0.4  # see scripts/10_fetch_new_repo_stats.py

MANIFEST_KINDS = ["pyproject.toml", "requirements.txt"]


def load_python_repos():
    return sorted(set(
        l.strip() for l in
        (ROOT / "data/repo-lists/python_ecosystem_repos.txt").read_text().splitlines()
        if l.strip()
    ))


def cached_kinds(owner, name):
    """Which manifest kinds are already cached for this repo. ["none"] means a
    previous run confirmed it has neither; [] means it was never probed."""
    found = [kind for kind in MANIFEST_KINDS
             if (PYTHON_MANIFEST_CACHE / f"{owner}__{name}.{kind}").exists()]
    if found:
        return found
    if (PYTHON_MANIFEST_CACHE / f"{owner}__{name}.none").exists():
        return ["none"]
    return []


def fetch_python_manifests(owner, name):
    """(kinds_present, did_fetch). Every kind that exists is fetched, not just
    the first -- see module docstring."""
    cached = cached_kinds(owner, name)
    if cached:
        return ([] if cached == ["none"] else cached), False

    found = []
    for candidate in MANIFEST_KINDS:
        out = subprocess.run(
            ["gh", "api", f"repos/{owner}/{name}/contents/{candidate}"],
            capture_output=True, text=True,
        )
        time.sleep(GH_API_THROTTLE_S)
        if out.returncode == 0:
            data = json.loads(out.stdout)
            raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
            PYTHON_MANIFEST_CACHE.mkdir(parents=True, exist_ok=True)
            (PYTHON_MANIFEST_CACHE / f"{owner}__{name}.{candidate}").write_text(raw, encoding="utf-8")
            found.append(candidate)

    if not found:
        PYTHON_MANIFEST_CACHE.mkdir(parents=True, exist_ok=True)
        (PYTHON_MANIFEST_CACHE / f"{owner}__{name}.none").write_text("")
    return found, True


def main():
    repos = load_python_repos()
    fetched = 0
    by_kind = {kind: 0 for kind in MANIFEST_KINDS}
    both = 0
    none_count = 0
    for repo in repos:
        owner, name = repo.split("/", 1)
        was_cached = bool(cached_kinds(owner, name))
        kinds, did_fetch = fetch_python_manifests(owner, name)
        if did_fetch and not was_cached:
            fetched += 1
        if not kinds:
            none_count += 1
        else:
            for kind in kinds:
                by_kind[kind] += 1
            if len(kinds) > 1:
                both += 1
    print(
        f"{len(repos)} Python-ecosystem repos, {fetched} fetched fresh this run, "
        f"{by_kind['pyproject.toml']} pyproject.toml + {by_kind['requirements.txt']} "
        f"requirements.txt ({both} have both), {none_count} have neither at repo root "
        f"(0-byte marker cached, real absence)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
