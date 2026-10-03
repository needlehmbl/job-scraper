"""
RemoteOK (remoteok.com) scraper.

RemoteOK exposes its latest ~100 postings as public JSON at /api, no auth,
no browser. This fits the user's remote-friendly scope: postings are
worldwide-remote, location field is often empty (which passes the NCR gate
as unknown) or explicitly remote.

Same row contract as trabajo.py / jobstreet.py:
(title, company, location, date_posted, job_url, description, site,
matched_search_term, salary_raw). Term filtering is local (the API returns
one global feed); scraper.py still runs title/exp/location gates.
"""
import html
import re
from datetime import datetime, timezone

import pandas as pd
import requests

API_URL = "https://remoteok.com/api"
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


def _salary_raw(job: dict) -> str:
    lo, hi = job.get("salary_min"), job.get("salary_max")
    try:
        lo_i = int(lo) if lo not in (None, "") else 0
        hi_i = int(hi) if hi not in (None, "") else 0
    except (ValueError, TypeError):
        return ""
    if lo_i and hi_i:
        return f"${lo_i:,}-${hi_i:,}"
    if lo_i or hi_i:
        return f"${(lo_i or hi_i):,}"
    return ""


def _matches_terms(job: dict, terms: list[str]) -> str:
    """First matching search term for this job, or '' when none match."""
    hay = " ".join([
        str(job.get("position", "")),
        str(job.get("company", "")),
        " ".join(job.get("tags", []) or []),
    ]).lower()
    for term in terms or []:
        t = (term or "").strip().lower()
        if not t:
            continue
        # Match any significant word of the term so "Junior Python
        # Developer" hits a posting titled "Python Developer".
        words = [w for w in re.split(r"\W+", t) if len(w) >= 3]
        if any(w in hay for w in words):
            return term
    return ""


def _map_job(job: dict, term: str) -> dict:
    loc = (job.get("location") or "").strip() or "Remote"
    return {
        "title": job.get("position", "") or "",
        "company": job.get("company", "") or "",
        "location": loc,
        "date_posted": _parse_dt(job.get("date")),
        "job_url": job.get("url") or job.get("apply_url") or "",
        "description": _strip_html(job.get("description", "")),
        "salary_raw": _salary_raw(job),
        "site": "remoteok",
        "matched_search_term": term,
    }


def scrape_remoteok(terms, max_results: int = 50,
                    hours_old: float | None = None) -> pd.DataFrame:
    """Fetch RemoteOK's feed, keeping postings matching any term.

    Never raises: network failure returns empty DataFrame.
    """
    try:
        r = requests.get(API_URL, headers=_UA, timeout=25)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        print(f"[remoteok] WARNING: feed fetch failed: {e}")
        return pd.DataFrame()
    if not isinstance(payload, list):
        print("[remoteok] WARNING: unexpected feed shape")
        return pd.DataFrame()
    # payload[0] is a legal notice, the rest are postings.
    jobs = [j for j in payload[1:] if isinstance(j, dict)]
    print(f"[remoteok] feed: {len(jobs)} postings")
    rows = []
    for job in jobs:
        term = _matches_terms(job, list(terms or []))
        if not term:
            continue
        if hours_old:
            age = _age_hours(_parse_dt(job.get("date")))
            if age is not None and age > hours_old:
                continue
        rows.append(_map_job(job, term))
        if len(rows) >= max_results:
            break
    if rows:
        print(f"[remoteok]: {len(rows)} jobs matched")
        return pd.DataFrame(rows)
    print("[remoteok]: no matching jobs")
    return pd.DataFrame()


if __name__ == "__main__":
    df = scrape_remoteok(["Junior Developer", "Python"], max_results=5)
    print(df[["title", "company", "location", "job_url"]].head()
          if not df.empty else "no jobs")
