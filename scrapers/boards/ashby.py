"""
Direct company-board scraping via Ashby's public posting API.

Same story as greenhouse.py / lever.py: companies whose careers page lives
at jobs.ashbyhq.com/<slug> expose every posting as JSON, no auth, no
browser:

    GET https://api.ashbyhq.com/posting-api/job-board/<slug>

Same row contract as jobstreet.py:
(title, company, location, date_posted, job_url, description, site,
matched_search_term). Location filtering is best-effort (loose PH gate);
scraper.py still runs the full post-scrape filters.
"""
import html
import re
from datetime import datetime, timezone

import pandas as pd
import requests

from locations import is_ph_or_metro

LIST_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_UA = {"User-Agent": "job-auto-apply/1.0 (local single-user job tracker)"}


def _strip_html(s: str) -> str:
    if not s:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\xa0]+", " ", text).strip()


def _parse_dt(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _age_hours(dt) -> float | None:
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600.0


def _board_specs(slugs) -> list[tuple[str, str]]:
    """Accept a list of slugs or a {slug: display name} mapping."""
    if isinstance(slugs, dict):
        return [(str(k).strip(), str(v).strip() or str(k).strip())
                for k, v in slugs.items() if str(k).strip()]
    return [(str(s).strip(), str(s).strip()) for s in (slugs or [])
            if str(s).strip()]


def _location_of(post: dict) -> str:
    loc = (post.get("location") or "").strip()
    if loc:
        return loc
    if post.get("isRemote"):
        return "Remote"
    return ""


def _map_posting(slug: str, company: str, post: dict) -> dict:
    return {
        "title": post.get("title", "") or "",
        "company": company,
        "location": _location_of(post),
        "date_posted": _parse_dt(post.get("publishedAt")),
        "job_url": post.get("jobUrl") or "",
        "description": _strip_html(post.get("descriptionHtml", "")),
        "site": "ashby",
        "matched_search_term": f"board:{slug}",
    }


def scrape_ashby(slugs, max_results: int = 50,
                 hours_old: float | None = None) -> pd.DataFrame:
    """Fetch PH/remote-relevant postings from one or more Ashby boards."""
    frames = []
    for slug, company in _board_specs(slugs):
        print(f"[ashby] board: '{slug}'")
        try:
            r = requests.get(LIST_URL.format(slug=slug), headers=_UA,
                             timeout=20)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            print(f"[ashby] WARNING: board '{slug}' failed: {e}")
            continue
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        rows = []
        for post in jobs:
            if not isinstance(post, dict):
                continue
            if not post.get("isListed", True):
                continue
            loc = _location_of(post)
            # Loose gate like greenhouse/lever: NCR passes, bare
            # country/remote passes, named non-NCR cities fail. Worldwide
            # "Remote" passes so remote-friendly roles stay in the funnel.
            if loc.lower() == "remote":
                pass
            elif not is_ph_or_metro(loc):
                continue
            if hours_old:
                age = _age_hours(_parse_dt(post.get("publishedAt")))
                if age is not None and age > hours_old:
                    continue
            rows.append(_map_posting(slug, company, post))
            if len(rows) >= max_results:
                break
        if rows:
            df = pd.DataFrame(rows)
            df["matched_search_term"] = f"board:{slug}"
            frames.append(df)
            print(f"[ashby] '{slug}': {len(rows)} jobs")
        else:
            print(f"[ashby] '{slug}': no PH-relevant jobs")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    import sys
    df = scrape_ashby(sys.argv[1:] or ["linear"], max_results=5)
    print(df[["title", "company", "location", "job_url"]].head()
          if not df.empty else "no jobs")
