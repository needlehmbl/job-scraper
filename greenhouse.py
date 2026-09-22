"""
Direct company-board scraping via Greenhouse's public JSON API.

Many startups/SMBs never post on Indeed/LinkedIn -- their careers page is a
Greenhouse board (boards.greenhouse.io/<slug>). Greenhouse exposes the whole
board as JSON with no auth, so this is plain requests, no browser needed.

Same row contract as jobstreet.py:
(title, company, location, date_posted, job_url, description, site,
matched_search_term). Location/hours prefiltering here is best-effort to
save detail fetches; scraper.py still runs the full post-scrape filters.
"""
import html
import re
from datetime import datetime, timezone

import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor

from locations import is_ph_or_metro

LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
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


_DETAIL_WORKERS = 5


def _fetch_detail(slug: str, job_id) -> dict:
    """One Greenhouse job-detail fetch. Never raises -- {} on failure."""
    try:
        detail = _get(DETAIL_URL.format(slug=slug, job_id=job_id))
    except Exception as e:
        print(f"[greenhouse] WARNING: '{slug}' job {job_id} "
              f"detail failed: {e}")
        return {}
    return detail if isinstance(detail, dict) else {}


def _get(url: str):
    r = requests.get(url, headers=_UA, timeout=20)
    r.raise_for_status()
    return r.json()


def _board_specs(slugs) -> list[tuple[str, str]]:
    """Accept a list of slugs or a {slug: display name} mapping."""
    if isinstance(slugs, dict):
        return [(str(k).strip(), str(v).strip() or str(k).strip())
                for k, v in slugs.items() if str(k).strip()]
    return [(str(s).strip(), str(s).strip()) for s in (slugs or [])
            if str(s).strip()]


def _map_detail(slug: str, company: str, item: dict, detail: dict) -> dict:
    loc = (item.get("location") or {}).get("name", "") \
        if isinstance(item.get("location"), dict) else (item.get("location") or "")
    upd = _parse_dt(detail.get("updated_at") or item.get("updated_at"))
    created = _parse_dt(detail.get("created_at"))
    return {
        "title": item.get("title", ""),
        "company": company,
        "location": loc,
        "date_posted": created or upd,
        "job_url": item.get("absolute_url", ""),
        "description": _strip_html(detail.get("content", "")),
        "site": "greenhouse",
        "matched_search_term": f"board:{slug}",
    }


def scrape_greenhouse(slugs, max_results: int = 50,
                      hours_old: float | None = None) -> pd.DataFrame:
    """Fetch PH-relevant postings from one or more Greenhouse boards."""
    frames = []
    for slug, company in _board_specs(slugs):
        print(f"[greenhouse] board: '{slug}'")
        try:
            payload = _get(LIST_URL.format(slug=slug))
        except Exception as e:
            print(f"[greenhouse] WARNING: board '{slug}' failed: {e}")
            continue
        items = payload.get("jobs", []) if isinstance(payload, dict) else []
        # Pass 1 (cheap, local): location/age prefilter + max_results cap.
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                continue
            loc = item.get("location", "")
            loc_name = loc.get("name", "") if isinstance(loc, dict) else loc
            if not is_ph_or_metro(loc_name):
                continue
            if hours_old:
                age = _age_hours(_parse_dt(item.get("updated_at")))
                if age is not None and age > hours_old:
                    continue
            candidates.append(item)
            if len(candidates) >= max_results:
                break
        # Pass 2 (I/O-bound): detail fetches concurrently, rows mapped back
        # in board order so output order matches the sequential run.
        rows = []
        if candidates:
            with ThreadPoolExecutor(
                    max_workers=min(_DETAIL_WORKERS, len(candidates))) as pool:
                details = list(pool.map(
                    lambda it: _fetch_detail(slug, it.get("id")), candidates))
            rows = [_map_detail(slug, company, item, detail)
                    for item, detail in zip(candidates, details)]
        if rows:
            df = pd.DataFrame(rows)
            df["matched_search_term"] = f"board:{slug}"
            frames.append(df)
            print(f"[greenhouse] '{slug}': {len(rows)} jobs")
        else:
            print(f"[greenhouse] '{slug}': no PH-relevant jobs")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    import sys
    df = scrape_greenhouse(sys.argv[1:] or ["greenhouse"],
                           max_results=5, hours_old=24 * 90)
    print(df[["title", "company", "location", "job_url"]].head()
          if not df.empty else "no jobs")
