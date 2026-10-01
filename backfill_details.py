"""Backfill missing descriptions + salaries on previously scraped postings.

Rows scraped Sep 16-29 predate description fetching and salary capture,
and upsert_job() is insert-or-ignore, so re-scrapes never healed them.
This script revisits each stored posting URL (oldest first), re-fetches
via manual_fetch (plain HTTP + headless-Chromium fallback), and fills
only the columns that are still empty.

Resume-safe: every run re-queries what's still missing. Throttled with
--sleep between rows; LinkedIn/Glassdoor each render a browser page, so
a full pass takes a while -- run bounded batches with --limit.

Usage:
  python3 backfill_details.py --limit 50 --dry-run
  python3 backfill_details.py --limit 200 --source linkedin
  python3 backfill_details.py --limit 500 --skip-sites linkedin,glassdoor
"""
import argparse
import sys
import time

import db
import manual_fetch as mf

SALARY_COLS = ("salary_raw", "salary_currency", "salary_min", "salary_max",
               "salary_interval", "salary_monthly_min", "salary_monthly_max",
               "salary_display")


def _norm_salary(raw: str, site: str) -> dict:
    try:
        from salary import normalize_job_salary
        return normalize_job_salary({"salary_raw": raw or "",
                                     "site": site or ""})
    except Exception:
        return {"salary_raw": "", "salary_currency": "", "salary_min": None,
                "salary_max": None, "salary_interval": "unknown",
                "salary_monthly_min": None, "salary_monthly_max": None,
                "salary_display": ""}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=100,
                    help="max rows to visit this run (default 100)")
    ap.add_argument("--source", default="",
                    help="only this source site (e.g. linkedin)")
    ap.add_argument("--skip-sites", default="",
                    help="comma-separated sites to skip (e.g. linkedin,glassdoor)")
    ap.add_argument("--sleep", type=float, default=2.0,
                    help="seconds between rows (default 2.0)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, but don't UPDATE")
    args = ap.parse_args()

    skip = {s.strip().lower() for s in args.skip_sites.split(",") if s.strip()}
    clauses, params = ["(description IS NULL OR description = '')",
                       "(salary_display IS NULL OR salary_display = '')"], []
    where = "((" + ") OR (".join(clauses) + "))"
    if args.source:
        where += " AND source = %s"
        params.append(args.source.strip().lower())
    if skip:
        where += " AND source NOT IN (" + ",".join(["%s"] * len(skip)) + ")"
        params.extend(sorted(skip))

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id, url, source, description, salary_display FROM jobs "
            f"WHERE {where} ORDER BY id LIMIT %s",
            (*params, args.limit))
        rows = cur.fetchall()
    print(f"[backfill] {len(rows)} rows to visit"
          f"{' (DRY RUN)' if args.dry_run else ''}")
    if not rows:
        return 0

    healed_desc = healed_sal = unchanged = errors = 0
    for i, (jid, url, site, desc, disp) in enumerate(rows, 1):
        need_desc = not (desc or "").strip()
        need_sal = not (disp or "").strip()
        try:
            fetched = mf.fetch_job_from_url(url)
            new_desc = (fetched.get("description") or "").strip()
            sal = _norm_salary(fetched.get("salary_raw") or "", site)
            sets, vals = [], []
            if need_desc and new_desc:
                sets.append("description = %s")
                vals.append(new_desc[:8000])
            if need_sal and (sal.get("salary_display") or "").strip():
                for col in SALARY_COLS:
                    sets.append(f"{col} = %s")
                    vals.append(sal.get(col))
            if sets and not args.dry_run:
                with db.connect() as conn2, conn2.cursor() as cur2:
                    cur2.execute(
                        f"UPDATE jobs SET {', '.join(sets)} WHERE id = %s",
                        (*vals, jid))
                    conn2.commit()
            if sets:
                if need_desc and new_desc:
                    healed_desc += 1
                if need_sal and (sal.get("salary_display") or "").strip():
                    healed_sal += 1
                tag = "+desc" if (need_desc and new_desc) else ""
                tag += "+sal" if (need_sal and sal.get("salary_display")) else ""
                print(f"  [{i}/{len(rows)}] id={jid} {site} healed ({tag})")
            else:
                unchanged += 1
                if (fetched.get("fetch_limited") and not new_desc
                        and not sal.get("salary_display")):
                    print(f"  [{i}/{len(rows)}] id={jid} {site} "
                          f"unfetched (bot-wall?)")
        except Exception as e:
            errors += 1
            print(f"  [{i}/{len(rows)}] id={jid} {site} ERROR: "
                  f"{type(e).__name__}: {str(e)[:160]}")
        if i < len(rows):
            time.sleep(max(0.0, args.sleep))
    print(f"[backfill] done: {healed_desc} +desc, {healed_sal} +salary, "
          f"{unchanged} unchanged, {errors} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
