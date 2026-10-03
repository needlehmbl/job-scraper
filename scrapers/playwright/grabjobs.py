"""
GrabJobs (grabjobs.co) scraper — Playwright, jobstreet-style.

Plain GETs hit Cloudflare (403 challenge shell, verified 2026-10-02), so
like jobstreet.py this renders search pages with headless Chromium and
reads cards out of the DOM. Persistent storage_state
(storage_state/grabjobs.json) reuses clearance cookies across runs.

v1 note: search URL + selectors are best-effort. A 0-card page logs an
HTML hint and stops the term instead of raising, so markup drift surfaces
as a scrape warning, never a crash.

Returned columns match the tracker schema
(title, company, location, date_posted, job_url, description, site,
matched_search_term).
"""
import re
import time as _time
from datetime import date, timedelta
from urllib.parse import quote_plus

import pandas as pd

from .common import launch_context, storage_state_path

BASE = "https://grabjobs.co"
SEARCH_URL = BASE + "/ph/jobs?search={query}&location={where}&page={page}"
CARD_SELECTOR = "div[class*='job-card'], article, div[data-job-id]"
PAGE_SIZE = 20

DEFAULT_STORAGE_STATE = storage_state_path("grabjobs")

_EXTRACT_JS = """
els => els.map(a => {
  const txt = s => { const e = a.querySelector(s); return e ? e.innerText.trim() : ""; };
  const link = a.querySelector('a[href*="/job/"], a[href*="/ph/"]');
  const lines = (a.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  return {
    title: link ? link.innerText.trim() : txt('h2, h3'),
    href: link ? link.getAttribute('href') : "",
    company: txt('[class*="company"]') || (lines.length > 1 ? lines[1] : ""),
    location: txt('[class*="location"]') || (lines.length > 2 ? lines[2] : ""),
    listed: txt('time, [class*="date"], [class*="posted"]') || "",
    description: txt('p') || "",
    salary: txt('[class*="salary"], [class*="pay"]') || "",
  };
})
"""


def _where_for_grabjobs(location: str) -> str:
    loc = re.sub(r",\s*philippines\s*$", "", (location or "").strip(),
                 flags=re.IGNORECASE)
    return loc or "Metro Manila"


def _clean_url(href: str) -> str:
    if not href:
        return ""
    path = href.split("#", 1)[0].split("?", 1)[0]
    if path.startswith("/"):
        return BASE + path
    if path.startswith("http"):
        return path
    return ""


def _parse_listed(text: str) -> str:
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


class GrabJobsScraper:
    """Context manager wrapping a single headless Chromium for many searches."""

    def __init__(self, headless: bool = True, timeout_ms: int = 60000,
                 storage_state=None):
        self.headless = headless
        self.timeout_ms = timeout_ms
        if storage_state is None:
            storage_state = (str(DEFAULT_STORAGE_STATE)
                             if DEFAULT_STORAGE_STATE.exists() else None)
        self.storage_state = str(storage_state) if storage_state else None
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        if self.storage_state:
            print(f"[grabjobs] using storage_state: {self.storage_state}")
        self._browser, self._context, self._page = launch_context(
            self._pw, headless=self.headless,
            storage_state=self.storage_state)
        return self

    def __exit__(self, *exc):
        for closer in (self._context, self._browser):
            if closer:
                try:
                    closer.close()
                except Exception:
                    pass
        if self._pw:
            self._pw.stop()
        return False

    def search(self, term: str, where: str, max_results: int = 50,
               hours_old: float | None = None) -> list[dict]:
        results: list[dict] = []
        seen: set[str] = set()
        page_no = 1
        max_pages = max(1, (max_results + PAGE_SIZE - 1) // PAGE_SIZE)
        while page_no <= max_pages and len(results) < max_results:
            url = SEARCH_URL.format(
                query=quote_plus(term), where=quote_plus(where),
                page=page_no)
            try:
                self._page.goto(url, timeout=self.timeout_ms,
                                wait_until="domcontentloaded")
                try:
                    body = self._page.content()
                    if any(s in body for s in ("Just a moment",
                                               "cf-turnstile",
                                               "Attention Required",
                                               "cf-challenge")):
                        print(f"[grabjobs] challenge on '{term}' page {page_no} -- waiting 8s")
                        self._page.wait_for_timeout(8000)
                except Exception:
                    pass
                try:
                    self._page.wait_for_selector(CARD_SELECTOR, timeout=15000)
                except Exception:
                    self._page.wait_for_timeout(4000)
                try:
                    self._page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)")
                    self._page.wait_for_timeout(1200)
                except Exception:
                    pass
                cards = self._page.eval_on_selector_all(
                    CARD_SELECTOR, _EXTRACT_JS)
            except Exception as e:
                print(f"[grabjobs] WARNING: '{term}' page {page_no} failed: {e}")
                break
            if not cards:
                try:
                    snippet = self._page.content()[:800].replace("\n", " ")
                    print(f"[grabjobs] '{term}' page {page_no}: 0 cards "
                          f"(HTML hint: {snippet[:300]}...)")
                except Exception:
                    pass
                break
            fresh = skipped_dup = skipped_age = 0
            for c in cards:
                job_url = _clean_url(c.get("href", ""))
                key = job_url.lower().rstrip("/")
                if not key or key in seen:
                    skipped_dup += 1
                    continue
                if hours_old and _relative_hours(c.get("listed", "")) > hours_old:
                    skipped_age += 1
                    continue
                seen.add(key)
                fresh += 1
                results.append({
                    "title": c.get("title", ""),
                    "company": c.get("company", ""),
                    "location": c.get("location", ""),
                    "date_posted": _parse_listed(c.get("listed", "")),
                    "job_url": job_url,
                    "description": c.get("description", ""),
                    "salary_raw": c.get("salary", ""),
                })
                if len(results) >= max_results:
                    break
            print(f"[grabjobs] '{term}' page {page_no}: {len(cards)} cards, "
                  f"+{fresh} fresh (dup:{skipped_dup} age:{skipped_age}) "
                  f"total {len(results)}/{max_results}")
            if fresh == 0:
                break
            page_no += 1
            if page_no <= max_pages and len(results) < max_results:
                _time.sleep(1.5)
        return results


def scrape_grabjobs(terms, where="Metro Manila", max_results=50,
                    hours_old=None, headless=True,
                    storage_state=None) -> pd.DataFrame:
    """Scrape several terms in one browser session. Never raises."""
    frames = []
    where = _where_for_grabjobs(where)
    try:
        with GrabJobsScraper(headless=headless,
                             storage_state=storage_state) as scraper:
            for term in terms:
                print(f"[grabjobs] searching: '{term}' in {where}")
                rows = scraper.search(term, where, max_results=max_results,
                                      hours_old=hours_old)
                if rows:
                    df = pd.DataFrame(rows)
                    df["matched_search_term"] = term
                    frames.append(df)
                    print(f"[grabjobs] '{term}': {len(rows)} jobs")
    except Exception as e:
        print(f"[grabjobs] WARNING: browser scrape failed: {e}")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["site"] = "grabjobs"
    return out


if __name__ == "__main__":
    out = scrape_grabjobs(["Junior Developer"], "Metro Manila", max_results=5)
    print(f"[grabjobs] found {len(out)} jobs")
