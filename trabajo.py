"""
Trabajo.org (ph.trabajo.org) scraper.

Trabajo is an aggregator (2,000+ sources), not a jobspy provider, so like
jobstreet.py / glassdoor.py it is scraped separately and merged in
scraper.py. Unlike those two it needs no browser: search pages are
server-rendered HTML, no login, plain GET with a browser UA passes.

URL pattern: /jobs-<term>-in-<where>[?page=N] (page 1 has no param).
Cards: li.nf-job.job-item with data-url, h3 > a title link, age text
("5 hours ago", "3 days ago"), .nf-job-list-info spans
(location / company / type / salary), p.mb-0 snippet.

Returned columns match the tracker schema
(title, company, location, date_posted, job_url, description, site,
matched_search_term). Overlap with Indeed/LinkedIn is expected (it
aggregates them); scraper.py's fingerprint dedup (dedupe.py) collapses
same-listing rows automatically. Filtering (title keywords, NCR gate,
experience) stays in scraper.py's pipeline -- this module only dedupes
within its own pages and applies the hours_old cutoff.
"""
import re
import time
from datetime import date, timedelta
from urllib.parse import quote_plus

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://ph.trabajo.org"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-PH,en;q=0.9",
}

CARD_SELECTOR = "li.nf-job.job-item"

_TYPE_WORDS = frozenset({
    "full-time", "part-time", "contract", "temporary", "freelance",
    "internship", "permanent",
})


def _where_for_trabajo(location: str) -> str:
    """Bare locality slug: the ", Philippines" suffix over-constrains
    trabajo's search (same lesson as jobstreet.py)."""
    loc = re.sub(r",\s*philippines\s*$", "", (location or "").strip(),
                 flags=re.IGNORECASE)
    return quote_plus(loc or "metro manila")


def _search_url(term: str, where: str = "metro manila", page: int = 1) -> str:
    slug = quote_plus((term or "").strip())
    w = _where_for_trabajo(where)
    url = f"{BASE}/jobs-{slug}-in-{w}"
    return url if page <= 1 else f"{url}?page={page}"


def _clean_url(href: str, data_url: str = "") -> str:
    raw = (href or "").strip() or (data_url or "").strip()
    if not raw:
        return ""
    path = raw.split("#", 1)[0].split("?", 1)[0]
    if path.startswith("/"):
        return BASE + path
    if path.startswith("http"):
        return path
    return ""


def _parse_listed(text: str) -> str:
    """Relative age ("3 days ago", "5 hours ago") -> ISO date, else ""."""
    if not text:
        return ""
    low = text.lower()
    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year)", low)
    if not m:
        if any(w in low for w in ("today", "just", "hour")):
            return date.today().isoformat()
        return ""
    n, unit = int(m.group(1)), m.group(2)
    days = {"second": 0, "minute": 0, "hour": 0, "day": 1,
            "week": 7, "month": 30, "year": 365}[unit]
    return (date.today() - timedelta(days=n * days)).isoformat()


def _relative_hours(text: str) -> float:
    if not text:
        return 0.0
    low = text.lower()
    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year)", low)
    if not m:
        return 0.0
    n, unit = int(m.group(1)), m.group(2)
    return {"second": 0, "minute": n / 60, "hour": n, "day": n * 24,
            "week": n * 24 * 7, "month": n * 24 * 30,
            "year": n * 24 * 365}[unit]


def _is_location_span(text: str) -> bool:
    low = text.lower()
    return ("philippines" in low or "metro manila" in low
            or "manila" in low or "," in text)


def _is_type_span(text: str) -> bool:
    return text.strip().lower() in _TYPE_WORDS


def parse_cards(html: str) -> list[dict]:
    """Parse one search-results page into raw card dicts."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select(CARD_SELECTOR):
        link = li.select_one("h3 a")
        title = link.get_text(strip=True) if link else ""
        href = link.get("href", "") if link else ""
        data_url = li.get("data-url", "")
        age_el = li.select_one("p.text-muted small") or li.select_one("p.text-muted")
        listed = age_el.get_text(" ", strip=True) if age_el else ""
        location, company = "", ""
        for sp in li.select(".nf-job-list-info span"):
            txt = sp.get_text(" ", strip=True)
            if not txt or "₱" in txt or _is_type_span(txt):
                continue
            if not location and _is_location_span(txt):
                location = txt
            elif not company:
                company = txt
        desc_el = li.select_one("p.mb-0")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""
        out.append({
            "title": title,
            "company": company,
            "location": location,
            "listed": listed,
            "job_url": _clean_url(href, data_url),
            "description": desc,
        })
    return out


def scrape_trabajo(terms, where="Metro Manila, Philippines", max_results=50,
                   hours_old=None, delay_s: float = 1.0,
                   session: requests.Session | None = None) -> pd.DataFrame:
    """Scrape several terms over shared HTTP session. Never raises: a
    failed term returns whatever was collected (possibly empty)."""
    sess = session or requests.Session()
    sess.headers.update(HEADERS)
    frames = []
    for term in terms:
        rows: list[dict] = []
        seen: set[str] = set()
        page = 1
        max_pages = max(1, (max_results + 19) // 20)
        while page <= max_pages and len(rows) < max_results:
            url = _search_url(term, where, page)
            try:
                r = sess.get(url, timeout=30)
                if r.status_code != 200:
                    print(f"[trabajo] '{term}' page {page}: HTTP {r.status_code}")
                    break
                if "challenge" in r.text[:2000].lower() and "awswaf" in r.text[:4000].lower():
                    print(f"[trabajo] '{term}' page {page}: WAF challenge -- stopping term.")
                    break
                cards = parse_cards(r.text)
            except Exception as e:
                print(f"[trabajo] WARNING: '{term}' page {page} failed: {e}")
                break
            if not cards:
                print(f"[trabajo] '{term}' page {page}: 0 cards -- stopping term.")
                break
            fresh = skipped_dup = skipped_age = 0
            for c in cards:
                key = (c.get("job_url") or "").lower().rstrip("/")
                if not key or key in seen:
                    skipped_dup += 1
                    continue
                if hours_old and _relative_hours(c.get("listed", "")) > hours_old:
                    skipped_age += 1
                    continue
                seen.add(key)
                fresh += 1
                rows.append({
                    "title": c.get("title", ""),
                    "company": c.get("company", ""),
                    "location": c.get("location", ""),
                    "date_posted": _parse_listed(c.get("listed", "")),
                    "job_url": c.get("job_url", ""),
                    "description": c.get("description", ""),
                    "site": "trabajo",
                })
                if len(rows) >= max_results:
                    break
            print(f"[trabajo] '{term}' page {page}: {len(cards)} cards, "
                  f"+{fresh} fresh (dup:{skipped_dup} age:{skipped_age}) "
                  f"total {len(rows)}/{max_results}")
            if fresh == 0:
                break
            page += 1
            if page <= max_pages and len(rows) < max_results:
                time.sleep(delay_s)
        if rows:
            df = pd.DataFrame(rows)
            df["matched_search_term"] = term
            frames.append(df)
            print(f"[trabajo] '{term}': {len(rows)} jobs")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    out = scrape_trabajo(["Junior Developer"], "Metro Manila, Philippines",
                         max_results=10)
    print(f"[trabajo] found {len(out)} jobs")
    if not out.empty:
        print(out[["title", "company", "location", "date_posted"]].head(10).to_string())
