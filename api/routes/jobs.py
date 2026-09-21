"""Job listing / updating endpoints."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException

from api import db
from api.models import (FollowUpUpdate, HistoryEntry, Job, JobStatusUpdate,
                        OfferUpdate, StageUpdate)

router = APIRouter()


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
