"""
Scrapes job postings across configured sites/terms using python-jobspy,
applies keyword + experience filters, and returns a deduplicated DataFrame.

JobStreet is not supported by jobspy, so it is handled separately by
jobstreet.py (headless Chromium); Glassdoor's jobspy integration is
bot-walled, so glassdoor.py renders its search pages the same way;
direct company boards (Greenhouse/Lever
JSON APIs) are handled by greenhouse.py / lever.py. This module merges all
frames in.
"""
import re
import threading

from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml
import pandas as pd
from jobspy import scrape_jobs

import glassdoor
import greenhouse
import jobstreet
import lever
from locations import is_metro_manila, is_ph_or_metro

# Last run's feedback-filter breakdown, filled by apply_feedback_filter
# (keys: heuristic_dropped, heuristic_reasons, ai_dropped, ai_provider).
# Single-user local tool: the pipeline reads this right after scrape().
last_feedback_report: dict = {}

# Per-source health for the last scrape(), filled at the end of every run:
# {source: {"terms": int, "rows": int, "errors": [str]}}.
# The pipeline turns all-attempted-but-empty sources into warnings so a
# silently-changed site layout surfaces instead of looking like "no jobs".
last_source_report: dict = {}


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


# Shared year-word: full, abbreviated ("3 yrs"), possessive ("5 years'")
# and acronym ("3 YOE") forms all appear in real postings. Ends with a
# letter-lookahead instead of \b -- there is no word boundary between a
# possessive apostrophe and the following space ("years' experience").
_YEAR = r"(?:years?['\u2019]?|yrs?|y\.?o\.?e\.?)(?![A-Za-z])"
# Filler words between the year and "experience" ("of relevant",
# "hands-on" -- hyphens included, they broke the old \w+ filler).
_FILL = r"(?:\s+[\w-]+){0,4}\s+"
# Work nouns that imply experience even when the literal word
# "experience" is absent ("3 years of backend development",
# "2 years exposure in IT support"). Deliberately narrow -- words like
# "business" or "warranty" must NOT match ("25 years in business").
_WORK_NOUN = (
    r"(?:experience|work|employment|development|engineering|programming|"
    r"coding|exposure|testing|design|support|administration|operations)"
)

_MAX_EXP_PAT = re.compile(
    rf"(\d{{1,2}})\b(?:\s*[-–to]+\s*(\d{{1,2}}))?\s*\+?\s*{_YEAR}{_FILL}experience\b",
    re.IGNORECASE,
)

# Catch the flipped phrasing: "experience of 3 years", "experience: 3 years".
_EXP_FIRST_PAT = re.compile(
    rf"experience\s*[:\-]?\s*(?:of|with|for|in)?\s*(\d{{1,2}})\b\s*\+?\s*{_YEAR}",
    re.IGNORECASE,
)

# Catch "3 or more years of experience" / "3 plus years experience".
_EXP_OR_MORE_PAT = re.compile(
    rf"(\d{{1,2}})\s+(?:or\s+more|plus)\s*{_YEAR}{_FILL}experience\b",
    re.IGNORECASE,
)

# Catch requirement phrasing with no literal "experience":
# "3 years of backend development", "at least 3 years in software
# development", "3 yrs related work", "2 years exposure in IT support".
_EXP_WORK_NOUN_PAT = re.compile(
    rf"(\d{{1,2}})\s*\+?\s*{_YEAR}\s+(?:of\s+|in\s+)?(?:[\w-]+\s+){{0,2}}{_WORK_NOUN}\b",
    re.IGNORECASE,
)

# Catch a bare year-acronym with nothing after it ("Senior QA with 4+
# YOE"). YOE is unambiguous so the plus is optional; bare "yrs" still
# needs the plus ("3+ yrs") to avoid matching stray durations.
_EXP_BARE_PAT = re.compile(
    r"(\d{1,2})\s*\+?\s*y\.?o\.?e\.?(?![A-Za-z])"
    r"|(\d{1,2})\s*\+\s*yrs?(?![A-Za-z])",
    re.IGNORECASE,
)

_NO_EXP_PAT = re.compile(
    r"\b(?:no\s+experience|fresh\s+graduate|entry[- ]level|0\s*years?\s*experience)\b",
    re.IGNORECASE,
)


def max_experience_years(text) -> float | None:
    """Highest explicit year requirement stated in the text, or None when no
    years-of-experience requirement is mentioned. Postings that say "no
    experience" / "fresh graduate" come back as 0."""
    if not isinstance(text, str) or not text.strip():
        return None
    low = re.sub(r"\s+", " ", text.lower())
    if _NO_EXP_PAT.search(low):
        return 0.0
    values = []
    for m in _MAX_EXP_PAT.finditer(low):
        values.extend(int(v) for v in m.groups() if v)
    for m in _EXP_FIRST_PAT.finditer(low):
        values.append(int(m.group(1)))
    for m in _EXP_OR_MORE_PAT.finditer(low):
        values.append(int(m.group(1)))
    for m in _EXP_WORK_NOUN_PAT.finditer(low):
        values.append(int(m.group(1)))
    for m in _EXP_BARE_PAT.finditer(low):
        values.extend(int(v) for v in m.groups() if v)
    return float(max(values)) if values else None


def normalize_url(url) -> str:
    if not isinstance(url, str):
        return ""
    return url.strip().lower().rstrip("/")


# Concurrency: every source is I/O-bound (HTTP / browser waits), so the
# term x site searches and the four source blocks run in threads. Filters
# are untouched -- this only overlaps waiting, never skips work.
_JOBSPY_WORKERS = 6
_SOURCE_WORKERS = 4

# Per-site throttle for jobspy calls. 2026-09-22: 6-wide parallel
# LinkedIn searches drew "too many 429" rate-limiting (sequential runs
# never did), so LinkedIn goes one-at-a-time while Indeed etc. keep
# full parallelism. Add a site here only with 429 evidence.
_SITE_SEMAPHORES = {
    "linkedin": threading.Semaphore(1),
}


def _jobspy_kwargs(s: dict, site: str, term: str) -> dict:
    kwargs = dict(
        site_name=[site],
        search_term=term,
        location=s["location"],
        results_wanted=s.get("results_wanted", 50),
        country_indeed=s.get("country_indeed", "Philippines"),  # required by Indeed/Glassdoor
        linkedin_fetch_description=s.get("linkedin_fetch_description", True),
    )

    # Google Jobs ignores search_term/location and filters only via
    # google_search_term -- build one per search term so enabling
    # "google" in site_names actually scopes results.
    if site == "google":
        kwargs["google_search_term"] = (
            f"{term} jobs in {s['location']}"
        )

    # jobspy's Indeed integration rejects combining is_remote with
    # hours_old in one call (400 error) -- only pass is_remote through
    # when it's actually True (a remote-only search). For onsite/hybrid
    # searches (is_remote: false), we skip it entirely and rely on
    # hours_old + the post-scrape filters instead.
    if s.get("is_remote", False):
        kwargs["is_remote"] = True
    else:
        kwargs["hours_old"] = s.get("hours_old", 72)
    return kwargs


def _jobspy_one(site: str, term: str, kwargs: dict):
    """One jobspy call in a worker thread. Returns (df_or_None, error_str)."""
    sem = _SITE_SEMAPHORES.get(site)
    try:
        if sem is not None:
            with sem:
                df = scrape_jobs(**kwargs)
        else:
            df = scrape_jobs(**kwargs)
    except Exception as e:
        return None, f"{term}: {e}"
    if df is not None and not df.empty:
        df = df.copy()
        df["matched_search_term"] = term
        return df, ""
    return None, ""


def _run_jobspy_block(s: dict, terms: list, jobspy_sites: list):
    """All jobspy term x site searches, concurrently.

    Returns (frames, notes) with frames in the original term-major order
    so within-run dedupe keeps the same winner as the sequential run.
    notes are (source, rows, error) triples for the caller's note().
    """
    for term in terms:
        print(f"[scraper] searching: '{term}' in {s['location']}")
    tasks = [(ti, si, site, term)
             for ti, term in enumerate(terms)
             for si, site in enumerate(jobspy_sites)]
    if not tasks:
        return [], []
    results: dict = {}
    with ThreadPoolExecutor(max_workers=min(_JOBSPY_WORKERS, len(tasks))) as pool:
        futs = {pool.submit(
            _jobspy_one, site, term, _jobspy_kwargs(s, site, term)): (ti, si, site, term)
            for ti, si, site, term in tasks}
        for f in as_completed(futs):
            ti, si, site, term = futs[f]
            try:
                df, err = f.result()
            except Exception as e:  # never lose a search to a worker crash
                df, err = None, f"{term}: {e}"
            results[(ti, si)] = (df, err)
    frames, notes = [], []
    for ti, term in enumerate(terms):
        for si, site in enumerate(jobspy_sites):
            df, err = results[(ti, si)]
            if df is not None:
                frames.append(df)
                notes.append((site, len(df), ""))
            elif err:
                print(f"[scraper] WARNING: '{site}' search for '{term}' failed: {err}")
                notes.append((site, 0, err))
            else:
                print(f"[scraper] '{site}' returned no results for '{term}'")
                notes.append((site, 0, ""))
    return frames, notes


def _run_jobstreet_block(s: dict, terms: list):
    """JobStreet browser block in a worker thread. Returns (frames, notes)."""
    try:
        js_df = jobstreet.scrape_jobstreet(
            terms,
            s["location"],
            max_results=s.get("results_wanted", 50),
            hours_old=s.get("hours_old"),
        )
    except Exception as e:
        print(f"[scraper] WARNING: jobstreet scrape failed: {e}")
        if "Executable doesn't exist" in str(e):
            print("[scraper] HINT: Playwright's browser build is missing -- run "
                  "'venv/bin/python -m playwright install chromium' to fix.")
        return [], [("jobstreet", 0, str(e))]
    if js_df is not None and not js_df.empty:
        return [js_df], [("jobstreet", len(js_df), "")]
    return [], [("jobstreet", 0, "")]


def _run_glassdoor_block(cfg: dict, s: dict, terms: list):
    """Glassdoor browser block in a worker thread. Returns (frames, notes)."""
    try:
        g_df, g_stats = glassdoor.scrape_glassdoor(
            terms,
            (cfg.get("glassdoor_locations")
             or [{"slug": "Makati City", "id": 4778930}]),
            max_results=s.get("results_wanted", 50),
        )
    except Exception as e:
        print(f"[scraper] WARNING: glassdoor scrape failed: {e}")
        if "Executable doesn't exist" in str(e):
            print("[scraper] HINT: Playwright's browser build is missing -- run "
                  "'venv/bin/python -m playwright install chromium' to fix.")
        return [], [("glassdoor", 0, str(e))]
    frames = [g_df] if g_df is not None and not g_df.empty else []
    notes = [("glassdoor", n, "") for _, n in (g_stats or {}).items()]
    if g_df is not None and g_df.empty and not g_stats:
        notes.append(("glassdoor", 0, ""))
    return frames, notes


def _run_boards_block(cfg: dict, s: dict, seen_urls: set | None = None):
    """Greenhouse/Lever board pulls. Returns (frames, notes)."""
    frames, notes = [], []
    for mod, label in ((greenhouse, "greenhouse"), (lever, "lever")):
        slugs = (cfg.get("company_boards") or {}).get(label, [])
        if not slugs:
            continue
        try:
            if label == "greenhouse":
                b_df = mod.scrape_greenhouse(
                    slugs,
                    max_results=s.get("results_wanted", 50),
                    hours_old=None,
                    seen_urls=seen_urls,
                )
            else:
                b_df = mod.scrape_lever(
                    slugs,
                    max_results=s.get("results_wanted", 50),
                    hours_old=None,
                )
        except Exception as e:
            print(f"[scraper] WARNING: {label} boards scrape failed: {e}")
            notes.append((label, 0, str(e)))
            continue
        if b_df is not None and not b_df.empty:
            b_df["_loose_location"] = True
            frames.append(b_df)
            notes.append((label, len(b_df), ""))
        elif b_df is not None:
            notes.append((label, 0, ""))
    return frames, notes


def scrape(cfg: dict, seen_urls: set | None = None) -> pd.DataFrame:
    s = cfg["search"]
    all_frames = []
    global last_source_report
    stats: dict = {}  # source -> {"terms", "rows", "errors"}

    def note(source: str, rows: int = 0, error: str = ""):
        e = stats.setdefault(source, {"terms": 0, "rows": 0, "errors": []})
        e["terms"] += 1
        e["rows"] += rows
        if error and error not in e["errors"]:
            e["errors"].append(error[:160])

    # jobspy only knows its own providers; JobStreet and Glassdoor are
    # scraped by jobstreet.py / glassdoor.py (headless Chromium).
    sites = s.get("site_names", ["indeed", "linkedin"])
    jobspy_sites = [x for x in sites if x not in ("jobstreet", "glassdoor")]
    use_jobstreet = "jobstreet" in sites
    use_glassdoor = "glassdoor" in sites
    terms = s["search_terms"]

    # All four source blocks are independent I/O-bound work (HTTP waits,
    # browser page loads, Glassdoor cooldown sleeps), so they run
    # concurrently. Frames merge in fixed order (jobspy, jobstreet,
    # glassdoor, boards) and every note() still happens on this thread,
    # so stats, warnings and within-run dedupe behave exactly as before.
    blocks: dict = {}
    with ThreadPoolExecutor(max_workers=_SOURCE_WORKERS) as pool:
        futs = {}
        if jobspy_sites:
            futs[pool.submit(_run_jobspy_block, s, terms, jobspy_sites)] = "jobspy"
        if use_jobstreet:
            futs[pool.submit(_run_jobstreet_block, s, terms)] = "jobstreet"
        if use_glassdoor:
            futs[pool.submit(_run_glassdoor_block, cfg, s, terms)] = "glassdoor"
        futs[pool.submit(_run_boards_block, cfg, s, seen_urls)] = "boards"
        for f in as_completed(futs):
            blocks[futs[f]] = f.result()
    for name in ("jobspy", "jobstreet", "glassdoor", "boards"):
        frames, notes = blocks.get(name, ([], []))
        all_frames.extend(frames)
        for source, rows, error in notes:
            note(source, rows=rows, error=error)

    last_source_report = stats
    for source, st in stats.items():
        if st["terms"] and not st["rows"]:
            print(f"[scraper] WARNING: '{source}' returned 0 rows across "
                  f"{st['terms']} searches -- the site layout may have changed.")

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)

    # Dedup within this run via normalized URL (falls back to title+company).
    if "job_url" in combined.columns:
        combined = combined[combined["job_url"].notna() & combined["job_url"].ne("")]
        combined["_urlkey"] = combined["job_url"].map(normalize_url)
        usable = combined[combined["_urlkey"].ne("")]
        if not usable.empty:
            usable = usable.drop_duplicates(subset=["_urlkey"])
            combined = pd.concat(
                [usable.drop(columns="_urlkey"), combined[combined["_urlkey"].eq("")]],
                ignore_index=True,
            )
    else:
        combined = combined.drop_duplicates(subset=["title", "company", "location"])

    combined = apply_keyword_filters(combined, s)
    combined = apply_feedback_filter(combined, cfg, seen_urls)
    return combined.reset_index(drop=True)


def apply_keyword_filters(df: pd.DataFrame, s: dict) -> pd.DataFrame:
    def title_ok(title: str) -> bool:
        if not isinstance(title, str):
            return False
        low = title.lower()
        if any(k.lower() in low for k in s.get("exclude_title_keywords", [])):
            return False
        includes = s.get("include_title_keywords", [])
        if includes and not any(k.lower() in low for k in includes):
            return False
        return True

    def company_ok(company: str) -> bool:
        if not isinstance(company, str):
            return True
        low = company.lower()
        return not any(k.lower() in low for k in s.get("exclude_company_keywords", []))

    def experience_ok(description) -> bool:
        cap = s.get("max_experience_years")
        if not cap or not isinstance(description, str) or not description.strip():
            return True
        req = max_experience_years(description)
        return req is None or req <= cap

    mask = df["title"].apply(title_ok) & df["company"].apply(company_ok)
    if "location" in df.columns:
        def loc_ok(row) -> bool:
            # Curated company boards get the looser PH gate (bare
            # "Philippines" passes); open board searches stay strict.
            if row.get("_loose_location") is True:
                return is_ph_or_metro(row.get("location", ""))
            return is_metro_manila(row.get("location", ""))
        mask = mask & df.apply(loc_ok, axis=1)
    if "description" in df.columns:
        mask = mask & df["description"].apply(experience_ok)
    out = df[mask]
    # Internal marker, never persisted (pipeline only reads known keys).
    if "_loose_location" in out.columns:
        out = out.drop(columns=["_loose_location"])
    return out


def apply_feedback_filter(df: pd.DataFrame, cfg: dict,
                            seen_urls: set | None = None) -> pd.DataFrame:
    """Drop postings resembling past negatively-decided dashboard rows.

    See feedback.py. Best-effort: any error returns the input unchanged so
    a broken learner can never break a scrape.
    """
    try:
        import feedback
        before = len(df)
        report: dict = {}
        out = feedback.apply_feedback(df, cfg, report=report, seen_urls=seen_urls)
        global last_feedback_report
        last_feedback_report = report
        if out is not None and len(out) != before:
            print(f"[scraper] feedback filter: {before} -> {len(out)} jobs")
        return out if out is not None else df
    except Exception as e:
        print(f"[scraper] WARNING: feedback filter failed ({e}); keeping all jobs.")
        return df


if __name__ == "__main__":
    cfg = load_config()
    jobs = scrape(cfg)
    print(f"[scraper] found {len(jobs)} jobs after filtering")
    if not jobs.empty:
        jobs.to_csv("raw_jobs.csv", index=False)
        print("[scraper] wrote raw_jobs.csv")