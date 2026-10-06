"""
Manual URL intake for postings found outside the scraper.

Flow (matches the Add-manually UX):
  1. normalize_url() keeps UNIQUE(url) stable: lowercase, strip tracking
     params (utm_*, fbclid, gclid, ...), drop fragments, strip trailing
     slash. Identity params (Indeed ?jk=, LinkedIn /view/<id>) are kept.
     Trabajo outbound redirects are unwrapped to the canonical /job- link.
  2. Callers compare the normalized link against stored URLs FIRST (no
     network) — only unknown links reach the fetch below.
  3. site_from_url() identifies the board; SITE_STRATEGY routes it:
     - ATS boards (greenhouse/lever/ashby) -> board JSON API (same data
       JobSpy bulk search uses, but single-posting direct — JobSpy has
       no fetch-by-URL, so this is the equivalent fast path).
     - everything else -> requests fast path, then per-site Playwright
       helper on empty/shell/bot-wall.
  4. Bot-wall detection (Cloudflare "Just a moment", cf-turnstile,
     login gates) is verified on BOTH paths and returned as
     blocked/block_reason — the UI then keeps the modal open and asks
     the user to fill title/company instead of saving Untitled silently.

fetch_job_from_url() never raises: walled pages return fetch_limited
rows with blocked=True so tracking is never blocked.
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
    # Trabajo outbound redirect wraps the canonical /job-... link
    # (e.g. /go/<id>?url=<canonical> or ?url=/job-...); the canonical URL
    # is the stable dedupe key, so unwrap it when present.
    try:
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        wrapped = q.get("url") or q.get("u") or q.get("redirect") or ""
        if "trabajo" in host and wrapped:
            if wrapped.startswith("/"):
                path, query = wrapped.rstrip("/"), ""
            elif "trabajo" in wrapped:
                wp = urlparse(wrapped)
                if wp.path:
                    path, query = wp.path.rstrip("/") or "", ""
    except Exception:
        pass
    return urlunparse((scheme, host + port, path.rstrip("/") or "", "",
                       query, "")).rstrip("/")


def site_from_url(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    host = host[4:] if host.startswith("www.") else host
    for needle in ("indeed", "linkedin", "jobstreet", "glassdoor",
                   "trabajo", "greenhouse", "lever", "ashby", "remoteok",
                   "kalibrr", "jora", "grabjobs"):
        if needle in host:
            return needle
    return host or ""


# Manual-add routing. JobSpy is bulk-search only (no fetch-by-URL), so
# "use jobspy" for a pasted link means: ATS boards go straight at the
# board JSON API (the same source JobSpy reads); aggregator/walled
# boards go requests -> per-site Playwright helper below.
SITE_STRATEGY = {
    "greenhouse": "ats_api",
    "lever": "ats_api",
    "ashby": "ats_api",
    "remoteok": "api",
    "trabajo": "requests",
    "indeed": "playwright",
    "linkedin": "playwright",
    "glassdoor": "playwright",
    "jobstreet": "playwright",
    "kalibrr": "playwright",
    "jora": "playwright",
    "grabjobs": "playwright",
}


def strategy_for_site(site: str) -> str:
    """Fetch strategy for a site key: ats_api | api | requests | playwright."""
    return SITE_STRATEGY.get(site or "", "requests")


# Substrings that mark a bot-wall / login-gate shell on either path.
_BOT_WALL_MARKERS = (
    "just a moment", "cf-turnstile", "cf-challenge", "attention required",
    "access denied", "verify you are human", "are you a robot",
    "sign in to view", "log in to view", "join now to view",
)


def is_bot_wall_text(text: str, title: str = "") -> bool:
    """True when HTML/title looks like a challenge/login shell, not a posting."""
    blob = f"{title or ''}\n{text or ''}"[:20000].lower()
    return any(m in blob for m in _BOT_WALL_MARKERS)


def _blocked_note(site: str, reason: str) -> str:
    return (f"{site or 'site'} blocked auto-fetch ({reason}) — "
            f"fill title/company manually")


# Hosts that answer plain GETs with a bot-wall (403 Cloudflare shell,
# login gate) instead of the posting. These go through headless Chromium
# when the fast path comes back empty, shelled, or bot-walled.
_BROWSER_HOSTS = frozenset({"jobstreet", "linkedin", "indeed", "glassdoor",
                            "kalibrr", "jora", "grabjobs"})

# Rendered-page hooks per site, first hit wins. Mapped live against
# ph.jobstreet.com/job/<id> (title: h1/job-detail-title, company:
# advertiser-name, location: job-detail-location, body: jobAdDetails) and
# linkedin.com/jobs/view/<id> (title: h1.top-card-layout__title,
# company: a.topcard__org-name-link, location: h4.top-card-layout__second-subline).
# Indeed/Glassdoor/Trabajo/Kalibrr/Jora/GrabJobs rows below are the
# best-known public hooks; JSON-LD JobPosting remains the fallback.
_TITLE_SELECTORS = {
    "jobstreet": ["[data-automation='job-detail-title']", "h1"],
    "linkedin": ["h1.top-card-layout__title",
                 "[data-automation='job-detail-title']", "h1"],
    "indeed": ["h1.jobsearch-JobInfoHeader-title",
               "[data-testid='jobsearch-JobInfoHeader-title']", "h1"],
    "glassdoor": ["[data-test='job-title']", "h1"],
    "trabajo": ["h1", "[data-automation='job-detail-title']"],
    "kalibrr": ["h1", "[data-testid='job-title']"],
    "jora": ["h1", "[data-testid='job-title']"],
    "grabjobs": ["h1", "[data-testid='job-title']"],
    "default": ["h1"],
}
_COMPANY_SELECTORS = {
    "jobstreet": ["[data-automation='advertiser-name']"],
    "linkedin": ["a.topcard__org-name-link",
                 ".jobs-unified-top-card__company-name"],
    "trabajo": ["span.job-chip"],
    "indeed": ["[data-company-name]", ".jobsearch-InlineCompanyRating a",
               ".jobsearch-CompanyInfoWithoutHeaderImage a"],
    "glassdoor": ["[data-test='employer-name']", ".employer-name"],
    "kalibrr": ["[data-testid='company-name']", ".company-name"],
    "jora": ["[data-testid='company-name']", ".company-name"],
    "grabjobs": ["[data-testid='company-name']", ".company-name"],
    "default": ["[data-automation='jobCompany']", ".company-name",
                ".job-company"],
}
_LOCATION_SELECTORS = {
    "jobstreet": ["[data-automation='job-detail-location']"],
    "linkedin": ["h4.top-card-layout__second-subline",
                 "[data-automation='jobLocation']"],
    "indeed": ["[data-testid='jobsearch-JobInfoHeader-companyLocation']",
               "[data-testid='inlineHeader-companyLocation']"],
    "glassdoor": ["[data-test='location']", ".location"],
    "default": ["[data-automation='jobLocation']", ".job-location"],
}
_DESC_SELECTORS = {
    "jobstreet": ["[data-automation='jobAdDetails']"],
    "indeed": ["#jobDescriptionText", "[data-testid='job-description']"],
    "glassdoor": ["[data-test='job-description']", "#JobDescription"],
    "default": ["article", "main"],
}

# Salary hooks per site, first hit wins. Only well-known automation attrs
# here -- anything else comes from the guarded description snippet below
# (salary_snippet) or schema.org baseSalary, never raw guesswork.
_SALARY_SELECTORS = {
    "jobstreet": ["[data-automation='jobSalary']"],
    "default": [],
}

# A sentence carries pay only when it names money AND an amount.
_SALARY_SENT_RE = re.compile(
    r"(₱|PHP|\$|€|£|¥|₹|\b(?:USD|EUR|GBP|JPY|INR|AUD|CAD|SGD)\b)")

# First currency-anchored amount: the snippet is windowed around this so
# nav chrome ("Home Browse jobs ...") and trailers ("Free with email ...")
# don't pollute the raw text.
_ANCHORED_AMT_RE = re.compile(
    r"(?:₱|PHP|\$|€|£|¥|₹)\s*[\d][\d,]*(?:\.\d+)?", re.IGNORECASE)


def salary_snippet(desc: str, limit: int = 300) -> str:
    """Pay-bearing slice of a description, validated by the parser.

    Returns "" unless the slice re-parses to a currency + amounts, so
    "3-5 years experience" next to a "competitive salary" mention never
    becomes a phantom range.
    """
    if not desc:
        return ""
    m = _ANCHORED_AMT_RE.search(desc)
    if m:
        s = max(0, m.start() - 150)
        e = min(len(desc), m.end() + 150)
        while s > 0 and not desc[s].isspace():
            s -= 1
        while e < len(desc) and not desc[e].isspace():
            e += 1
        out = " ".join(desc[s:e].split())[:limit].strip()
    else:
        sents = re.split(r"(?<=[.!?\n;•·])\s+", desc)
        hits = [s.strip() for s in sents
                if _SALARY_SENT_RE.search(s) and re.search(r"\d", s)]
        out = " ".join(hits)[:limit].strip()
    if not out:
        return ""
    try:
        from salary import parse_salary_text
        p = parse_salary_text(out)
    except Exception:
        return ""
    if p.get("currency") and p.get("min") is not None:
        return out
    return ""


def extract_salary_raw(soup, site: str, desc: str | None = None) -> str:
    """Salary text for one posting page: selector hit, else snippet."""
    for sel in _SALARY_SELECTORS.get(site, _SALARY_SELECTORS["default"]):
        t = _text(soup, sel)
        if t:
            return t[:300]
    return salary_snippet(desc or "")

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
                        "salary_raw": _jsonld_salary(item),
                    }
    return {}


def _jsonld_salary(item: dict) -> str:
    """schema.org baseSalary blob -> short raw string, else ""."""
    try:
        sal = item.get("baseSalary")
        if not isinstance(sal, dict):
            return ""
        v = sal.get("value")
        v = v if isinstance(v, dict) else sal
        lo, hi = v.get("minValue"), v.get("maxValue")
        if lo is None and hi is None:
            single = v.get("value")
            lo = hi = single if isinstance(single, (int, float)) else None
        if lo is None and hi is None:
            return ""
        cur = (v.get("currency") or sal.get("currency") or "").strip().upper()
        unit = (v.get("unitText") or sal.get("unitText") or "").strip().lower()

        def fmt(n):
            return f"{n:,.0f}" if isinstance(n, float) and n.is_integer() \
                else str(n)
        rng = fmt(lo) if lo == hi else f"{fmt(lo)}-{fmt(hi)}"
        return f"{cur} {rng} {unit}".strip()
    except Exception:
        return ""


def _storage_state_for(site: str):
    """Persisted login/clearance cookies for headless renders, if present.

    Reuses the bulk scraper's storage_state/<site>.json when it exists,
    falling back to storage_state/jobstreet.json (Cloudflare clearance).
    Returns None for a fresh anonymous context.
    """
    import os
    from pathlib import Path
    cands = []
    try:
        here = Path(__file__).resolve().parent
        cands = [here / "storage_state" / f"{site}.json",
                 here / "storage_state" / "jobstreet.json",
                 Path.cwd() / "storage_state" / f"{site}.json"]
    except Exception:
        return None
    for p in cands:
        try:
            if p.is_file() and p.stat().st_size > 10 and os.access(p, os.R_OK):
                return str(p)
        except Exception:
            continue
    return None


def fetch_with_browser(url: str, site: str, timeout_ms: int = 60000) -> dict:
    """Render one posting URL in headless Chromium and extract fields.

    Never raises: returns {} when the browser, the challenge, or the
    selectors defeat us (caller keeps the minimal trackable row).
    On bot-wall defeat returns {"blocked": True, "block_reason": ...}
    so the caller can ask the user for fields instead of saving Untitled.
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
            storage_state = _storage_state_for(site)
            try:
                if storage_state:
                    ctx = browser.new_context(
                        user_agent=HEADERS["User-Agent"], locale="en-PH",
                        storage_state=storage_state)
                else:
                    ctx = browser.new_context(
                        user_agent=HEADERS["User-Agent"], locale="en-PH")
                page = ctx.new_page()
            except Exception:
                page = browser.new_page(
                    user_agent=HEADERS["User-Agent"], locale="en-PH")
            try:
                page.goto(url, timeout=timeout_ms,
                          wait_until="domcontentloaded")
                try:
                    html = page.content() or ""
                    if is_bot_wall_text(html):
                        page.wait_for_timeout(8000)
                        # GrabJobs-style walls sometimes need a reload.
                        if site in ("grabjobs", "jora", "indeed", "glassdoor"):
                            try:
                                if is_bot_wall_text(page.content() or ""):
                                    page.reload(timeout=30000,
                                                wait_until="domcontentloaded")
                                    page.wait_for_timeout(5000)
                            except Exception:
                                pass
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
                salary_raw = (
                    _pick_first(
                        page, _SALARY_SELECTORS.get(
                            site, _SALARY_SELECTORS["default"]))
                    or salary_snippet(desc)
                    or extra.get("salary_raw", "")
                )
                if not title:
                    try:
                        walled = is_bot_wall_text(page.content() or "", og_title)
                    except Exception:
                        walled = False
                    if walled:
                        return {"blocked": True,
                                "block_reason": "bot-wall challenge in browser",
                                "site": site}
                    return {}
                # A 200-OK challenge shell can carry a junk title
                # ("Just a moment...") — never save it as the posting.
                if is_bot_wall_text(f"{title} {desc or ''}", title):
                    return {"blocked": True,
                            "block_reason": "bot-wall challenge in browser",
                            "site": site}
                return {"title": title[:500], "company": company[:300],
                        "location": (location[:300] if location else None),
                        "description": desc or None,
                        "salary_raw": salary_raw or "",
                        "site": site, "fetch_limited": False,
                        "blocked": False}
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


def _fetch_ats_api(url: str, site: str) -> dict:
    """Direct board-API fetch for greenhouse/lever/ashby (JobSpy equivalent).

    JobSpy has no fetch-by-URL; these are the same JSON endpoints its
    bulk search reads, hit for one posting. Returns {} when the URL does
    not match a known board pattern or the API misses.
    """
    try:
        from urllib.parse import urlparse as _up
        host = (_up(url).hostname or "").lower()
        parts = [p for p in (_up(url).path or "").split("/") if p]
    except Exception:
        return {}
    try:
        if site == "greenhouse":
            # boards.greenhouse.io/<board>/jobs/<id> or job-boards.../jobs/<id>
            bid = None
            if "boards.greenhouse.io" in host and len(parts) >= 3:
                bid, jid = parts[-3], parts[-1].split("?")[0]
            elif parts:
                # greenhouse Harvest API needs board token; try path guess.
                return {}
            else:
                return {}
            if not bid or not jid:
                return {}
            r = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{bid}/jobs/{jid}",
                headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return {}
            d = r.json()
            loc = (d.get("location") or {}).get("name", "") if isinstance(
                d.get("location"), dict) else (d.get("location") or "")
            return {"title": (d.get("title") or "")[:500],
                    "company": "",  # board token -> employer unknown w/o board meta
                    "location": (loc or "")[:300] or None,
                    "description": (d.get("content") or "")[:8000] or None,
                    "salary_raw": "", "site": site, "fetch_limited": False,
                    "blocked": False}
        if site == "lever":
            # api.lever.co/v0/postings/<board>/<id>
            if "lever.co" not in host or len(parts) < 2:
                return {}
            board, jid = parts[-2], parts[-1].split("?")[0]
            r = requests.get(
                f"https://api.lever.co/v0/postings/{board}/{jid}",
                headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return {}
            d = r.json()
            cats = d.get("categories") or {}
            return {"title": (d.get("text") or "")[:500],
                    "company": "", "location": (cats.get("location") or "")[:300] or None,
                    "description": (d.get("description") or "")[:8000] or None,
                    "salary_raw": (cats.get("salary") or "")[:300],
                    "site": site, "fetch_limited": False, "blocked": False}
        if site == "ashby":
            # api.ashbyhq.com/posting-api/job-board/<board>/<id>
            if "ashby" not in host or len(parts) < 2:
                return {}
            board, jid = parts[-2], parts[-1].split("?")[0]
            r = requests.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{board}/{jid}",
                headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return {}
            d = r.json()
            loc = (d.get("location") or {}).get("name", "") if isinstance(
                d.get("location"), dict) else ""
            return {"title": (d.get("title") or "")[:500],
                    "company": (d.get("organizationName") or "")[:300],
                    "location": (loc or "")[:300] or None,
                    "description": (d.get("descriptionHtml") or d.get("description") or "")[:8000] or None,
                    "salary_raw": "", "site": site, "fetch_limited": False,
                    "blocked": False}
    except Exception:
        return {}
    return {}


def fetch_job_from_url(url: str, timeout: int = 15) -> dict:
    """Fetch title/company/location/description for one posting URL.

    Strategy: ATS API (greenhouse/lever/ashby) -> requests fast path ->
    per-site Playwright helper. Never raises: on any failure returns a
    minimal row with fetch_limited=True + blocked/block_reason so the
    caller can prompt for manual fields instead of saving Untitled.
    """
    site = site_from_url(url)
    strategy = strategy_for_site(site)
    walled_note = (f"{site} blocks anonymous fetching "
                   f"(bot-wall) — saved with URL only"
                   if site in _BROWSER_HOSTS else "")
    blank = {"title": "", "company": "", "location": None,
             "description": None, "site": site, "fetch_limited": True,
             "fetch_note": walled_note, "salary_raw": "",
             "blocked": site in _BROWSER_HOSTS, "block_reason": "fast-path empty",
             "strategy": strategy}
    # 1. ATS fast lane (JobSpy-equivalent direct API, no browser needed).
    if strategy in ("ats_api", "api"):
        if strategy == "ats_api":
            hit = _fetch_ats_api(url, site)
            if hit.get("title"):
                hit["strategy"] = strategy
                return hit
        # remoteok JSON feed is list-level; fall through to generic path.
    soup = None
    status = None
    raw_text = ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        status = r.status_code
        if r.status_code == 200 and r.text:
            raw_text = r.text
            soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        soup = None
    if soup is None:
        # Plain GET failed outright (bot-wall, timeout, 404): render once in
        # headless Chromium for ANY site instead of saving Untitled.
        rendered = fetch_with_browser(url, site)
        if rendered.get("title"):
            rendered["strategy"] = strategy
            return rendered
        if rendered.get("blocked"):
            blank.update({k: rendered.get(k) for k in ("blocked", "block_reason") if rendered.get(k)})
            blank["fetch_note"] = _blocked_note(site, rendered.get("block_reason") or "request failed")
        blank["strategy"] = strategy
        return blank

    title, company, location, desc, shell = _parse_html(soup, site)
    # 200-OK bot-wall shells carry junk titles ("Just a moment...") with
    # non-empty text — verify BEFORE trusting the fast path.
    if is_bot_wall_text(f"{title} {raw_text[:8000]}", title):
        rendered = fetch_with_browser(url, site)
        if rendered.get("title"):
            rendered["strategy"] = strategy
            return rendered
        reason = rendered.get("block_reason") or "bot-wall challenge (200-OK shell)"
        out = dict(blank)
        out.update({"fetch_note": _blocked_note(site, reason),
                    "blocked": True, "block_reason": reason,
                    "strategy": strategy})
        return out
    if not title or shell:
        # Empty fast path, or LinkedIn answered with its anonymous shell
        # ("<Company> hiring <Title> in ..."): render once in headless
        # Chromium for the real posting.
        rendered = fetch_with_browser(url, site)
        if rendered.get("title"):
            if shell and not rendered.get("company") and company:
                rendered["company"] = company
            rendered["strategy"] = strategy
            return rendered
        if rendered.get("blocked"):
            out = dict(blank)
            out.update({"fetch_note": _blocked_note(
                site, rendered.get("block_reason") or "bot-wall"),
                "blocked": True,
                "block_reason": rendered.get("block_reason") or "bot-wall",
                "strategy": strategy})
            return out
    return {"title": title[:500] if title else "",
            "company": company[:300],
            "location": (location[:300] if location else None),
            "description": desc,
            "salary_raw": extract_salary_raw(soup, site, desc or ""),
            "site": site,
            "fetch_limited": not bool(title),
            "blocked": False, "block_reason": "",
            "strategy": strategy}
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
