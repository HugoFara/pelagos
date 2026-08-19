#!/usr/bin/env python3
"""Phase 15, part two: readable cluster labels.

Every cluster shipped since Phase 10 has been labeled by its highest-
degree member ("pytorch/pytorch cluster") rather than a real category
name -- web/template.html used to justify that choice directly: "everything
shown in this app is a real, derived value, not a guessed one." A single
hub repo's name is a real fact about the cluster, but it's a poor one --
a 20-member cluster named after whichever member happens to have the most
edges tells a reader nothing about what actually holds it together, and
two different clusters can easily share the same kind of hub (see
NOTES.md for an example).

This script generates an actual category label per cluster, but keeps
faith with that same "not a guessed one" principle rather than abandoning
it: the label is derived from real text (each member repo's real
description/topics/README paragraph -- the same source
scripts/18_text_embeddings.py already cleaned and cached), reduced to
each cluster's most *distinctive* terms via class-based TF-IDF (c-TF-IDF,
Grootendorst 2022 -- treats each cluster as one pooled "class document"
so a term ubiquitous across every cluster, like "python", scores low
automatically rather than needing a hand-maintained stopword beyond
ordinary English function words), and only then handed to a single LLM
call (`claude -p`, this project's own tooling, invoked offline/no-tools)
whose entire job is turning that list of *already-real* terms into
readable prose -- not inventing a theme from nothing. Falls back to a
plain Title Case join of the top terms if the CLI call fails or isn't
available, so the pipeline degrades instead of hard-failing.

Runs after scripts/21_stabilize_cluster_ids.py, and depends on it: labels
are cached by cluster id + a signature of that cluster's real repo
membership (data/processed/cluster_labels.json, permanent, committed) --
matching only makes sense once ids are stable across runs, otherwise
every refresh would look like an all-new set of clusters and burn an LLM
call on all of them regardless of whether anything actually changed.

Usage: python3 scripts/22_label_clusters.py
"""
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data/processed"
HIERARCHY_PATH = PROCESSED / "repo_cluster_hierarchy.json"
CACHE_PATH = PROCESSED / "cluster_labels.json"

sys.path.insert(0, str(ROOT / "scripts"))
text_mod = __import__("18_text_embeddings")

TOP_N_TERMS = 8
LLM_MODEL = "haiku"
LLM_TIMEOUT_S = 45
MAX_LABEL_CHARS = 48

# Ordinary English function words -- deliberately NOT extended with
# domain terms like "python"/"library"/"code": c-TF-IDF's whole point is
# that a term appearing in most classes gets a low score on its own
# (idf = log(1 + A/tf_all(t))), so a genuinely ubiquitous word suppresses
# itself without needing to be named here. This list exists only to stop
# grammatical glue from ever competing for a top-N slot.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "can", "do", "does", "for", "from", "has", "have", "having", "in",
    "into", "is", "it", "its", "of", "on", "or", "our", "over", "such",
    "that", "the", "their", "then", "there", "these", "this", "to", "under",
    "up", "used", "using", "very", "was", "we", "were", "which", "while",
    "with", "you", "your", "about", "across", "after", "against", "all",
    "also", "among", "any", "around", "based", "both", "each", "either",
    "further", "here", "how", "if", "into", "just", "like", "more", "most",
    "not", "now", "one", "only", "other", "out", "per", "same", "so",
    "some", "than", "them", "through", "when", "where", "who", "will",
    "within", "without",
}
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]{2,}")


def tokenize(text):
    return [t.lower() for t in TOKEN_RE.findall(text) if t.lower() not in STOPWORDS]


def flatten_members(clusters, cid):
    result = set()
    stack = [cid]
    while stack:
        cur = stack.pop()
        if cur in clusters:
            stack.extend(clusters[cur]["children"])
        else:
            result.add(cur)
    return result


def ctfidf_top_terms(class_tokens, top_n=TOP_N_TERMS):
    """Grootendorst's class-based TF-IDF: each cluster is one pooled
    "class" document, so a term's score reflects how distinctive it is of
    *this* cluster relative to the others being labeled alongside it, not
    how rare it is globally. tf(t,c) = t's share of class c's words;
    idf(t) = log(1 + A / tf_all(t)) where A is the average class size in
    words and tf_all(t) is t's total count summed across all classes --
    a term every class uses heavily (e.g. "python" in an ML-heavy cohort)
    has a large tf_all(t) and so a small idf, automatically."""
    class_counts = {cid: Counter(toks) for cid, toks in class_tokens.items()}
    total_words = {cid: sum(c.values()) for cid, c in class_counts.items()}
    nonempty = [n for n in total_words.values() if n]
    avg_words = (sum(nonempty) / len(nonempty)) if nonempty else 1.0
    term_total = Counter()
    for c in class_counts.values():
        term_total.update(c)
    result = {}
    for cid, counts in class_counts.items():
        n_words = total_words[cid] or 1
        scored = []
        for term, cnt in counts.items():
            tf = cnt / n_words
            idf = math.log(1 + avg_words / term_total[term])
            scored.append((tf * idf, term))
        scored.sort(reverse=True)
        result[cid] = [t for _, t in scored[:top_n]]
    return result


def heuristic_label(terms):
    if not terms:
        return "Miscellaneous"
    return " ".join(t.capitalize() for t in terms[:3])


def sanitize_label(raw):
    if not raw:
        return None
    line = raw.strip().splitlines()[0].strip()
    line = line.strip("\"'`. ")
    if not line or len(line) > MAX_LABEL_CHARS:
        return None
    return line


def call_llm_for_label(terms, example_repos_meta):
    examples = "\n".join(f"- {repo}: {desc}" for repo, desc in example_repos_meta)
    prompt = (
        "These are the most distinctive terms extracted (via c-TF-IDF) from the "
        "real descriptions of software repos that a clustering algorithm grouped "
        "together, plus a few example members:\n\n"
        f"Top terms: {', '.join(terms)}\n\n"
        f"Example members:\n{examples}\n\n"
        "In 2-5 words, give a short, readable category label for this cluster "
        "(Title Case, no punctuation, no trailing period). Use only what the terms "
        "and examples actually indicate -- do not invent a theme they don't support. "
        "Reply with ONLY the label, nothing else."
    )
    try:
        result = subprocess.run(
            ["claude", "-p", "--tools", "", "--model", LLM_MODEL, "--no-session-persistence", prompt],
            capture_output=True,
            text=True,
            timeout=LLM_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  claude CLI call failed ({e}), falling back to heuristic label", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  claude CLI exited {result.returncode}: {result.stderr[:200]!r}, falling back", file=sys.stderr)
        return None
    return sanitize_label(result.stdout)


def main():
    hierarchy = json.loads(HIERARCHY_PATH.read_text())
    clusters = hierarchy["clusters"]
    cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}

    by_level = defaultdict(list)
    for cid, c in clusters.items():
        by_level[c["level"]].append(cid)

    member_repos = {cid: sorted(flatten_members(clusters, cid)) for cid in clusters}
    members_signature = {cid: "|".join(member_repos[cid]) for cid in clusters}

    n_reused, n_fresh, n_fallback = 0, 0, 0
    for level, cids in sorted(by_level.items()):
        class_tokens = {}
        for cid in cids:
            text = " ".join(text_mod.build_embedding_text(r) for r in member_repos[cid])
            class_tokens[cid] = tokenize(text)
        top_terms = ctfidf_top_terms(class_tokens)

        for cid in cids:
            sig = members_signature[cid]
            terms = top_terms.get(cid, [])
            cached = cache.get(cid)
            if cached and cached.get("membersSignature") == sig and cached.get("label"):
                clusters[cid]["label"] = cached["label"]
                n_reused += 1
                continue

            examples = []
            for r in member_repos[cid][:5]:
                desc, _ = text_mod.load_repo_meta(r)
                examples.append((r, desc or ""))
            label = call_llm_for_label(terms, examples) if terms else None
            if label is None:
                label = heuristic_label(terms) if terms else clusters[cid]["hub"]
                source = "fallback"
                n_fallback += 1
            else:
                source = "llm"
                n_fresh += 1
            print(f"  {cid} ({clusters[cid]['memberCount']} members, {source}): {label!r} <- {terms}", file=sys.stderr)
            clusters[cid]["label"] = label
            cache[cid] = {"label": label, "terms": terms, "membersSignature": sig, "source": source}
            if source == "llm":
                # Checkpoint after every real LLM call (the slow, costly part) so a
                # mid-run kill/timeout over a large cohort doesn't discard completed
                # labels -- a rerun then only has to redo whatever didn't finish,
                # same as the cache already guarantees for a clean rerun.
                CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True))

    stale = set(cache) - set(clusters)
    for cid in stale:
        del cache[cid]

    CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True))
    HIERARCHY_PATH.write_text(json.dumps(hierarchy, separators=(",", ":")))
    print(
        f"{n_reused} labels reused from cache (unchanged membership), "
        f"{n_fresh} generated fresh via {LLM_MODEL}, {n_fallback} fell back to a "
        f"terms-only heuristic (no confident LLM label); {len(stale)} stale cache "
        f"entries pruned",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
