"""
Manual URL intake for postings found outside the scraper.

normalize_url() keeps UNIQUE(url) stable: lowercase, strip tracking
params (utm_*, fbclid, gclid, ...), drop fragments, strip trailing
slash. Identity params (Indeed ?jk=, LinkedIn /view/<id>) are kept.

fetch_job_from_url() is site-aware by hostname with a generic
OpenGraph/<title> fallback so login-walled pages still save a minimal
trackable row (fetch_limited=True) instead of blocking.

Bot-walled boards (jobstreet/linkedin/indeed/glassdoor) answer plain
GETs with a 403 Cloudflare "Just a moment..." shell, so when the fast
requests path comes back empty for one of those hosts we retry in a
headless Chromium (same launch flags as jobstreet.py) and read the
rendered data-automation hooks / JSON-LD JobPosting.
"""
import json
import re
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

# Identity-only query params per host: everything else is per-click
# tracking that would splinter UNIQUE(url). Indeed is the big one --
# ?from=&tk=&xpse=&xfps=&xkcb= change on every impression; jk is the job.
_IDENTITY_PARAMS = {
    "indeed": frozenset({"jk", "vjk"}),
}


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
    site = site_from_url(url)
    identity = _IDENTITY_PARAMS.get(site)
    if identity is not None:
        kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                if k.lower() in identity]
    else:
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


# Hosts that answer plain GETs with a bot-wall (403 Cloudflare shell,
# login gate) instead of the posting. These go through headless Chromium
# when the fast path comes back empty.
_BROWSER_HOSTS = frozenset({"jobstreet", "linkedin", "indeed", "glassdoor"})

# Rendered-page hooks per site, first hit wins. Mapped live against
# ph.jobstreet.com/job/<id> (title: h1/job-detail-title, company:
# advertiser-name, location: job-detail-location, body: jobAdDetails) and
# linkedin.com/jobs/view/<id> (title: h1.top-card-layout__title,
# company: a.topcard__org-name-link, location: h4.top-card-layout__second-subline).
_TITLE_SELECTORS = {
    "jobstreet": ["[data-automation='job-detail-title']", "h1"],
    "linkedin": ["h1.top-card-layout__title",
                 "[data-automation='job-detail-title']", "h1"],
    "default": ["h1"],
}
_COMPANY_SELECTORS = {
    "jobstreet": ["[data-automation='advertiser-name']"],
    "linkedin": ["a.topcard__org-name-link",
                 ".jobs-unified-top-card__company-name"],
    "trabajo": ["span.job-chip"],
    "default": ["[data-automation='jobCompany']", ".company-name",
                ".job-company"],
}
_LOCATION_SELECTORS = {
    "jobstreet": ["[data-automation='job-detail-location']"],
    "linkedin": ["h4.top-card-layout__second-subline",
                 "[data-automation='jobLocation']"],
    "default": ["[data-automation='jobLocation']", ".job-location"],
}
_DESC_SELECTORS = {
    "jobstreet": ["[data-automation='jobAdDetails']"],
    "default": ["article", "main"],
}

# "Solutions Architect Job in Taguig City, Metro Manila - Jobstreet"
_JOBSTREET_OG_RE = re.compile(
    r"^(?P<title>.+?)\s+Job in\s+(?P<location>.+?)\s*-\s*Jobstreet\s*$",
    re.IGNORECASE)


def parse_jobstreet_og(og_title: str) -> tuple[str, str]:
    """Split JobStreet's rendered og:title into (title, location)."""
    m = _JOBSTREET_OG_RE.match((og_title or "").strip())
    if not m:
        return "", ""
    return m.group("title").strip(), m.group("location").strip()


# LinkedIn's anonymous shell/landing page embeds the identity in <title> as
# "<Company> hiring <Title> in <Location>" (truncated). The real posting page
# uses og:title "<Title> at <Company> — <site>". Both shapes are parsed here.
_LINKEDIN_SHELL_RE = re.compile(
    r"^(?P<company>.+?)\s+hiring\s+(?P<title>.+?)\s+in\s+(?P<location>.+?)\s*$",
    re.IGNORECASE)

# Trabajo's <title> is "<Title> in <Location> - <Company>" while og:title
# carries the title alone, so the company/location live in the title tag.
_TRABAJO_TITLE_RE = re.compile(
    r"^(?P<title>.+?)\s+in\s+(?P<location>.+?)\s*-\s*(?P<company>.+?)\s*$",
    re.IGNORECASE)


def parse_linkedin_shell(title: str) -> tuple[str, str, str]:
    """Split LinkedIn's '<Company> hiring <Title> in <Location>' shell title
    into (title, company, location). Empty strings when it isn't that shape.
    """
    m = _LINKEDIN_SHELL_RE.match((title or "").strip())
    if not m:
        return "", "", ""
    return (m.group("title").strip(), m.group("company").strip(),
            m.group("location").strip())


def parse_trabajo_title(title: str) -> tuple[str, str, str]:
    """Split Trabajo's '<Title> in <Location> - <Company>' title tag into
    (title, company, location). Empty strings when it isn't that shape.
    """
    m = _TRABAJO_TITLE_RE.match((title or "").strip())
    if not m:
        return "", "", ""
    return (m.group("title").strip(), m.group("company").strip(),
            m.group("location").strip())


# Trailing decoration title-splits leave on the company: "Acme Inc. — ",
# "Acme | LinkedIn", "Acme - Jobs". Strip separators and their padding.
_COMPANY_TRAILING_RE = re.compile(r"\s+[—–\-|](?:\s+.*)?$")


def _clean_company(name: str) -> str:
    """Strip trailing site decoration from a company parsed out of a title."""
    s = (name or "").strip()
    s = _COMPANY_TRAILING_RE.sub("", s)
    return s.strip()


def _text(soup, selector: str) -> str:
    try:
        el = soup.select_one(selector)
        return el.get_text(" ", strip=True) if el else ""
    except Exception:
        return ""


def _pick_first(page, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            texts = page.eval_on_selector_all(
                sel, "e => e.map(x => (x.innerText || '').trim())")
        except Exception:
            continue
        for t in texts or []:
            if t:
                return t.split("\n")[0].strip()
    return ""


def _jobposting_jsonld(page) -> dict:
    """schema.org JobPosting blob when the page embeds one (generic host)."""
    try:
        blobs = page.eval_on_selector_all(
            "script[type='application/ld+json']",
            "e => e.map(x => x.innerText)")
    except Exception:
        return {}
    for raw in blobs or []:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph", [node])
            for item in graph if isinstance(graph, list) else [graph]:
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    org = item.get("hiringOrganization") or {}
                    loc = item.get("jobLocation") or {}
                    addr = (loc.get("address") if isinstance(loc, dict) else {}) or {}
                    return {
                        "title": item.get("title", ""),
                        "company": org.get("name", "") if isinstance(org, dict) else "",
                        "location": addr.get("addressLocality", "") or
                        (loc.get("name", "") if isinstance(loc, dict) else ""),
                        "description": item.get("description", ""),
                    }
    return {}


def fetch_with_browser(url: str, site: str, timeout_ms: int = 60000) -> dict:
    """Render one posting URL in headless Chromium and extract fields.

    Never raises: returns {} when the browser, the challenge, or the
    selectors defeat us (caller keeps the minimal trackable row).
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox"],
            )
            page = browser.new_page(
                user_agent=HEADERS["User-Agent"], locale="en-PH")
            try:
                page.goto(url, timeout=timeout_ms,
                          wait_until="domcontentloaded")
                try:
                    if "Just a moment" in (page.content() or ""):
                        page.wait_for_timeout(8000)
                except Exception:
                    pass
                try:
                    page.wait_for_timeout(2500)
                except Exception:
                    pass
                title = _pick_first(
                    page, _TITLE_SELECTORS.get(site, _TITLE_SELECTORS["default"]))
                company = _pick_first(
                    page, _COMPANY_SELECTORS.get(site, _COMPANY_SELECTORS["default"]))
                location = _pick_first(
                    page, _LOCATION_SELECTORS.get(site, _LOCATION_SELECTORS["default"]))
                if (site == "linkedin" and location and company
                        and location.startswith(company)):
                    # LinkedIn's subline is "<Company>  <Location>" — drop the
                    # company prefix so location stays a bare locality.
                    location = location[len(company):].strip()
                desc = _pick_first(
                    page, _DESC_SELECTORS.get(site, _DESC_SELECTORS["default"]))
                og_title = ""
                try:
                    if page.query_selector("meta[property='og:title']"):
                        og_title = page.eval_on_selector(
                            "meta[property='og:title']", "e => e.content") or ""
                except Exception:
                    og_title = ""
                og_desc = ""
                try:
                    if page.query_selector("meta[property='og:description']"):
                        og_desc = page.eval_on_selector(
                            "meta[property='og:description']", "e => e.content") or ""
                except Exception:
                    og_desc = ""
                extra = _jobposting_jsonld(page)
                if site == "jobstreet" and (not title or not location):
                    og_t, og_loc = parse_jobstreet_og(og_title)
                    title = title or og_t
                    location = location or og_loc
                title = title or extra.get("title", "")
                company = company or extra.get("company", "")
                location = location or extra.get("location", "")
                desc = desc or extra.get("description", "") or og_desc or ""
                if desc and len(desc) > 8000:
                    desc = desc[:8000]
                if not title:
                    return {}
                return {"title": title[:500], "company": company[:300],
                        "location": (location[:300] if location else None),
                        "description": desc or None,
                        "site": site, "fetch_limited": False}
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception:
        return {}
    return {}


def _parse_html(soup, site: str) -> tuple[str, str, str, str, bool]:
    """Extract (title, company, location, description, is_shell) from a
    200-OK HTML page (fast path).

    Site-specific shapes:
      linkedin  og:title "<Title> at <Company> — <site>"; the anonymous
                shell instead carries "<Company> hiring <Title> in <Location>"
                in <title> — parsed company-first and flagged as a shell so
                the caller retries in headless Chromium.
      trabajo   og:title is title-only; <title> is
                "<Title> in <Location> - <Company>".
    """
    def meta(prop: str) -> str:
        try:
            tag = soup.find("meta", property=prop) or \
                soup.find("meta", attrs={"name": prop})
            return (tag.get("content") or "").strip() if tag else ""
        except Exception:
            return ""

    title_tag = ""
    try:
        title_tag = (soup.title.string or "").strip() if soup.title else ""
    except Exception:
        title_tag = ""
    raw_title = meta("og:title") or _text(soup, "h1") or title_tag
    title = raw_title
    company = ""
    location = None

    shell = False
    if site == "linkedin":
        t, c, loc = parse_linkedin_shell(raw_title)
        if c:
            title, company, location = t, c, loc
            shell = True
    if not company and site != "linkedin":
        # og:site_name is the SITE name on LinkedIn ("LinkedIn"), never the
        # employer — every other board uses it for the company.
        company = meta("og:site_name") or ""
    if not company and title:
        if site == "linkedin":
            # og:title "<Title> at <Company> — <Location> | <site>": the
            # company follows " at "; location/site suffixes are cleaned.
            if " at " in title:
                parts = title.split(" at ")
                company = _clean_company(parts[-1])
                title = " at ".join(parts[:-1]).strip()
        else:
            # Common pattern: "Senior Python Dev - Acme Corp | JobStreet"
            # (company last)
            for sep in (" - ", " | ", " at ", " @ "):
                if sep in title:
                    parts = title.split(sep)
                    if len(parts) >= 2:
                        company = _clean_company(parts[-1])
                        title = sep.join(parts[:-1]).strip()
                    break
    if site == "trabajo":
        t, c, loc = parse_trabajo_title(title_tag)
        if c:
            company = company or c
            location = location or loc
            title = title or t
    company = _clean_company(company)
    location = _text(
        soup, "[data-automation='jobLocation'], .job-location, .location"
    ) or location
    if location:
        location = _COMPANY_TRAILING_RE.sub("", location).strip() or None
    desc = (_text(soup, "article") or _text(soup, "main") or
            meta("og:description") or None)
    if desc and len(desc) > 8000:
        desc = desc[:8000]
    return title, company, location or None, desc, shell


def fetch_job_from_url(url: str, timeout: int = 15) -> dict:
    """Fetch title/company/location/description for one posting URL.

    Never raises: on any failure returns a minimal row with
    fetch_limited=True so tracking is never blocked.
    """
    site = site_from_url(url)
    walled_note = (f"{site} blocks anonymous fetching "
                   f"(bot-wall) — saved with URL only"
                   if site in _BROWSER_HOSTS else "")
    blank = {"title": "", "company": "", "location": None,
             "description": None, "site": site, "fetch_limited": True,
             "fetch_note": walled_note}
    soup = None
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200 and r.text:
            soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        soup = None
    if soup is None:
        # Plain GET failed outright (bot-wall, timeout, 404): render once in
        # headless Chromium for ANY site instead of saving Untitled.
        rendered = fetch_with_browser(url, site)
        if rendered.get("title"):
            return rendered
        return blank

    title, company, location, desc, shell = _parse_html(soup, site)
    if not title or shell:
        # Empty fast path, or LinkedIn answered with its anonymous shell
        # ("<Company> hiring <Title> in ..."): render once in headless
        # Chromium for the real posting.
        rendered = fetch_with_browser(url, site)
        if rendered.get("title"):
            if shell and not rendered.get("company") and company:
                rendered["company"] = company
            return rendered
    return {"title": title[:500] if title else "",
            "company": company[:300],
            "location": (location[:300] if location else None),
            "description": desc,
            "site": site,
            "fetch_limited": not bool(title)}
PLACEHOLDER_TITLES = frozenset({"untitled (manual)", "untitled", ""})


def is_placeholder_title(title: str) -> bool:
    """True when the stored title is an auto-fill placeholder (or empty)."""
    return (title or "").strip().lower() in PLACEHOLDER_TITLES


def resolve_manual_fields(fetched: dict, override: dict) -> dict:
    """Merge user-typed modal fields over auto-fetched ones.

    A non-blank override always wins (covers Indeed's bot-wall); blanks
    fall back to fetched values, then to safe defaults. Pure function.
    """
    fetched = fetched or {}
    override = override or {}

    def pick(key: str) -> str:
        v = (override.get(key) or "").strip()
        return v or (fetched.get(key) or "").strip()

    title = pick("title") or "Untitled (manual)"
    company = pick("company")
    location = pick("location") or fetched.get("location")
    return {"title": title[:500], "company": company[:300],
            "location": (location[:300] if location else None)}
