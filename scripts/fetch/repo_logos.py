#!/usr/bin/env python3
"""Download each repo owner's GitHub avatar as a small square PNG, for the
graph explorer to render on repo nodes instead of a flat circle.

Only 51 repos / ~46 distinct owners in the current cohort, so a one-time
download into a committed web/logos/ directory is simpler than fetching
avatars live on every page load (and doesn't burn into the 60/hour
unauthenticated API cap the live description/star fetch already uses).

GitHub's avatar redirect (github.com/{owner}.png) sometimes serves a JPEG
even when asked for .png, so every image is decoded and re-saved as a real
PNG rather than trusting the response's nominal extension.

## Renamed owners need the API, not the vanity URL

github.com/{owner}.png is keyed on the owner's *current* login, so it 404s
for any account renamed since this cohort's repo ids were captured -- and
the file still has to be saved under the old login, since that's the name
the graph nodes carry. `flagalpha/llama2-chinese` was the real case: the
org is now `LlamaChinese`, github.com/flagalpha.png is a hard 404, and the
one missing avatar was showing up as a console 404 on every page load.
`gh api repos/{repo}` still resolves the renamed repo and reports the
current owner's avatar_url, so a 404 on the vanity URL falls back to that,
resolved per repo rather than per owner (only a repo id can be followed
through a rename). Genuinely deleted accounts still fail both ways, which
is the honest outcome -- see scripts/build/web_explorer.py, which ships the list of
owners with no avatar so the renderer never requests one.

Usage: python3 scripts/fetch/repo_logos.py repo_list.txt [out_dir=web/logos]
"""
import io
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.identity import is_forge_node  # noqa: E402

AVATAR_URL = "https://github.com/{owner}.png?size=64"


def fetch_png(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "pelagos-logo-fetch"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    Image.open(io.BytesIO(data)).convert("RGBA").save(dest, "PNG")


def avatar_url_via_api(repo):
    """Current avatar_url for a repo's owner, following any rename."""
    out = subprocess.run(["gh", "api", f"repos/{repo}", "--jq", ".owner.avatar_url"],
                         capture_output=True, text=True)
    url = out.stdout.strip()
    return url if out.returncode == 0 and url.startswith("http") else None


def main(repo_list_path, out_dir="web/logos"):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Non-GitHub forge nodes have no GitHub owner and therefore no avatar --
    # `gitlab.freedesktop.org` is a hostname, not a user the API can answer
    # for. They fall back to the flat node colour, which scripts/build/web_explorer.py
    # already ships a list for.
    repos = [l.strip() for l in Path(repo_list_path).read_text().splitlines()
             if l.strip() and not is_forge_node(l.strip())]
    # One repo per owner is enough to follow a rename; any of them resolves.
    owner_repo = {}
    for repo in repos:
        owner_repo.setdefault(repo.split("/")[0], repo)

    ok, renamed, failed = [], [], []
    for owner in sorted(owner_repo):
        dest = out_dir / f"{owner}.png"
        if dest.exists():
            ok.append(owner)
            continue
        try:
            fetch_png(AVATAR_URL.format(owner=owner), dest)
            ok.append(owner)
        except Exception as exc:
            api_url = avatar_url_via_api(owner_repo[owner])
            try:
                if not api_url:
                    raise RuntimeError(f"vanity URL failed ({exc}) and the API had no avatar_url")
                fetch_png(api_url, dest)
                ok.append(owner)
                renamed.append(owner)
            except Exception as exc2:
                failed.append((owner, str(exc2)))
        time.sleep(0.2)

    print(f"{len(ok)}/{len(owner_repo)} owner avatars saved to {out_dir}/", file=sys.stderr)
    if renamed:
        print(f"{len(renamed)} recovered via the API after a 404 on the vanity URL "
              f"(renamed owner): {renamed}", file=sys.stderr)
    if failed:
        print(f"failed: {failed}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(f"usage: {sys.argv[0]} repo_list.txt [out_dir=web/logos]")
    main(*sys.argv[1:])
