"""
Glassdoor (glassdoor.com) scraper.

python-jobspy's Glassdoor integration is unusable here: its country table
has no Philippines domain (hard exception), and even bypassed via the
global domain every location lookup -- PH and US alike -- hits Glassdoor's
bot-wall (403 "Security" page on findPopularLocationAjax.htm). Verified
2026-09-21 against jobspy 1.1.82.

So, like jobstreet.py, this renders the public search-results page with a
headless Chromium via Playwright and reads the result cards out of the DOM.
Passive page loads from a home IP pass the bot check; interactive searching
(form submits) triggers a "Just a moment..." challenge, so this module only
ever GETs pre-built search URLs and never touches the search form.

Location targeting needs Glassdoor's numeric location ID, which is NOT
resolvable programmatically (the autocomplete endpoint is the walled one).
IDs live in `config.yaml` -> `glassdoor_locations` (copy the IC number out
of any browser Glassdoor search URL for your city, e.g. Makati City's
4778930 in ..._IC4778930_...). One results page (30 cards) per term per
location, with a cooldown between loads -- rapid successive loads come back
empty (throttle), so a double-empty page aborts the run instead of burning
time and session reputation.

Returned columns match the tracker schema
(title, company, location, date_posted, job_url, description, site,
matched_search_term). Cards carry no usable age, so date_posted is always
"" (like company-board pulls, these skip the hours_old gate; keyword,
experience-from-snippet, location and learner filters still apply).
"""
import time
from urllib.parse import quote_plus

import pandas as pd

BASE = "https://www.glassdoor.com"
JOBS_URL = (BASE + "/Job/jobs.htm?sc.keyword={keyword}"
            "&locT=C&locId={loc_id}&locKeyword={slug}")
CARD_SELECTOR = "li[data-jobid]"
TITLE_SELECTOR = 'a[data-test="job-title"]'

# Cooldown between page loads: faster than this and Glassdoor starts
# serving empty result sets (throttle). One page per term per location
# keeps a full run to roughly a minute per term.
BETWEEN_LOADS_SECONDS = 20
RETRY_COOLDOWN_SECONDS = 45

_EXTRACT_JS = """
els => els.map(li => {
  const link = li.querySelector('a[data-test="job-title"]');
  const lines = (li.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const title = link ? link.innerText.trim() : '';
  const titleIdx = title ? lines.indexOf(title) : -1;
  return {
    jobid: li.getAttribute('data-jobid') || '',
    title: title,
    href: link ? link.getAttribute('href') : '',
    company: lines.length ? lines[0] : '',
    location: (titleIdx >= 0 && titleIdx + 1 < lines.length) ? lines[titleIdx + 1] : '',
    desc: (titleIdx >= 0 ? lines.slice(titleIdx + 2) : lines.slice(1)).join(' ').slice(0, 2000),
  };
})
"""


def _clean_url(href: str) -> str:
    """Canonical job URL: path only (the ?jl= impression param varies)."""
    if not href:
        return ""
    path = href.split("#", 1)[0].split("?", 1)[0]
    if path.startswith("/"):
        return BASE + path
    return path if path.startswith("http") else ""


class GlassdoorScraper:
    """Context manager wrapping a single headless Chromium for many searches."""

    def __init__(self, headless: bool = True, timeout_ms: int = 60000,
                 cooldown_s: int = BETWEEN_LOADS_SECONDS):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.cooldown_s = cooldown_s
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
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-PH",
            viewport={"width": 1400, "height": 900},
        )
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

    def _load_cards(self, url: str, term: str) -> list[dict]:
        """Load one results page; one cooldown retry on empty. [] if walled."""
        for attempt in (1, 2):
            try:
                self._page.goto(url, timeout=self.timeout_ms,
                                wait_until="domcontentloaded")
                self._page.wait_for_timeout(8000)
                cards = self._page.eval_on_selector_all(CARD_SELECTOR, _EXTRACT_JS)
            except Exception as e:
                print(f"[glassdoor] WARNING: '{term}' load failed "
                      f"(attempt {attempt}): {e}")
                cards = []
            if cards:
                return cards
            print(f"[glassdoor] '{term}': empty result set "
                  f"(attempt {attempt}) -- cooling down.")
            time.sleep(RETRY_COOLDOWN_SECONDS)
        return []

    def search(self, term: str, locations: list[dict],
               max_results: int = 50) -> tuple[list[dict], dict]:
        """Scrape one page per location for a term. Returns (rows, stats).

        stats maps a label to rows collected (for per-source health
        reporting). A double-empty page aborts the whole run: the session
        is hot and further loads will fail too.
        """
        results: list[dict] = []
        seen: set[str] = set()
        stats: dict[str, int] = {}
        for loc in locations:
            url = JOBS_URL.format(
                keyword=quote_plus(term),
                loc_id=loc["id"],
                slug=quote_plus(str(loc.get("slug", ""))),
            )
            label = f"{term} @ {loc.get('slug', loc['id'])}"
            cards = self._load_cards(url, label)
            if not cards:
                print(f"[glassdoor] aborting run (session throttled at '{label}').")
                stats[label] = 0
                break
            n = 0
            for c in cards:
                job_url = _clean_url(c.get("href", ""))
                key = job_url.lower().rstrip("/")
                if not key or key in seen:
                    continue
                seen.add(key)
                n += 1
                results.append({
                    "title": c.get("title", ""),
                    "company": c.get("company", ""),
                    "location": c.get("location", ""),
                    "date_posted": "",
                    "job_url": job_url,
                    "description": c.get("desc", ""),
                    "site": "glassdoor",
                })
                if len(results) >= max_results:
                    break
            stats[label] = n
            print(f"[glassdoor] '{label}': {n} jobs")
            if len(results) >= max_results:
                break
            time.sleep(self.cooldown_s)
        return results, stats


def scrape_glassdoor(terms, locations, max_results=50,
                     headless=True) -> tuple:
    """Convenience wrapper: scrape several terms in one browser session.

    Returns (DataFrame, per-term-label stats). Never raises: failures
    come back as empty frames so one board can't break a scrape.
    """
    if not locations:
        print("[glassdoor] no glassdoor_locations configured -- skipping.")
        return pd.DataFrame(), {}
    frames = []
    all_stats: dict[str, int] = {}
    try:
        with GlassdoorScraper(headless=headless) as gd:
            for term in terms:
                print(f"[glassdoor] searching: '{term}'")
                try:
                    rows, stats = gd.search(term, locations,
                                            max_results=max_results)
                except Exception as e:
                    print(f"[glassdoor] WARNING: '{term}' failed: {e}")
                    continue
                all_stats.update(stats)
                if rows:
                    df = pd.DataFrame(rows)
                    df["matched_search_term"] = term
                    frames.append(df)
                if stats and all(v == 0 for v in stats.values()):
                    break  # throttled: stop burning session reputation
    except Exception as e:
        print(f"[glassdoor] WARNING: browser session failed: {e}")
        if "Executable doesn't exist" in str(e):
            print("[glassdoor] HINT: run "
                  "'venv/bin/python -m playwright install chromium'.")
    if not frames:
        return pd.DataFrame(), all_stats
    return pd.concat(frames, ignore_index=True), all_stats


if __name__ == "__main__":
    out, stats = scrape_glassdoor(
        ["Junior Developer"], [{"slug": "Makati City", "id": 4778930}],
        max_results=10)
    print(f"[glassdoor] found {len(out)} jobs")
    if not out.empty:
        print(out[["title", "company", "location"]].head(10).to_string())
