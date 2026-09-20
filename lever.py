"""
Direct company-board scraping via Lever's public JSON API.

Same story as greenhouse.py: companies whose careers page lives at
lever.co/<slug> expose every posting (description included) as JSON, no
auth, no browser.

Same row contract as jobstreet.py:
(title, company, location, date_posted, job_url, description, site,
matched_search_term). Location/hours prefiltering here is best-effort;
scraper.py still runs the full post-scrape filters.
"""
import html
import re
from datetime import datetime, timezone

import pandas as pd
import requests

from locations import is_ph_or_metro

LIST_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
_UA = {"User-Agent": "job-auto-apply/1.0 (local single-user job tracker)"}


def _strip_html(s: str) -> str:
    if not s:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\xa0]+", " ", text).strip()


def _board_specs(slugs) -> list[tuple[str, str]]:
    """Accept a list of slugs or a {slug: display name} mapping."""
    if isinstance(slugs, dict):
        return [(str(k).strip(), str(v).strip() or str(k).strip())
                for k, v in slugs.items() if str(k).strip()]
    return [(str(s).strip(), str(s).strip()) for s in (slugs or [])
            if str(s).strip()]


def _map_posting(slug: str, company: str, p: dict) -> dict:
    cats = p.get("categories") or {}
    created = p.get("createdAt")
    posted = None
    if isinstance(created, (int, float)):
        # Lever timestamps are milliseconds since epoch.
        posted = datetime.fromtimestamp(created / 1000.0, tz=timezone.utc)
    return {
        "title": p.get("text", ""),
        "company": company,
        "location": cats.get("location", "") or "",
        "date_posted": posted,
        "job_url": p.get("hostedUrl") or p.get("applyUrl", ""),
        "description": (p.get("descriptionPlain")
                        or _strip_html(p.get("description", ""))),
        "site": "lever",
        "matched_search_term": f"board:{slug}",
    }


def scrape_lever(slugs, max_results: int = 50,
                 hours_old: float | None = None) -> pd.DataFrame:
    """Fetch PH-relevant postings from one or more Lever boards."""
    frames = []
    for slug, company in _board_specs(slugs):
        print(f"[lever] board: '{slug}'")
        try:
            r = requests.get(LIST_URL.format(slug=slug), headers=_UA,
                             timeout=20)
            r.raise_for_status()
            postings = r.json()
        except Exception as e:
            print(f"[lever] WARNING: board '{slug}' failed: {e}")
            continue
        if not isinstance(postings, list):
            print(f"[lever] WARNING: board '{slug}' returned unexpected data")
            continue
        rows = []
        now = datetime.now(timezone.utc)
        for p in postings:
            if not isinstance(p, dict):
                continue
            loc = (p.get("categories") or {}).get("location", "") or ""
            if not is_ph_or_metro(loc):
                continue
            if hours_old and isinstance(p.get("createdAt"), (int, float)):
                age = (now - datetime.fromtimestamp(
                    p["createdAt"] / 1000.0, tz=timezone.utc)).total_seconds() / 3600.0
                if age > hours_old:
                    continue
            rows.append(_map_posting(slug, company, p))
            if len(rows) >= max_results:
                break
        if rows:
            df = pd.DataFrame(rows)
            df["matched_search_term"] = f"board:{slug}"
            frames.append(df)
            print(f"[lever] '{slug}': {len(rows)} jobs")
        else:
            print(f"[lever] '{slug}': no PH-relevant jobs")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    import sys
    df = scrape_lever(sys.argv[1:] or ["lever"],
                      max_results=5, hours_old=24 * 90)
    print(df[["title", "company", "location", "job_url"]].head()
          if not df.empty else "no jobs")
