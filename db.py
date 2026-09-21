"""
Postgres access helpers for the job scraper (psycopg2, plain SQL -- no ORM).

Connects over the local unix socket with the current OS user (peer auth),
matching the native-Postgres local setup. Override with the DATABASE_URL
env var if you run against a remote/explicit-credentials database.

Usage:
    import db
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(...)

    db.upsert_job({...})          # insert or ignore duplicates
"""
import os

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///job_scraper")


def connect():
    return psycopg2.connect(DATABASE_URL)


def find_existing(row: dict, conn=None):
    """Return the tracker DB row matching a scraped job, or None.

    Mirrors the old xlsx dedupe logic: normalized `url` first (exact match),
    falling back to title+company+location when the job has no URL.
    """
    conn = conn or connect()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        url = (row.get("url") or "").lower().rstrip("/")
        if url:
            cur.execute("SELECT * FROM jobs WHERE url = %s", (url,))
            r = cur.fetchone()
            if r:
                return dict(r)
        cur.execute(
            """
            SELECT * FROM jobs
            WHERE url = ''
              AND lower(title) = lower(%s)
              AND lower(coalesce(company, '')) = lower(%s)
              AND lower(coalesce(location, '')) = lower(%s)
            LIMIT 1
            """,
            (row.get("title", ""), row.get("company", ""), row.get("location", "")),
        )
        r = cur.fetchone()
        return dict(r) if r else None


def upsert_job(row: dict, conn=None) -> bool:
    """Insert a job, skipping PostgreSQL UNIQUE violations on `url`.

    Returns True when the row was actually inserted (new job), False when it
    was already present. This mirrors the old xlsx dedupe path -- URL first
    (the UNIQUE constraint catches exact matches) with a title+company
    fallback in the `find_existing` helper the caller should use too.
    """
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            url = (row.get("url") or "").lower().rstrip("/")
            cur.execute(
                """
                INSERT INTO jobs (source, title, company, url, location, date_posted, status,
                                  score, score_reason)
                VALUES (%(source)s, %(title)s, %(company)s, %(url)s, %(location)s,
                        %(date_posted)s, %(status)s,
                        COALESCE(%(score)s, 0), COALESCE(%(score_reason)s, ''))
                """,
                {**row, "url": url},
            )
            conn.commit()
        return True
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return False
    finally:
        if own:
            conn.close()


def record_run(started_at, finished_at, new_jobs_count, summary, conn=None) -> int:
    """Log a finished scrape into `scrape_runs` and return its id."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrape_runs (started_at, finished_at, new_jobs_count, summary)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (started_at, finished_at, new_jobs_count, summary),
            )
            conn.commit()
            return cur.fetchone()[0]
    finally:
        if own:
            conn.close()


TRACKING_DDL = """
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS score INT NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS score_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS follow_up_at DATE DEFAULT NULL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS stage TEXT DEFAULT NULL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS offer_salary TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS offer_benefits TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS offer_pros TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS offer_cons TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS job_stage_history (
  id SERIAL PRIMARY KEY,
  job_id INT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  old_stage TEXT,
  new_stage TEXT,  -- NULL = left the pipeline (status moved off APPLIED)
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS job_stage_history_job_id_idx ON job_stage_history(job_id);
-- Earlier revision created new_stage NOT NULL; leaving the pipeline
-- legitimately records NULL, so relax it on existing databases too.
ALTER TABLE job_stage_history ALTER COLUMN new_stage DROP NOT NULL;
CREATE TABLE IF NOT EXISTS job_status_history (
  id SERIAL PRIMARY KEY,
  job_id INT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  old_status TEXT,
  new_status TEXT NOT NULL,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS job_status_history_job_id_idx ON job_status_history(job_id);
CREATE INDEX IF NOT EXISTS job_status_history_changed_at_idx ON job_status_history(changed_at);
"""


def ensure_tracking_schema(conn=None):
    """Idempotently add negative-status tracking columns + history table.

    Safe to call on every startup: uses IF NOT EXISTS guards. Also
    backfills status_updated_at for pre-existing rows from
    COALESCE(applied_at, scraped_at) so the outcome graph has data
    even before any new dashboard decisions are made.
    """
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(TRACKING_DDL)
            cur.execute(
                """
                UPDATE jobs
                SET status_updated_at = COALESCE(applied_at, scraped_at)
                WHERE status_updated_at IS NULL
                """
            )
            conn.commit()
    finally:
        if own:
            conn.close()


def record_status_change(job_id: int, old_status, new_status: str, conn=None):
    """Append one row to job_status_history (caller owns the jobs UPDATE)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_status_history (job_id, old_status, new_status)
                VALUES (%s, %s, %s)
                """,
                (job_id, old_status, new_status),
            )
            conn.commit()
    finally:
        if own:
            conn.close()


def touch_last_seen(job_id: int, conn=None):
    """Mark a posting re-seen by the latest scrape (freshness tracking)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET last_seen = now() WHERE id = %s",
                        (job_id,))
            conn.commit()
    finally:
        if own:
            conn.close()


FILTERED_DDL = """
CREATE TABLE IF NOT EXISTS filtered_jobs (
  id SERIAL PRIMARY KEY,
  source TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  company TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  location TEXT,
  date_posted DATE,
  description TEXT,
  search_term TEXT,
  filter_reason TEXT NOT NULL DEFAULT '',
  filtered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  restored BOOLEAN NOT NULL DEFAULT FALSE
);
-- Partial unique index: real URLs dedupe, but rows scraped without a URL
-- (rare -- the scraper normally drops those) are still storable.
CREATE UNIQUE INDEX IF NOT EXISTS filtered_jobs_url_idx
  ON filtered_jobs (url) WHERE url <> '';
CREATE INDEX IF NOT EXISTS filtered_jobs_restored_idx ON filtered_jobs (restored);
"""


def ensure_filtered_schema(conn=None):
    """Idempotently create the filtered-for-review table. Safe every startup."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(FILTERED_DDL)
            conn.commit()
    finally:
        if own:
            conn.close()


def _filtered_source(row: dict) -> str:
    """Map a dropped posting onto the schema's source enum (no pipeline import)."""
    site = (row.get("site") or "").strip().lower()
    if site:
        for needle in ("indeed", "linkedin", "jobstreet", "glassdoor", "google"):
            if needle in site:
                return needle
        return site
    url = (row.get("job_url") or row.get("url") or "").lower()
    for needle in ("indeed", "linkedin", "jobstreet", "glassdoor", "google"):
        if needle in url:
            return needle
    return ""


def save_filtered_jobs(rows: list[dict], conn=None) -> int:
    """Persist feedback-filtered postings for dashboard review.

    Rows already tracked in `jobs` (e.g. previously restored) are skipped.
    Rows without a URL are skipped (they can't be restored meaningfully).
    A re-filtered URL refreshes its reason/time and re-appears
    (restored=FALSE). Returns the number of rows upserted.
    """
    if not rows:
        return 0
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            urls = [((r.get("job_url") or r.get("url") or "").strip().lower().rstrip("/"))
                    for r in rows]
            cur.execute("SELECT url FROM jobs WHERE url = ANY(%s)",
                        ([u for u in urls if u],))
            have = {x[0] for x in cur.fetchall()}
            n = 0
            for r, url in zip(rows, urls):
                if not url or url in have:
                    continue
                cur.execute(
                    """
                    INSERT INTO filtered_jobs
                      (source, title, company, url, location, date_posted,
                       description, search_term, filter_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (url) WHERE url <> '' DO UPDATE SET
                      source = EXCLUDED.source,
                      title = EXCLUDED.title,
                      company = EXCLUDED.company,
                      location = EXCLUDED.location,
                      date_posted = EXCLUDED.date_posted,
                      description = EXCLUDED.description,
                      search_term = EXCLUDED.search_term,
                      filter_reason = EXCLUDED.filter_reason,
                      filtered_at = now(),
                      restored = FALSE
                    """,
                    (_filtered_source(r),
                     (r.get("title") or "").strip(),
                     (r.get("company") or "").strip(),
                     url,
                     (r.get("location") or "").strip() or None,
                     r.get("date_posted"),
                     (r.get("description") or "").strip() or None,
                     (r.get("matched_search_term") or r.get("search_term") or "").strip() or None,
                     (r.get("filter_reason") or "").strip()),
                )
                n += 1
            conn.commit()
            return n
    finally:
        if own:
            conn.close()


def list_filtered_jobs(include_restored: bool = False, conn=None) -> list[dict]:
    """Newest-first filtered postings; restored ones hidden unless asked for."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM filtered_jobs
                WHERE (restored = FALSE OR %s)
                ORDER BY filtered_at DESC
                """,
                (include_restored,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()


def restore_filtered_job(fid: int, conn=None) -> dict | None:
    """Move a filtered posting back into `jobs` as NEW for applying.

    Returns the jobs row (existing one if the URL is already tracked),
    or None when the filtered id doesn't exist. The filtered row is kept
    with restored=TRUE as an audit trail.
    """
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM filtered_jobs WHERE id = %s", (fid,))
            f = cur.fetchone()
            if not f:
                return None
            f = dict(f)
            url = (f.get("url") or "").lower().rstrip("/")
            try:
                import score as score_mod
                f_score, f_reason = score_mod.score_job(
                    f.get("title") or "", f.get("company") or "",
                    f.get("description") or "")
            except Exception:
                f_score, f_reason = 0, ""
            cur.execute("SELECT * FROM jobs WHERE url = %s", (url,))
            job = cur.fetchone()
            if job is None:
                cur.execute(
                    """
                    INSERT INTO jobs (source, title, company, url, location, date_posted, status,
                                      score, score_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, 'NEW',
                            COALESCE(%s, 0), COALESCE(%s, ''))
                    ON CONFLICT (url) DO NOTHING
                    RETURNING *
                    """,
                    (f.get("source") or "", f.get("title") or "",
                     f.get("company") or "", url, f.get("location"),
                     f.get("date_posted"),
                     f.get("score", f_score), f.get("score_reason", f_reason)),
                )
                job = cur.fetchone()
                if job is None:  # lost a race with a concurrent insert; re-read
                    cur.execute("SELECT * FROM jobs WHERE url = %s", (url,))
                    job = cur.fetchone()
            cur.execute("UPDATE filtered_jobs SET restored = TRUE WHERE id = %s",
                        (fid,))
            conn.commit()
            return dict(job) if job else None
    finally:
        if own:
            conn.close()


def delete_filtered_job(fid: int, conn=None) -> bool:
    """Permanently dismiss a filtered posting (it was filtered correctly)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM filtered_jobs WHERE id = %s RETURNING id",
                        (fid,))
            gone = cur.fetchone() is not None
            conn.commit()
            return gone
    finally:
        if own:
            conn.close()