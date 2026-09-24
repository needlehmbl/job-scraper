"""
Cross-link dedup fingerprints: same listing, different links.

Exact-URL dedup (UNIQUE on jobs.url) can't catch reposts: the same role
re-scraped with a different job ID (Indeed `?jk=...`, LinkedIn
`/jobs/view/<id>`, JobStreet `/job/<id>`) or cross-posted across boards
has a different URL every time. This module provides the shared
normalized fingerprint used at every layer:

  fingerprint = normalized_company + "|" + normalized_title

Location is intentionally NOT part of the key: within Metro Manila the
same title+company in "Taguig" vs "Taguig City, Metro Manila" is the same
listing with different formatting, and out-of-NCR postings never reach
dedup (locations.py gate). The raw location is still stored on the row
for audit; it just doesn't split the key.

Normalization is deliberately aggressive so minor board variations merge:
case, punctuation, extra whitespace, and common company suffixes
("inc", "corp", "philippines", "in the philippines", ...) are stripped.
"""
import re

_BASE_SPLIT = re.compile(r"[^a-z0-9]+")

# Trailing company tokens that are legal-entity / region decorations, not
# identity. Stripped from the END of the normalized company only, so
# "primus knowledge specialists inc" -> "primus knowledge specialists"
# while a core word like "labs" is kept.
_COMPANY_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "llc", "plc", "gmbh", "pty", "pvt",
    "philippines", "ph",
})

# Whole-phrase decorations jobspy/boards append to company names.
_COMPANY_PHRASES = (
    "in the philippines",
)

_STOPWORDS_COMPANY = frozenset()  # reserved: keep company tokens intact


def normalize_text(text) -> str:
    """Lowercase, non-alnum -> space, collapse whitespace."""
    if not isinstance(text, str):
        return ""
    s = _BASE_SPLIT.sub(" ", text.lower()).strip()
    return re.sub(r"\s+", " ", s)


def normalize_company(company) -> str:
    s = normalize_text(company)
    for phrase in _COMPANY_PHRASES:
        # strip occurrences anywhere ("accenture in the philippines" -> "accenture")
        s = s.replace(phrase, " ")
    s = re.sub(r"\s+", " ", s).strip()
    toks = s.split()
    # strip trailing legal/region suffixes repeatedly ("... corp ph" -> "...")
    while toks and toks[-1] in _COMPANY_SUFFIXES:
        toks.pop()
    return " ".join(toks)


def normalize_title(title) -> str:
    return normalize_text(title)


def fingerprint(title, company) -> str:
    """Cross-link identity key. Empty string when either side is missing
    (those rows fall back to exact-URL dedup only)."""
    c = normalize_company(company)
    t = normalize_title(title)
    if not c or not t:
        return ""
    return f"{c}|{t}"


def row_fingerprint(row) -> str:
    """Accepts a dict or pandas Series with title/company keys."""
    try:
        title = row.get("title", "") if hasattr(row, "get") else row["title"]
        company = row.get("company", "") if hasattr(row, "get") else row["company"]
    except Exception:
        return ""
    return fingerprint(title, company)
