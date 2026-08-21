"""Minimal N-Triples line parser -- just enough for this dataset's shape.

Not a general RDF parser: assumes subject and predicate are always full URIs in
angle brackets, and the object is either a URI or a simple quoted literal
(handles backslash-escaped quotes, ignores language tags / datatypes beyond
that). Good enough for grep-filtered subsets of SemRepo; reach for rdflib if the
input isn't already narrowed down.
"""
import re

_LINE_RE = re.compile(r'^<([^>]+)>\s+<([^>]+)>\s+(.*)\s\.\s*$')
_LITERAL_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"')


def parse_line(line):
    """Return (subject, predicate, object, kind) or None if the line doesn't parse.

    kind is 'uri' or 'literal'.
    """
    m = _LINE_RE.match(line.strip())
    if not m:
        return None
    subj, pred, obj = m.groups()
    if obj.startswith('<'):
        return subj, pred, obj[1:-1], 'uri'
    m2 = _LITERAL_RE.match(obj)
    if m2:
        return subj, pred, m2.group(1), 'literal'
    return None


def short_predicate(pred_uri):
    """https://semrepo.org/property/hasIssue -> hasIssue"""
    return pred_uri.rsplit('/', 1)[-1].rsplit('#', 1)[-1]


def repo_name(subject_uri):
    """https://semrepo.org/repository/owner/name -> owner/name"""
    return subject_uri.replace('https://semrepo.org/repository/', '')


def object_type_label(object_uri):
    """Classify a SemRepo object URI into (type, short_label) for display."""
    path = object_uri.replace('https://semrepo.org/', '')
    if path.startswith('person/'):
        return 'person', path[len('person/'):]
    if path.startswith('forkedRepo/'):
        return 'forkedRepo', path[len('forkedRepo/'):]
    if path.startswith('contributorreference/') or path.startswith('contributorReference/'):
        return 'contributorReference', 'contributorRef #' + path.rsplit('/', 1)[-1]
    if path.startswith('package/'):
        return 'package', path[len('package/'):]
    if '/issue/' in path:
        return 'issue', 'issue/' + path.split('/issue/')[-1]
    return 'other', path
