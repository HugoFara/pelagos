#!/usr/bin/env python3
"""Pre-fetch and cache each Rust-ecosystem repo's Cargo.toml, raw text.

Rust repos (scripts/cohort/rust_ecosystem.py) arrive with zero
dependency edges: scripts/edges/dependency_edges.py's usedPackage signal comes from the
SemRepo dump, which is PyPI-only. Cargo.toml is the cleanest manifest of the
four ecosystems wired up so far -- a single fixed path at repo root (unlike
Java, which needed a three-way pom.xml/build.gradle/build.gradle.kts probe,
see scripts/fetch/java_manifests.py) and real TOML, so the stdlib `tomllib`
parses it with no new dependency and no hand-rolled scanner.

Cached raw to data/raw/cargo_toml_cache/{owner}__{name}.toml. A repo with no
Cargo.toml at root (a Rust project whose crates all live under a subdirectory
with no workspace root, or a repo GitHub labels `language:rust` off .rs files
alone) gets a zero-byte marker file so a re-run doesn't re-request it -- same
convention as the README / go.mod / package.json / java-manifest caches.

Usage: python3 scripts/fetch/cargo_toml.py
"""
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CARGO_TOML_CACHE = ROOT / "data/raw/cargo_toml_cache"
GH_API_THROTTLE_S = 0.4  # see scripts/cohort/dependency_repos.py


def load_rust_repos():
    return sorted(set(
        l.strip() for l in
        (ROOT / "data/repo-lists/rust_ecosystem_repos.txt").read_text().splitlines()
        if l.strip()
    ))


def fetch_cargo_toml(owner, name):
    cache_path = CARGO_TOML_CACHE / f"{owner}__{name}.toml"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace"), False

    out = subprocess.run(
        ["gh", "api", f"repos/{owner}/{name}/contents/Cargo.toml"], capture_output=True, text=True
    )
    time.sleep(GH_API_THROTTLE_S)
    CARGO_TOML_CACHE.mkdir(parents=True, exist_ok=True)
    if out.returncode != 0:
        cache_path.write_text("")  # no Cargo.toml at repo root (real, not an error)
        return "", True

    data = json.loads(out.stdout)
    raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    cache_path.write_text(raw, encoding="utf-8")
    return raw, True


def main():
    repos = load_rust_repos()
    fetched = 0
    empty = 0
    for repo in repos:
        owner, name = repo.split("/", 1)
        was_cached = (CARGO_TOML_CACHE / f"{owner}__{name}.toml").exists()
        text, did_fetch = fetch_cargo_toml(owner, name)
        if did_fetch and not was_cached:
            fetched += 1
        if not text.strip():
            empty += 1
    print(
        f"{len(repos)} Rust-ecosystem repos, {fetched} fetched fresh this run, "
        f"{empty} have no Cargo.toml at repo root (0-byte marker cached, real absence)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
