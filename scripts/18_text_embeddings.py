#!/usr/bin/env python3
"""Text-embedding similarity signal (Phase 14): the actual problem with
topic-only similarity isn't noise, it's coverage -- only 155-ish of 319
cohort repos clear the topic-PMI min-support thresholds (see NOTES.md).
Embedding real prose reaches almost every repo instead: description alone
covers nearly the full cohort, and README first-paragraph fills in most of
the rest.

Per repo, the embedding input is `description + topics + a cleaned first
paragraph` of its README -- deliberately not the raw README, which runs
~60% badges/install-boilerplate/shields.io noise (checked directly on a
sample while writing clean_first_paragraph() below). Embedded with
`BAAI/bge-small-en-v1.5` via `fastembed` (ONNX runtime, no PyTorch/CUDA --
this project's third tracked Python dependency after numpy and
leidenalg/python-igraph, picked for the same "smallest workable dependency"
reasoning as those two: a 384-dim MiniLM-family model is enough signal for
short repo blurbs, and fastembed's ONNX path avoids pulling in a multi-GB
torch install for it).

ROADMAP.md Phase 19 adds a capped, joined slice of a repo's sampled issue
titles (23_shared_issue_authors.py) as a fourth input where available --
see load_issue_titles() below for why it's capped shorter than the README
paragraph. Only 74/319 cohort repos have any issue data in this dump at
all, so this thickens the signal for a minority of already-embeddable repos
rather than closing the coverage gap the way the README paragraph did.

Usage: python3 scripts/18_text_embeddings.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB_CACHE = ROOT / "data/raw/github_cache"
README_CACHE = ROOT / "data/raw/readme_cache"
# ROADMAP.md Phase 19: a repo's sampled issue titles (23_shared_issue_authors.py)
# are real per-repo text too, same reasoning as the README paragraph below --
# folded in if present, optional (only 74/319 repos have any issue data in
# this dump at all) so a from-scratch run before script 23 exists degrades
# gracefully instead of failing.
ISSUE_TITLES_PATH = ROOT / "data/processed/repo_issue_titles.json"
ISSUE_TITLES = json.loads(ISSUE_TITLES_PATH.read_text()) if ISSUE_TITLES_PATH.exists() else {}

MODEL_NAME = "BAAI/bge-small-en-v1.5"

CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
HTML_TAG_RE = re.compile(r"<[^>]+>")
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
BADGE_LINE_RE = re.compile(r"^\s*(\[!\[.*?\]\(.*?\)\]\(.*?\)\s*)+$")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
HR_RE = re.compile(r"^\s*([-*_=]\s?){3,}\s*$")
EMPHASIS_RE = re.compile(r"[*_`]{1,3}")
URL_RE = re.compile(r"https?://\S+")
# ReStructuredText directives/comments (some cohort READMEs are .rst, not
# .md, e.g. aio-libs/aiohttp -- ".. image::" is that format's badge/image
# syntax, structurally invisible to the markdown-shaped regexes above).
RST_DIRECTIVE_RE = re.compile(r"^\s*\.\.\s")
ASCII_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-'.,;:!?]*$")

# Small academic/research repos (common in the dependency-expansion cohort
# -- see NOTES.md) overwhelmingly open their description/README with one of
# a handful of "this is the code for our paper" formulas. Checked directly:
# a cluster of 42 completely unrelated repos (poetry generation, anti-
# spoofing, wireless protocols, knowledge distillation, ...) came out
# thematically merged in an early Phase 14 pass purely because their
# opening clause was near-identical boilerplate, not because the repos are
# actually related -- the embedding was keying on phrasing, not subject
# matter. Stripped (not skipped -- the real paper title/subject usually
# follows immediately in the same sentence, e.g. "Code for the paper SH2:
# Self-Highlighted Hesitation Helps You Decode More Truthfully" strips down
# to the distinctive part) before embedding.
PAPER_BOILERPLATE_RES = [
    re.compile(r"here\s+is\s+the\s+official\s+code\s+of\s+\S+\s+for\s+paper\s*:?\s*", re.I),
    re.compile(r"this\s+is\s+the\s+repository\s+for\s*:?\s*", re.I),
    re.compile(r"this\s+is\s+the\s+official\s+(pytorch\s+)?(code|implementation|repository)\s+(of|for)\s*:?\s*", re.I),
    re.compile(r"this\s+repo(sitory)?\s+is\s+the\s+official\s+(implementation|code)\s+of\s+.*?paper\s*:?\s*", re.I),
    re.compile(r"this\s+repo(sitory)?\s+(includes?|contains?)\s+(the\s+|our\s+)?(code|implementation)\s*(to\s+reproduce)?\s*(our\s+|the\s+)?paper\s*:?\s*", re.I),
    re.compile(r"this\s+repo(sitory)?\s+contains\s+our\s+implementation\s+of\s+(the\s+)?paper\s*:?\s*", re.I),
    re.compile(r"(this\s+repo(sitory)?\s+)?(code|codes)\s+(for|of)\s+(reproducing\s+)?(the\s+|our\s+)?paper\s*:?\s*", re.I),
    re.compile(r"repo\s+for\s+\S*\s*paper\s*:?\s*", re.I),
    re.compile(r"official\s+(pytorch\s+)?(code|implementation)\s+(of|for)\s*:?\s*", re.I),
]


LEADING_VENUE_TAG_RE = re.compile(r"^\[[^\]]{0,40}\]\s*")


def strip_paper_boilerplate(text):
    # A leading "[EMNLP 2024 Findings]"/"[AAAI 2025]" venue tag isn't
    # subject-matter-distinctive either, and would otherwise block the
    # boilerplate patterns below from matching at the true start of string.
    text = LEADING_VENUE_TAG_RE.sub("", text)
    # Not anchored to string start: a setext-style README heading ("Title\n===")
    # with no blank line before the next sentence gets merged into the same
    # "paragraph" by clean_first_paragraph, so the boilerplate clause can land
    # mid-string, not just at the front -- checked directly (TakHemlata/
    # SSL_Anti-spoofing). Applied cumulatively, not first-match-wins, since a
    # description can carry one boilerplate phrase and the README paragraph
    # another.
    for pattern in PAPER_BOILERPLATE_RES:
        text = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_cohort():
    top50 = (ROOT / "data/repo-lists/top50_repos.txt").read_text().splitlines()
    extra = (ROOT / "data/repo-lists/dependency_extra_repos.txt").read_text().splitlines()
    return sorted(set(l.strip() for l in top50 + extra if l.strip()))


def clean_first_paragraph(raw, min_words=8, max_chars=600):
    """First substantial prose paragraph of a README, badges/HTML/code
    stripped out. A "paragraph" a reader would recognize as one, not a
    badge wall, a lone image, or a table -- checked directly against a
    sample of this cohort's actual READMEs (torch, requests, flask, ...)
    while picking min_words/max_chars: every false-positive short-circuit
    at min_words=8 was still a badge/link fragment that had survived line-
    level stripping, never real prose."""
    text = CODE_BLOCK_RE.sub("", raw)
    text = HTML_COMMENT_RE.sub("", text)

    paragraphs, current = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if HEADING_RE.match(stripped) or HR_RE.match(stripped) or BADGE_LINE_RE.match(stripped):
            continue
        if RST_DIRECTIVE_RE.match(stripped):
            continue
        if stripped.startswith(("<p", "<img", "<a ", "<div", "<table", "<tr", "<td", "|")):
            continue
        current.append(stripped)
    if current:
        paragraphs.append(" ".join(current))

    for p in paragraphs:
        p = HTML_TAG_RE.sub("", p)
        p = MD_IMAGE_RE.sub("", p)
        p = MD_LINK_RE.sub(r"\1", p)
        p = EMPHASIS_RE.sub("", p)
        p = URL_RE.sub("", p)
        p = re.sub(r"\s+", " ", p).strip()
        words = p.split()
        if len(words) < min_words:
            continue
        # Reject language-switcher lines ("English | 简体中文 | 日本語 | ...")
        # and other non-prose lines that survive to here: real sentences are
        # dominantly recognizable ASCII words, not pipe-separated short
        # tokens or non-Latin script.
        if p.count("|") >= 2:
            continue
        ascii_word_ratio = sum(1 for w in words if ASCII_WORD_RE.match(w)) / len(words)
        if ascii_word_ratio < 0.7:
            continue
        return p[:max_chars]
    return ""


def load_readme_paragraph(repo):
    owner, name = repo.split("/", 1)
    path = README_CACHE / f"{owner}__{name}.md"
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        return ""
    return clean_first_paragraph(raw)


def load_repo_meta(repo):
    owner, name = repo.split("/", 1)
    path = GITHUB_CACHE / f"{owner}__{name}.json"
    if not path.exists():
        return None, []
    data = json.loads(path.read_text())
    return data.get("description"), data.get("topics") or []


def load_issue_titles(repo, max_titles=20, max_chars=400):
    """A capped, joined slice of a repo's sampled issue titles -- capped
    lower than the README paragraph's max_chars (600) deliberately: a
    repo's description/README paragraph is written prose about the project
    as a whole, while issue titles are individually short and mostly
    incident-report phrasing ("crash on startup", "TFLite?") rather than
    subject-matter description -- meant to nudge the embedding, not compete
    with the parts that actually describe what the repo is."""
    titles = ISSUE_TITLES.get(repo) or []
    if not titles:
        return ""
    return " ".join(titles[:max_titles])[:max_chars]


def build_embedding_text(repo):
    description, topics = load_repo_meta(repo)
    paragraph = load_readme_paragraph(repo)
    description = strip_paper_boilerplate(description) if description else description
    paragraph = strip_paper_boilerplate(paragraph) if paragraph else paragraph
    issue_titles = load_issue_titles(repo)
    parts = [p for p in [description, " ".join(topics), paragraph, issue_titles] if p]
    return " ".join(parts).strip()


def main():
    from fastembed import TextEmbedding

    cohort = load_cohort()
    texts = {repo: build_embedding_text(repo) for repo in cohort}
    embeddable = [repo for repo in cohort if texts[repo]]

    print(
        f"{len(embeddable)}/{len(cohort)} cohort repos have any embeddable text "
        f"(description and/or topics and/or a real README first paragraph)",
        file=sys.stderr,
    )

    model = TextEmbedding(model_name=MODEL_NAME)
    vectors = list(model.embed([texts[repo] for repo in embeddable]))

    out = {repo: [round(float(x), 5) for x in vec] for repo, vec in zip(embeddable, vectors)}
    (ROOT / "data/processed/repo_text_embeddings.json").write_text(
        json.dumps(out, separators=(",", ":"))
    )

    readme_paragraphs = sum(1 for repo in embeddable if load_readme_paragraph(repo))
    issue_title_repos = sum(1 for repo in embeddable if load_issue_titles(repo))
    print(
        f"embedded {len(out)} repos with {MODEL_NAME} ({readme_paragraphs} contributed a real "
        f"README first paragraph, {issue_title_repos} contributed sampled issue titles) -> "
        f"data/processed/repo_text_embeddings.json",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
