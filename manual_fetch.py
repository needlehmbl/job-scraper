"""
Manual URL intake for postings found outside the scraper.

normalize_url() keeps UNIQUE(url) stable: lowercase, strip tracking
params (utm_*, fbclid, gclid, ...), drop fragments, strip trailing
slash. Identity params (Indeed ?jk=, LinkedIn /view/<id>) are kept.

fetch_job_from_url() is site-aware by hostname with a generic
OpenGraph/<title> fallback so login-walled pages still save a minimal
trackable row (fetch_limited=True) instead of blocking.
"""
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-PH,en;q=0.9",
}

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "mc_cid", "mc_eid", "igshid", "sk",
})


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        p = urlparse(url)
    except Exception:
        return url.lower().rstrip("/")
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    if not host:
        return url.lower().rstrip("/")
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS]
    query = urlencode(kept)
    path = p.path or ""
    # Trabajo outbound redirect wraps the canonical /job-... link;
    # the canonical URL is the stable dedupe key.
    return urlunparse((scheme, host + port, path.rstrip("/") or "", "",
                       query, "")).rstrip("/")


def site_from_url(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    host = host[4:] if host.startswith("www.") else host
    for needle in ("indeed", "linkedin", "jobstreet", "glassdoor",
                   "trabajo", "greenhouse", "lever"):
        if needle in host:
            return needle
    return host or ""


def _text(soup, selector: str) -> str:
    try:
        el = soup.select_one(selector)
        return el.get_text(" ", strip=True) if el else ""
    except Exception:
        return ""


def fetch_job_from_url(url: str, timeout: int = 15) -> dict:
    """Fetch title/company/location/description for one posting URL.

    Never raises: on any failure returns a minimal row with
    fetch_limited=True so tracking is never blocked.
    """
    site = site_from_url(url)
    blank = {"title": "", "company": "", "location": None,
             "description": None, "site": site, "fetch_limited": True}
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200 or not r.text:
            return blank
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return blank

    def meta(prop: str) -> str:
        try:
            tag = soup.find("meta", property=prop) or \
                soup.find("meta", attrs={"name": prop})
            return (tag.get("content") or "").strip() if tag else ""
        except Exception:
            return ""

    title = meta("og:title") or _text(soup, "h1") or \
        ((soup.title.string or "").strip() if soup.title and soup.title.string else "")
    # Common pattern: "Senior Python Dev - Acme Corp | JobStreet"
    company = meta("og:site_name") or ""
    if not company and title:
        for sep in (" - ", " | ", " at ", " @ "):
            if sep in title:
                parts = title.split(sep)
                if len(parts) >= 2:
                    company = parts[-1].strip()
                    title = sep.join(parts[:-1]).strip()
                    break
    location = _text(soup, "[data-automation='jobLocation'], .job-location, .location") or None
    desc = (_text(soup, "article") or _text(soup, "main") or
            meta("og:description") or None)
    if desc and len(desc) > 8000:
        desc = desc[:8000]
    return {"title": title[:500] if title else "",
            "company": company[:300] if company else "",
            "location": (location[:300] if location else None),
            "description": desc,
            "site": site,
            "fetch_limited": not bool(title)}
