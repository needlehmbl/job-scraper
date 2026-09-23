"""
JobStreet (ph.jobstreet.com) scraper.

python-jobspy has no JobStreet provider, so we render the public search page
with a headless Chromium via Playwright and read the result cards out of the
DOM. The cards expose stable `data-automation` hooks, which is a lot less
fragile than reverse-engineering JobStreet's (Cloudflare-gated) GraphQL API.

Used by scraper.py; the returned columns match the tracker schema
(title, company, location, date_posted, job_url, description).
"""
import re
from datetime import date, timedelta

import pandas as pd

BASE = "https://ph.jobstreet.com"
SEARCH_URL = BASE + "/jobs?query={query}&where={where}&page={page}"
CARD_SELECTOR = 'article[data-automation="normalJob"]'
PAGE_SIZE = 30

# Persistent login: reuse Cloudflare/Gmail cookies across runs.
# Login once via `python -m jobstreet --login` (headed window) and the
# file below is created. Subsequent scrapes load it if present.
import pathlib as _pathlib
DEFAULT_STORAGE_STATE = _pathlib.Path(__file__).parent / "storage_state" / "jobstreet.json"

_EXTRACT_JS = """
els => els.map(a => {
  const txt = s => { const e = a.querySelector(s); return e ? e.innerText.trim() : ""; };
  const link = a.querySelector('[data-automation="jobTitle"]');
  return {
    id: a.getAttribute('data-job-id') || "",
    title: link ? link.innerText.trim() : "",
    href: link ? link.getAttribute('href') : "",
    company: txt('[data-automation="jobCompany"]'),
    location: txt('[data-automation="jobLocation"]'),
    listed: txt('[data-automation="jobListingDate"]'),
    description: txt('[data-automation="jobShortDescription"]'),
    salary: txt('[data-automation="jobSalary"]'),
  };
})
"""


from locations import is_metro_manila


def _where_for_jobstreet(location: str) -> str:
    """JobStreet's `where` resolver chokes on the full ", Philippines" suffix
    (it returns zero results), so search on the bare locality instead."""
    return re.sub(r",\s*philippines\s*$", "", location.strip(), flags=re.IGNORECASE)


def _clean_url(href: str, job_id: str) -> str:
    if href:
        path = href.split("#", 1)[0].split("?", 1)[0]
        if path:
            return BASE + path
    return f"{BASE}/job/{job_id}" if job_id else ""


def _parse_listed(text: str) -> str:
    """Turn JobStreet's relative dates ("26d ago", "3h ago", "30d+ ago") into
    an ISO date. Returns "" when nothing parseable is present."""
    if not text:
        return ""
    low = text.lower()
    m = re.search(r"(\d+)\s*([dhm])\s*\+?\s*ago", low)
    if m:
        n = int(m.group(1))
        if m.group(2) == "d":
            return (date.today() - timedelta(days=n)).isoformat()
        return date.today().isoformat()
    if "today" in low or "just" in low or "hour" in low or "minute" in low:
        return date.today().isoformat()
    return ""


def _relative_hours(text: str) -> float:
    """Age of a posting in hours, for the hours_old cutoff."""
    if not text:
        return 0.0
    low = text.lower()
    m = re.search(r"(\d+)\s*([dhm])\s*\+?\s*ago", low)
    if not m:
        return 0.0
    n = int(m.group(1))
    return {"d": n * 24, "h": n, "m": n / 60}[m.group(2)]


class JobStreetScraper:
    """Context manager wrapping a single headless Chromium for many searches."""

    def __init__(self, headless: bool = True, timeout_ms: int = 60000, storage_state: str | pathlib.Path | None = None):
        self.headless = headless
        self.timeout_ms = timeout_ms
        # None = auto-detect DEFAULT_STORAGE_STATE if it exists; False/"" = force anonymous
        if storage_state is None:
            storage_state = str(DEFAULT_STORAGE_STATE) if DEFAULT_STORAGE_STATE.exists() else None
        self.storage_state = str(storage_state) if storage_state else None
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx_kwargs = dict(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-PH",
            viewport={"width": 1400, "height": 900},
        )
        if self.storage_state:
            ctx_kwargs["storage_state"] = self.storage_state
            print(f"[jobstreet] using storage_state: {self.storage_state}")
        self._context = self._browser.new_context(**ctx_kwargs)
        self._page = self._context.new_page()
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
               hours_old: int | None = None) -> list[dict]:
        """Scrape up to `max_results` cards for one search term, paging as
        needed. Never raises: a failed page returns whatever was collected."""
        import time as _time
        results: list[dict] = []
        seen: set[str] = set()
        page_no = 1
        max_pages = max(1, (max_results + PAGE_SIZE - 1) // PAGE_SIZE)

        while page_no <= max_pages and len(results) < max_results:
            url = SEARCH_URL.format(
                query=term.replace(" ", "%20"),
                where=where.replace(" ", "%20"),
                page=page_no,
            )
            try:
                self._page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                # Cloudflare/interstitial detection
                try:
                    body = self._page.content()
                    if any(s in body for s in ("Just a moment", "cf-turnstile", "Attention Required", "cf-challenge")):
                        print(f"[jobstreet] Cloudflare challenge detected on '{term}' page {page_no} -- waiting 8s")
                        self._page.wait_for_timeout(8000)
                except Exception:
                    pass
                # Wait for cards, but fall back to a short networkidle grace
                try:
                    self._page.wait_for_selector(CARD_SELECTOR, timeout=15000)
                except Exception:
                    self._page.wait_for_timeout(4000)
                # Trigger lazy load
                try:
                    self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    self._page.wait_for_timeout(1200)
                except Exception:
                    pass
                cards = self._page.eval_on_selector_all(CARD_SELECTOR, _EXTRACT_JS)
            except Exception as e:
                print(f"[jobstreet] WARNING: '{term}' page {page_no} failed: {e}")
                break

            if not cards:
                # Log page source hint for selector breakage
                try:
                    snippet = self._page.content()[:800].replace("\n", " ")
                    print(f"[jobstreet] '{term}' page {page_no}: 0 cards (HTML hint: {snippet[:300]}...)")
                except Exception:
                    pass
                break

            fresh = 0
            skipped_hours = 0
            skipped_location = 0
            skipped_dup = 0
            for c in cards:
                job_url = _clean_url(c.get("href", ""), c.get("id", ""))
                key = job_url.lower().rstrip("/")
                if not key or key in seen:
                    skipped_dup += 1
                    continue
                if hours_old and _relative_hours(c.get("listed", "")) > hours_old:
                    skipped_hours += 1
                    continue
                if not is_metro_manila(c.get("location", "")):
                    skipped_location += 1
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
                })
                if len(results) >= max_results:
                    break

            print(f"[jobstreet] '{term}' page {page_no}: {len(cards)} cards, +{fresh} fresh (dup:{skipped_dup} loc:{skipped_location} age:{skipped_hours}) total {len(results)}/{max_results}")
            if fresh == 0:
                break
            page_no += 1
            # Gentle throttle between pages
            if page_no <= max_pages and len(results) < max_results:
                _time.sleep(1.2)

        return results


def scrape_jobstreet(terms, where, max_results=50, hours_old=None,
                     headless=True, storage_state=None) -> pd.DataFrame:
    """Convenience wrapper: scrape several terms in one browser session."""
    frames = []
    where = _where_for_jobstreet(where)
    with JobStreetScraper(headless=headless, storage_state=storage_state) as js:
        for term in terms:
            print(f"[jobstreet] searching: '{term}' in {where}")
            rows = js.search(term, where, max_results=max_results, hours_old=hours_old)
            if rows:
                df = pd.DataFrame(rows)
                df["matched_search_term"] = term
                frames.append(df)
                print(f"[jobstreet] '{term}': {len(rows)} jobs")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    import sys as _sys
    if "--login" in _sys.argv:
        # Headed login: open Chrome, let user finish Google + Cloudflare, then save storage_state
        import pathlib as _plogin
        _login_path = _plogin.Path(__file__).parent / "storage_state" / "jobstreet.json"
        _login_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[jobstreet] login: opening headed browser -> {BASE}")
        print(f"[jobstreet] login: sign in with Google, clear any Cloudflare challenge, then press Enter in this terminal to save.")
        from playwright.sync_api import sync_playwright as _spw
        _pw = _spw().start()
        # Prefer installed Chrome for Google login (more trusted), fall back to Chromium
        try:
            _browser = _pw.chromium.launch(headless=False, channel="chrome", args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        except Exception:
            _browser = _pw.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        _ctx = _browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="en-PH",
            viewport={"width": 1400, "height": 900},
        )
        _page = _ctx.new_page()
        _page.goto(BASE, wait_until="domcontentloaded")
        print(f"[jobstreet] login: browser ready at {BASE}. Finish login, then hit Enter here...")
        try:
            input()
        except EOFError:
            pass
        _ctx.storage_state(path=str(_login_path))
        print(f"[jobstreet] login: saved storage_state to {_login_path} ({_login_path.stat().st_size} bytes)")
        _ctx.close()
        _browser.close()
        _pw.stop()
        _sys.exit(0)
    out = scrape_jobstreet(["Junior Developer"], "Metro Manila", max_results=10)
    print(f"[jobstreet] found {len(out)} jobs")
    if not out.empty:
        print(out[["title", "company", "location", "date_posted", "job_url"]].head())
