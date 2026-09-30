"""Job listing / updating endpoints."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException

from api import db
from api.models import (FollowUpUpdate, HistoryEntry, Job, JobDetailsUpdate,
                         JobStatusUpdate, ManualAddRequest, OfferUpdate,
                         StageUpdate)

router = APIRouter()


def _move_to_applied(cur, job_id: int, old_status, old_stage):
    """Shared flip-to-APPLIED used by PATCH and manual-add dedupe moves."""
    cur.execute(
        """
        UPDATE jobs SET status = 'APPLIED', applied_at = %s,
                        status_updated_at = %s,
                        stage = COALESCE(stage, 'APPLIED')
        WHERE id = %s RETURNING *
        """,
        (datetime.now(timezone.utc), datetime.now(timezone.utc), job_id),
    )
    row = cur.fetchone()
    cols = [d.name for d in cur.description]
    result = dict(zip(cols, row))
    if old_status != "APPLIED":
        cur.execute(
            """
            INSERT INTO job_status_history (job_id, old_status, new_status)
            VALUES (%s, %s, 'APPLIED')
            """,
            (job_id, old_status),
        )
    if (old_stage or None) != result.get("stage"):
        cur.execute(
            """
            INSERT INTO job_stage_history (job_id, old_stage, new_stage)
            VALUES (%s, %s, %s)
            """,
            (job_id, old_stage, result.get("stage")),
        )
    return result


@router.post("/manual-add", response_model=dict)
def manual_add(body: ManualAddRequest):
    """Track an externally-found posting URL as APPLIED.

    Dedupes by normalized URL then title+company fingerprint
    (dedupe.py): existing rows are moved to APPLIED, new rows are
    inserted directly as APPLIED with stage APPLIED.
    """
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import manual_fetch as mf

    raw = (body.url or "").strip()
    if not (raw.startswith("http://") or raw.startswith("https://") or "." in raw):
        raise HTTPException(status_code=400, detail="invalid URL")
    url = mf.normalize_url(raw)
    if not url:
        raise HTTPException(status_code=400, detail="invalid URL")
    override = {"title": body.title, "company": body.company,
                "location": body.location}

    # Network fetch OUTSIDE any DB transaction: holding a transaction
    # open across a 15s+ browser render once wedged the whole DB behind
    # one stuck manual-add (idle-in-transaction lock pile-up).
    fetched = mf.fetch_job_from_url(url)
    fields = mf.resolve_manual_fields(fetched, override)
    title = fields["title"]
    company = fields["company"]
    site = fetched.get("site") or "manual"
    try:
        import score as score_mod

        f_score, f_reason = score_mod.score_job(
            title, company, fetched.get("description") or "")
    except Exception:
        f_score, f_reason = 0, ""

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, status, stage FROM jobs WHERE url = %s", (url,))
        hit = cur.fetchone()

        def backfill_placeholders(job_id: int, fields: dict):
            """Upgrade placeholder/empty columns on a moved row.

            Re-pasting an Untitled row with typed (or newly fetched)
            values fixes it in place instead of preserving the placeholder.
            Only placeholder title / empty company/location/description
            are touched; real stored values are never overwritten.
            """
            cur.execute(
                "SELECT title, company, location, description FROM jobs "
                "WHERE id = %s", (job_id,))
            row = cur.fetchone()
            if not row:
                return
            old_title, old_company, old_loc, old_desc = row
            sets, vals = [], []
            if mf.is_placeholder_title(old_title) and not mf.is_placeholder_title(
                    fields.get("title", "")):
                sets.append("title = %s")
                vals.append(fields["title"][:500])
            if not (old_company or "").strip() and (fields.get("company") or "").strip():
                sets.append("company = %s")
                vals.append(fields["company"][:300])
            if not (old_loc or "").strip() and (fields.get("location") or "").strip():
                sets.append("location = %s")
                vals.append(fields["location"][:300])
            if not (old_desc or "").strip() and (fields.get("description") or "").strip():
                sets.append("description = %s")
                vals.append(fields["description"][:8000])
            if sets:
                cur.execute(
                    f"UPDATE jobs SET {', '.join(sets)} WHERE id = %s",
                    (*vals, job_id))

        if hit:
            jid, old_status, old_stage = hit[0], hit[1], hit[2]
            result = _move_to_applied(cur, jid, old_status, old_stage)
            backfill_placeholders(jid, {**fields,
                                        "description": fetched.get("description")})
            cur.execute("SELECT * FROM jobs WHERE id = %s", (jid,))
            row = cur.fetchone()
            result = dict(zip([d.name for d in cur.description], row))
            conn.commit()
            return {"moved": True, "job": result}

        # Fingerprint fallback: same listing, different URL. Placeholder
        # titles ("Untitled (manual)") are skipped: two unfetched rows that
        # happen to share a company are not the same listing.
        try:
            import dedupe as dedupe_mod

            fp = dedupe_mod.fingerprint(title, company)
            if fp and company and not mf.is_placeholder_title(title):
                cur.execute("SELECT id, title, company, status, stage FROM jobs")
                for cid, ctitle, ccompany, cstatus, cstage in cur.fetchall():
                    if dedupe_mod.fingerprint(ctitle, ccompany) == fp:
                        result = _move_to_applied(cur, cid, cstatus, cstage)
                        backfill_placeholders(cid, {**fields, "description": fetched.get("description")})
                        cur.execute("SELECT * FROM jobs WHERE id = %s", (cid,))
                        frow = cur.fetchone()
                        result = dict(zip([d.name for d in cur.description], frow))
                        conn.commit()
                        return {"moved": True, "job": result}
        except Exception:
            pass

        site = fetched.get("site") or "manual"
        now = datetime.now(timezone.utc)
        cur.execute(
            """
            INSERT INTO jobs (source, title, company, url, location,
                              date_posted, status, applied_at,
                              status_updated_at, stage, score, score_reason,
                              description)
            VALUES (%s, %s, %s, %s, %s, NULL, 'APPLIED', %s, %s,
                    'APPLIED', %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
            RETURNING *
            """,
            (site, title, company, url, fields.get("location"),
             now, now, f_score or 0, f_reason or "",
             fetched.get("description")),
        )
        row = cur.fetchone()
        if row is None:  # lost a race with a concurrent insert; move it
            cur.execute("SELECT id, status, stage FROM jobs WHERE url = %s", (url,))
            hit = cur.fetchone()
            if hit:
                result = _move_to_applied(cur, hit[0], hit[1], hit[2])
                conn.commit()
                return {"moved": True, "job": result}
            raise HTTPException(status_code=500, detail="insert failed")
        cols = [d.name for d in cur.description]
        result = dict(zip(cols, row))
        cur.execute(
            """
            INSERT INTO job_status_history (job_id, old_status, new_status)
            VALUES (%s, NULL, 'APPLIED')
            """,
            (result["id"],),
        )
        cur.execute(
            """
            INSERT INTO job_stage_history (job_id, old_stage, new_stage)
            VALUES (%s, NULL, 'APPLIED')
            """,
            (result["id"],),
        )
        conn.commit()
        note = ""
        if mf.is_placeholder_title(fields["title"]):
            note = fetched.get("fetch_note") or (
                "details could not be fetched — saved with URL only")
        return {"moved": False, "job": result, "note": note}


@router.get("", response_model=list[Job])
def list_jobs(
    status: str | None = None,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
):
    sql = "SELECT * FROM jobs WHERE 1=1"
    params: list = []

    if status:
        sql += " AND status = %s"
        params.append(status)
    if source:
        sql += " AND source = %s"
        params.append(source)
    if date_from:
        sql += " AND date_posted >= %s"
        params.append(date_from)
    if date_to:
        sql += " AND date_posted <= %s"
        params.append(date_to)
    if search:
        sql += " AND (title ILIKE %s OR company ILIKE %s)"
        like = f"%{search}%"
        params.extend([like, like])

    # Triage sort: ?sort=score (default desc) | scraped | posted.
    # Default is score-first so the most relevant rows surface on load.
    sort = (sort or "score").lower()
    direction = (direction or "").lower()
    if sort == "scraped":
        order = "scraped_at DESC" if direction != "asc" else "scraped_at ASC"
    elif sort == "posted":
        order = ("date_posted DESC NULLS LAST, scraped_at DESC"
                 if direction != "asc" else
                 "date_posted ASC NULLS LAST, scraped_at ASC")
    else:
        order = "score DESC, scraped_at DESC" if direction != "asc" else \
            "score ASC, scraped_at ASC"
    sql += f" ORDER BY {order}"

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


@router.patch("/{job_id}", response_model=Job)
def update_status(job_id: int, body: JobStatusUpdate):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT status, stage FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        old_status, old_stage = row[0], row[1]
        if body.status == "APPLIED":
            cur.execute(
                """
                UPDATE jobs SET status = %s, applied_at = %s, status_updated_at = %s,
                                stage = COALESCE(stage, 'APPLIED')
                WHERE id = %s RETURNING *
                """,
                (body.status, datetime.now(timezone.utc),
                 datetime.now(timezone.utc), job_id),
            )
        else:
            # Leaving the pipeline: a non-APPLIED row carries no stage.
            cur.execute(
                """
                UPDATE jobs SET status = %s, applied_at = NULL, status_updated_at = %s,
                                stage = NULL
                WHERE id = %s RETURNING *
                """,
                (body.status, datetime.now(timezone.utc), job_id),
            )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        cols = [d.name for d in cur.description]
        result = dict(zip(cols, row))
        if old_status != body.status:
            cur.execute(
                """
                INSERT INTO job_status_history (job_id, old_status, new_status)
                VALUES (%s, %s, %s)
                """,
                (job_id, old_status, body.status),
            )
        new_stage = result.get("stage")
        if old_stage != new_stage:
            cur.execute(
                """
                INSERT INTO job_stage_history (job_id, old_stage, new_stage)
                VALUES (%s, %s, %s)
                """,
                (job_id, old_stage, new_stage),
            )
        conn.commit()
        return result


@router.patch("/{job_id}/stage", response_model=Job)
def update_stage(job_id: int, body: StageUpdate):
    """Advance a pipeline row's funnel stage.

    Keeps the invariant stage-set ⟺ APPLIED: setting a stage on a
    non-APPLIED row flips it to APPLIED (recorded in both histories).
    """
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT status, stage FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        old_status, old_stage = row[0], row[1]
        if old_status == "APPLIED":
            cur.execute(
                "UPDATE jobs SET stage = %s WHERE id = %s RETURNING *",
                (body.stage, job_id),
            )
        else:
            cur.execute(
                """
                UPDATE jobs SET status = 'APPLIED', applied_at = %s,
                                status_updated_at = %s, stage = %s
                WHERE id = %s RETURNING *
                """,
                (datetime.now(timezone.utc), datetime.now(timezone.utc),
                 body.stage, job_id),
            )
        row = cur.fetchone()
        cols = [d.name for d in cur.description]
        result = dict(zip(cols, row))
        if old_status != "APPLIED":
            cur.execute(
                """
                INSERT INTO job_status_history (job_id, old_status, new_status)
                VALUES (%s, %s, 'APPLIED')
                """,
                (job_id, old_status),
            )
        if old_stage != body.stage:
            cur.execute(
                """
                INSERT INTO job_stage_history (job_id, old_stage, new_stage)
                VALUES (%s, %s, %s)
                """,
                (job_id, old_stage, body.stage),
            )
        conn.commit()
        return result


@router.patch("/{job_id}/followup", response_model=Job)
def update_followup(job_id: int, body: FollowUpUpdate):
    """Set/clear the "ping if no response by" reminder date."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM jobs WHERE id = %s", (job_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="job not found")
        cur.execute(
            "UPDATE jobs SET follow_up_at = %s WHERE id = %s RETURNING *",
            (body.follow_up_at, job_id),
        )
        row = cur.fetchone()
        cols = [d.name for d in cur.description]
        result = dict(zip(cols, row))
        conn.commit()
        return result


@router.patch("/{job_id}/details", response_model=Job)
def update_details(job_id: int, body: JobDetailsUpdate):
    """Manually correct a tracked row's title / company / location.

    Only the fields present in the body are changed. Title/company that
    arrive blank-after-strip are rejected: empty identity breaks the
    fingerprint dedupe. Location accepts empty string to clear it.
    """
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="nothing to update")
    cleaned: dict = {}
    for key, val in updates.items():
        s = val.strip() if isinstance(val, str) else val
        if key in ("title", "company") and not s:
            raise HTTPException(
                status_code=400, detail=f"{key} must not be blank")
        cleaned[key] = s[:500] if key == "title" else (
            s[:300] if isinstance(s, str) else s)
    if "location" in cleaned and cleaned["location"] == "":
        cleaned["location"] = None
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, title, company FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        # Duplicate guard: editing title/company onto another tracked row's
        # fingerprint would silently create a cross-link duplicate. Reject
        # with the conflicting row's id so the user can merge deliberately.
        try:
            import dedupe as dedupe_mod

            new_title = cleaned.get("title", row[1])
            new_company = cleaned.get("company", row[2])
            fp = dedupe_mod.fingerprint(new_title, new_company)
            if fp:
                cur.execute(
                    "SELECT id, title, company FROM jobs WHERE id <> %s",
                    (job_id,))
                for cid, ctitle, ccompany in cur.fetchall():
                    if dedupe_mod.fingerprint(ctitle, ccompany) == fp:
                        raise HTTPException(
                            status_code=409,
                            detail=(f"title+company matches existing job #{cid} "
                                    f"({ctitle} @ {ccompany}) — edit would "
                                    f"create a duplicate"))
        except HTTPException:
            raise
        except Exception:
            pass
        set_clause = ", ".join(f"{k} = %s" for k in cleaned)
        cur.execute(
            f"UPDATE jobs SET {set_clause} WHERE id = %s RETURNING *",
            (*cleaned.values(), job_id),
        )
        row = cur.fetchone()
        cols = [d.name for d in cur.description]
        result = dict(zip(cols, row))
        conn.commit()
        return result


@router.patch("/{job_id}/offer", response_model=Job)
def update_offer(job_id: int, body: OfferUpdate):
    """Save offer details (salary / benefits / pros / cons). Only the
    fields present in the body are changed."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="nothing to update")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM jobs WHERE id = %s", (job_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="job not found")
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        cur.execute(
            f"UPDATE jobs SET {set_clause} WHERE id = %s RETURNING *",
            (*updates.values(), job_id),
        )
        row = cur.fetchone()
        cols = [d.name for d in cur.description]
        result = dict(zip(cols, row))
        conn.commit()
        return result


@router.get("/{job_id}/history", response_model=list[HistoryEntry])
def job_history(job_id: int):
    """Status + stage timeline for one posting — shows which funnel stage
    a rejection came after."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM jobs WHERE id = %s", (job_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="job not found")
        cur.execute(
            """
            SELECT old_status, new_status, changed_at
            FROM job_status_history
            WHERE job_id = %s
            ORDER BY changed_at ASC
            """,
            (job_id,),
        )
        entries = [
            {"kind": "status", "old_status": r[0], "new_status": r[1],
             "old_stage": None, "new_stage": None, "changed_at": r[2]}
            for r in cur.fetchall()
        ]
        try:
            cur.execute(
                """
                SELECT old_stage, new_stage, changed_at
                FROM job_stage_history
                WHERE job_id = %s
                ORDER BY changed_at ASC
                """,
                (job_id,),
            )
            entries.extend(
                {"kind": "stage", "old_status": None, "new_status": None,
                 "old_stage": r[0], "new_stage": r[1], "changed_at": r[2]}
                for r in cur.fetchall()
            )
        except Exception:
            conn.rollback()  # stage table missing on very old DBs; status-only
        entries.sort(key=lambda e: (e["changed_at"] or "", e["kind"]))
        return entries
