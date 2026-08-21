#!/usr/bin/env python3
"""Read every cohort origin's intrinsic git object ids, via `git ls-remote`.

This is the fetch half of "a node is a repository, not a GitHub slug";
44_repo_identity.py is the derive half.

## Why intrinsic ids, and why not Software Heritage's API

A git object id is a hash of the object's content. Two origins serving the
same repository -- a GitHub mirror and its upstream on kernel.org, a repo
before and after a rename, a fork and its source -- publish byte-identical
ids for every ref they have in common, with no name, URL or heuristic
involved. That is the property that makes cross-forge deduplication exact
rather than a guess, and it is the reason Software Heritage's SWHIDs work at
421M-project scale.

It is also the reason this script does not need Software Heritage. Checked
directly against the two Linux origins:

    git.kernel.org  .../torvalds/linux.git   941 refs via ls-remote
    SWH snapshot of the same origin           942 targets
    overlap                                   940 (100% of the ls-remote tags)

SWH's `swh:1:rev:` / `swh:1:rel:` ids for a git origin *are* the git object
ids. So `ls-remote` yields the same intrinsic identifiers, for any forge,
with no crawl-coverage dependency and no rate limit -- SWH's anonymous quota
is 700 requests/hour, which would be roughly 20 hours for this cohort. SWH
stays genuinely useful for the one thing ls-remote structurally cannot do --
*discovering* that a sibling origin exists somewhere else at all -- and
44_repo_identity.py uses it for exactly that, on a bounded subset.

And a real cross-forge result the same check produced, which is the whole
point of the exercise:

    github.com/torvalds/linux              2259 ref ids
    git.kernel.org .../torvalds/linux.git   941 ref ids
    shared                                  940  (99.9% of the kernel.org side)

`torvalds/linux` is a mirror. Under slug identity it is a node in its own
right, at the bottom of the trophic axis, standing in for a repository whose
real origin this dataset does not contain.

## What is stored

data/raw/repo_refs_cache/{owner}__{name}.json, as
{"url":..., "ids":[...], "refs": n, "error": null}. Both the tag object and
its peeled commit are kept: a mirror that fetched tags without peeling still
matches on one of the two, and keeping both only ever raises overlap.

A *terminal* error is cached too -- a repository that is gone or private is a
real, stable answer, and re-probing 7000 origins to rediscover it every run
would be the expensive kind of honesty. Delete the file to retry one.

A *transient* error is never cached, and that distinction was not obvious
enough to guess at. The first full run of this script reported 3160 of 7051
origins unreadable, essentially all of them with

    fatal: expected flush after ref listing

which is what GitHub answers when too many anonymous git connections arrive
at once -- retrying any one of them by hand succeeds immediately. Caching
that as an answer would have frozen 45% of the cohort in as "no refs" and
silently removed it from the identity pass, which is exactly the failure the
whole script exists to prevent. So: retryable errors are retried with
backoff, and if they still fail nothing is written and the origin is picked
up again next run. Requests are authenticated through the `gh` token where
one is available, which raises the ceiling that produced the throttling in
the first place; the token is used for the request only and never written to
the cache.

Usage: python3 scripts/43_repo_refs.py [limit=0]
"""
import json
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFS_CACHE = ROOT / "data/raw/repo_refs_cache"
GITHUB_CACHE = ROOT / "data/raw/github_cache"

LS_REMOTE_TIMEOUT_S = 120  # linux, the largest origin here, answers in ~2s; this is a stuck-connection guard
LS_REMOTE_WORKERS = 8  # 16 was enough to make GitHub start refusing; see module docstring
LS_REMOTE_RETRIES = 4
LS_REMOTE_BACKOFF_S = 3.0

# Substrings of the git errors that mean "ask again", not "there is nothing
# here". All of these were observed on origins that answered normally on a
# plain manual retry seconds later.
RETRYABLE_ERRORS = (
    "expected flush", "rpc failed", "early eof", "connection reset",
    "connection timed out", "could not read from remote", "unexpected disconnect",
    "the remote end hung up", "gnutls", "ssl_read", "http/2", "502", "503", "504",
)

_progress_lock = threading.Lock()
_progress = Counter()


def gh_token():
    """The `gh` CLI's token, or None. Authenticated git requests get a far
    higher concurrent-connection ceiling than anonymous ones."""
    out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


TOKEN = gh_token()


def load_cohort():
    top50 = json.loads((ROOT / "data/processed/repo_aggregates.json").read_text())
    extra = json.loads((ROOT / "data/processed/dependency_repo_aggregates.json").read_text())
    return sorted(set(top50) | set(extra))


def canonical_url(repo):
    """The URL to probe. Prefers the current `full_name` from the cached
    GitHub response, so a renamed repo is read at the name it actually has
    rather than 301-redirecting (which git follows, but which would also make
    the cache key disagree with the origin it describes)."""
    owner, name = repo.split("/", 1)
    cached = GITHUB_CACHE / f"{owner}__{name}.json"
    if cached.exists():
        try:
            data = json.loads(cached.read_text())
        except ValueError:
            data = {}
        if not data.get("_404") and data.get("full_name"):
            return f"https://github.com/{data['full_name']}.git"
    return f"https://github.com/{repo}.git"


def request_url(url):
    """The URL actually passed to git. Carries the token when there is one;
    the caller keeps the clean URL for the cache, so no credential is ever
    written to disk."""
    if TOKEN and url.startswith("https://github.com/"):
        return url.replace("https://", f"https://x-access-token:{TOKEN}@", 1)
    return url


def is_retryable(error):
    lowered = error.lower()
    return any(marker in lowered for marker in RETRYABLE_ERRORS)


def ls_remote_once(url):
    """(object ids, error). Never prompts: a private or deleted repository
    would otherwise block the whole pass waiting on credentials."""
    try:
        out = subprocess.run(
            ["git", "ls-remote", request_url(url)],
            capture_output=True, text=True, timeout=LS_REMOTE_TIMEOUT_S,
            env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true", "PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired:
        return [], "connection timed out"
    if out.returncode != 0:
        error = (out.stderr.strip().splitlines() or ["failed"])[-1][:200]
        return [], error.replace(TOKEN, "***") if TOKEN else error
    ids = set()
    for line in out.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and len(parts[0]) == 40:
            ids.add(parts[0])
    return sorted(ids), None


def ls_remote(url):
    """(object ids, error, terminal). `terminal` is False when the origin
    still refuses after every retry -- the caller must not cache that."""
    error = "failed"
    for attempt in range(LS_REMOTE_RETRIES):
        ids, error = ls_remote_once(url)
        if error is None:
            return ids, None, True
        if not is_retryable(error):
            return [], error, True
        time.sleep(LS_REMOTE_BACKOFF_S * (attempt + 1))
    return [], error, False


def cache_path(repo):
    owner, name = repo.split("/", 1)
    return REFS_CACHE / f"{owner}__{name}.json"


def fetch_refs(repo):
    path = cache_path(repo)
    if path.exists():
        return "cached"
    url = canonical_url(repo)
    ids, error, terminal = ls_remote(url)
    if not terminal:
        return "deferred"  # still throttled after every retry; retried next run, never cached
    REFS_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"url": url, "refs": len(ids), "ids": ids, "error": error}, separators=(",", ":")))
    return "error" if error else "fresh"


def fetch_reporting(repo, total):
    result = fetch_refs(repo)
    with _progress_lock:
        _progress[result] += 1
        done = sum(_progress.values())
        if done % 500 == 0 or done == total:
            print(f"  {done}/{total} origins "
                  f"({_progress['fresh']} read, {_progress['cached']} already cached, "
                  f"{_progress['error']} gone/private, {_progress['deferred']} deferred)",
                  file=sys.stderr)
    return result


def main(limit="0"):
    limit = int(limit)
    repos = load_cohort()
    if limit:
        repos = repos[:limit]
    print(f"reading intrinsic refs for {len(repos)} cohort origins"
          f"{' (authenticated)' if TOKEN else ' (anonymous -- expect throttling)'}",
          file=sys.stderr)

    with ThreadPoolExecutor(max_workers=LS_REMOTE_WORKERS) as pool:
        list(pool.map(lambda r: fetch_reporting(r, len(repos)), repos))

    total_ids = 0
    empty = 0
    read = 0
    for repo in repos:
        path = cache_path(repo)
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        total_ids += data["refs"]
        if data["error"]:
            continue
        read += 1
        if not data["refs"]:
            empty += 1
    print(f"{total_ids} intrinsic object ids across {read} readable origins "
          f"({_progress['error']} gone/private, {empty} readable but ref-less, "
          f"{_progress['deferred']} still throttled and left uncached for the next run) "
          f"-> {REFS_CACHE.relative_to(ROOT)}/", file=sys.stderr)
    if _progress["deferred"]:
        print(f"re-run to pick up the {_progress['deferred']} deferred origins", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
