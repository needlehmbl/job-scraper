"""
Strict Metro Manila (National Capital Region) location gating.

Job board searches for "Metro Manila" are loose -- JobStreet, Indeed, and
LinkedIn all leak postings from Central Luzon, Calabarzon, the Visayas, and
Mindanao. Every scraped row's `location` field is checked against this
whitelist before it is kept.

Metro Manila = the NCR's 16 cities + Pateros, plus the well-known business
districts / neighbourhoods boards use in place of a city name, and Indeed's
regional code for the NCR ("P00", seen as "Makati, P00, PH").
"""
import re

_PATTERNS = [
    r"\bmetro\b",                 # "Metro Manila"
    r"metropolitan",              # "Metropolitan Manila"
    r"national\s+capital\b",      # "National Capital Region"
    r"\bncr\b",
    r"\bmanila\b",
    r"\bmakati\b",
    r"\btaguig\b",
    r"quezon\s+city",
    r"\bqc\b",
    r"\bpasig\b",
    r"\bpasay\b",
    r"paranaque|parañaque",
    r"mandaluyong",
    r"muntinlupa",
    r"marikina",
    r"caloocan",
    r"las\s+pinas|las\s+piñas",
    r"malabon",
    r"navotas",
    r"valenzuela",
    r"\bsan\s+juan\b",
    r"pateros",
    r"\bortigas\b",               # Pasig / Mandaluyong
    r"\beastwood\b",               # Eastwood City, Libis (Quezon City)
    r"\bnovaliches\b",             # Novaliches (Quezon City)
    r"\balabang\b",               # Muntinlupa
    r"\bbinondo\b",               # Manila
    r"\bbgc\b",                   # Bonifacio Global City, Taguig
    r"\bbonifacio\b",
    r"\bp00\b",                   # Indeed's NCR regional code
]

_METRO_MANILA_PAT = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def is_metro_manila(location) -> bool:
    """True when the location is (or could be) inside Metro Manila.

    An empty/unknown location keeps the posting -- the strict filter only
    rejects postings that are EXPLICITLY somewhere outside the NCR.
    """
    if not isinstance(location, str) or not location.strip():
        return True
    return bool(_METRO_MANILA_PAT.search(location))


# Company career boards (Greenhouse/Lever) are curated targets, not open
# nationwide searches, so they get a looser gate: an explicit NCR location
# always passes, and a bare country/remote-PH location ("Philippines",
# "Remote in the Philippines", "Philippines Remote") passes too -- board
# data entry is inconsistent ("Manila" vs "Philippines" for the same
# office). A named non-NCR city ("Cebu, Philippines") still fails.
_PH_COUNTRY_ONLY_PAT = re.compile(
    r"^\s*(remote(\s+in(\s+the)?)?\s*[-–,]?\s*)?(philippines|ph)\s*$"
    r"|^\s*(philippines|ph)\s*[-–,]?\s*remote\s*$",
    re.IGNORECASE,
)


def is_ph_or_metro(location) -> bool:
    """Loose location gate for curated company-board rows (see above)."""
    if not isinstance(location, str) or not location.strip():
        return True
    if is_metro_manila(location):
        return True
    return bool(_PH_COUNTRY_ONLY_PAT.match(location.strip()))