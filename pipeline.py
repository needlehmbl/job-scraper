"""
Shared scrape pipeline: scrape -> filter -> dedupe (within run + vs tracker)
-> Postgres.

Used by both the CLI (`main.py`) and the dashboard API
(`api/routes/scrape.py`) so the "Scrape new jobs" button runs exactly the
same code as `venv/bin/python main.py`.
"""
import math
from datetime import datetime, timezone

from scraper import load_config, scrape

import db
import scraper as scraper_mod
import tracker


def _is_missing(value) -> bool:
    """True for NaN floats and pandas NaT dates."""
    if isinstance(value, float):
        return math.isnan(value)
    try:
        import pandas as pd
        if value is pd.NaT:
            return True
    except Exception:
        pass
    return False


def _clean(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _source(row) -> str:
    """Map the scraper's site label onto the schema's source enum."""
    site = _clean(row.get("site", "")).lower()
    if not site:
        url = _clean(row.get("job_url", "")).lower()
        if "jobstreet" in url:
            return "jobstreet"
        if "boards.greenhouse.io" in url or "greenhouse" in url:
            return "greenhouse"
        if "lever.co" in url:
            return "lever"
        if "indeed" in url:
            return "indeed"
        if "linkedin" in url:
            return "linkedin"
        if "glassdoor" in url:
            return "glassdoor"
        if "google" in url:
            return "google"
        return ""
    for needle in ("indeed", "linkedin", "jobstreet", "glassdoor", "google",
                     "greenhouse", "lever"):
        if needle in site:
            return needle
    return site


def _jrow(job, bank_low=None, _score_fn=None) -> dict:
    dpost = job.get("date_posted")
    if dpost is None or _is_missing(dpost):
        dpost = None
    elif isinstance(dpost, str) and not dpost.strip():
        dpost = None  # dateless rows (Glassdoor cards, board list rows) -> NULL, not ''
    row = {
        "source": _source(job),
        "title": _clean(job.get("title", "")),
        "company": _clean(job.get("company", "")),
        "url": _clean(job.get("job_url", "")).lower().rstrip("/"),
        "location": _clean(job.get("location", "")),
        "date_posted": dpost,
        "status": "NEW",
        # Stored for the learner's desc-skill patterns (capped: some
        # boards return very long HTML-stripped text).
        "description": _clean(job.get("description", ""))[:20000] or "",
    }
    try:
        import score as score_mod
        fn = _score_fn or score_mod.score_job
        s, reason = fn(row["title"], row["company"],
                       _clean(job.get("description", "")), bank_low)
        row["score"] = s
        row["score_reason"] = reason
    except Exception as e:
        print(f"[pipeline] WARNING: scoring failed ({e}); defaulting to 0.")
        row["score"] = 0
        row["score_reason"] = ""
    return row


def _xrow(job) -> dict:
    return {
        "status": "NEW",
        "title": _clean(job.get("title", "")),
        "company": _clean(job.get("company", "")),
        "location": _clean(job.get("location", "")),
        "date_posted": job.get("date_posted", ""),
        "job_url": _clean(job.get("job_url", "")),
        "search_term": _clean(job.get("matched_search_term", "")),
        "notes": "",
    }


def run_scrape(legacy_xlsx: bool = False) -> dict:
    """Run one full scrape and return a summary dict.

    Returns {"added", "scraped", "summary", "filtered", "filter_reasons",
    "filtered_saved"}. Raises on fatal errors (DB connection failure, ...);
    an empty result (no jobs found) is NOT an error -- it returns added=0,
    scraped=0.
    """
    started = datetime.now(timezone.utc)
    cfg = load_config()
    try:
        db.ensure_tracking_schema()
        db.ensure_filtered_schema()
    except Exception as e:
        print(f"[pipeline] WARNING: tracking migration failed: {e}")
    try:
        seen_urls = db.known_urls()
    except Exception as e:
        print(f"[pipeline] WARNING: could not load stored URLs ({e}); "
              f"no fetch/AI skipping this run.")
        seen_urls = set()
    jobs = scrape(cfg, seen_urls=seen_urls)
    print(f"[pipeline] {len(jobs)} jobs after scraping + filters")
    src_report = dict(getattr(scraper_mod, "last_source_report", {}) or {})
    warnings = [
        f"{source}: 0 rows across {st['terms']} searches "
        f"(layout may have changed{'; ' + st['errors'][0] if st.get('errors') else ''})"
        for source, st in sorted(src_report.items())
        if st.get("terms") and not st.get("rows")
    ]
    for w in warnings:
        print(f"[pipeline] WARNING: {w}")
    fb = dict(getattr(scraper_mod, "last_feedback_report", {}) or {})
    heur_dropped = int(fb.get("heuristic_dropped", 0) or 0)
    ai_dropped = int(fb.get("ai_dropped", 0) or 0)
    from collections import Counter
    top_reasons = dict(Counter(fb.get("heuristic_reasons", []) or []).most_common(5))
    if ai_dropped and fb.get("ai_provider"):
        top_reasons[f"ai:{fb['ai_provider']}"] = ai_dropped
    filtered = heur_dropped + ai_dropped
    if filtered:
        print(f"[pipeline] feedback filtered {filtered}: {top_reasons}")
    # Hold dropped postings for review even when nothing survived -- the
    # dashboard's Filtered tab reads this table.
    filtered_saved = 0
    try:
        filtered_saved = db.save_filtered_jobs(list(fb.get("dropped_rows") or []))
        if filtered_saved:
            print(f"[pipeline] held {filtered_saved} filtered postings for review")
    except Exception as e:
        print(f"[pipeline] WARNING: could not save filtered postings: {e}")
    if jobs.empty:
        print("[pipeline] nothing found -- check config.yaml search terms/location.")
        summary = (
            f"[run] {datetime.now().strftime('%a %b %d %H:%M')} · "
            f"0 new · 0 scraped"
            + (" · WARN: " + " | ".join(warnings) if warnings else "")
        )
        with open(cfg["paths"]["runs_log"], "a") as f:
            f.write(summary + "\n")
        return {"added": 0, "scraped": 0, "summary": "no jobs found",
                "filtered": filtered, "filter_reasons": top_reasons,
                "filtered_saved": filtered_saved,
                "source_stats": src_report, "warnings": warnings}

    sheet_path = cfg["paths"]["tracker_sheet"]
    df = tracker.load_or_init(sheet_path) if legacy_xlsx else None

    try:
        import score as score_mod
        bank_low = score_mod.load_bank_text()
    except Exception as e:
        print(f"[pipeline] WARNING: could not load resume bank ({e}); scoring neutral.")
        bank_low = ""

    added = 0
    for _, job in jobs.iterrows():
        row = _jrow(job, bank_low)
        existing = db.find_existing(row)
        if existing is not None:
            try:
                db.touch_last_seen(existing["id"])
            except Exception as e:
                print(f"[pipeline] WARNING: could not touch last_seen: {e}")
            continue
        if db.upsert_job(row):
            added += 1
            if legacy_xlsx:
                df = tracker.upsert(df, _xrow(job))

    if legacy_xlsx:
        tracker.save(df, sheet_path)

    finished = datetime.now(timezone.utc)
    summary = (
        f"[run] {datetime.now().strftime('%a %b %d %H:%M')} · "
        f"{added} new · {len(jobs)} scraped"
        + (" · WARN: " + " | ".join(warnings) if warnings else "")
    )
    print(summary)

    try:
        db.record_run(started, finished, added, summary)
    except Exception as e:
        print(f"[pipeline] WARNING: could not log scrape run: {e}")

    run_log = cfg["paths"]["runs_log"]
    with open(run_log, "a") as f:
        f.write(summary + "\n")

    return {"added": added, "scraped": len(jobs), "summary": summary,
            "filtered": filtered, "filter_reasons": top_reasons,
            "filtered_saved": filtered_saved,
            "source_stats": src_report, "warnings": warnings}
