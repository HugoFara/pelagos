#!/usr/bin/env python3
"""Pre-fetch and cache each cohort repo's README, raw markdown.

Phase 14's text-embedding similarity signal needs real prose per repo, and
descriptions alone (already cached, scripts/12_cache_repo_descriptions.py)
are one-liners -- not enough text to embed meaningfully for the ~155 repos
with no topics either (see NOTES.md's Phase 13 coverage finding). The
README's own first real paragraph is the next-best source of genuine,
repo-specific prose already sitting in this dataset's reach, no new API
surface needed beyond what scripts/10-12 already use.

Cached raw (before any cleaning -- see scripts/18_text_embeddings.py for the
badge/HTML/code-block stripping and first-paragraph extraction, kept as a
separate step so this cache stays a faithful, reusable copy of what GitHub
actually returned) to data/raw/readme_cache/{owner}__{name}.md. A repo with
no README (real, not rare -- some of the smaller dependency-cohort repos
have none) gets a zero-byte marker file so a re-run doesn't re-request it.

Usage: python3 scripts/17_fetch_readmes.py
"""
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import is_forge_node  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
README_CACHE = ROOT / "data/raw/readme_cache"
GH_API_THROTTLE_S = 0.4  # see scripts/10_fetch_new_repo_stats.py -- an unthrottled
# sequential gh api loop over thousands of repos trips GitHub's secondary/abuse
# rate limit well before the primary 5000/hour quota is exhausted


def load_cohort():
    top50 = (ROOT / "data/repo-lists/top50_repos.txt").read_text().splitlines()
    extra = (ROOT / "data/repo-lists/dependency_extra_repos.txt").read_text().splitlines()
    repos = [l.strip() for l in top50 + extra if l.strip()]
    return sorted(set(repos))


def fetch_readme(owner, name):
    cache_path = README_CACHE / f"{owner}__{name}.md"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace"), False

    out = subprocess.run(
        ["gh", "api", f"repos/{owner}/{name}/readme"], capture_output=True, text=True
    )
    time.sleep(GH_API_THROTTLE_S)
    README_CACHE.mkdir(parents=True, exist_ok=True)
    if out.returncode != 0:
        cache_path.write_text("")  # no README (404) -- real, not an error
        return "", True

    data = json.loads(out.stdout)
    raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    cache_path.write_text(raw, encoding="utf-8")
    return raw, True


def main():
    cohort = load_cohort()
    fetched = 0
    empty = 0
    for repo in cohort:
        if is_forge_node(repo):
            continue  # no GitHub README endpoint for a forge-hosted project
        owner, name = repo.split("/", 1)
        was_cached = (README_CACHE / f"{owner}__{name}.md").exists()
        text, did_fetch = fetch_readme(owner, name)
        if did_fetch and not was_cached:
            fetched += 1
        if not text.strip():
            empty += 1
    print(
        f"{len(cohort)} cohort repos, {fetched} fetched fresh this run, "
        f"{empty} have no README (0-byte marker cached, real absence)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
