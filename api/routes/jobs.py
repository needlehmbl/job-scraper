"""Job listing / updating endpoints."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException

from api import db
from api.models import FollowUpUpdate, Job, JobStatusUpdate

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
        cur.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        old_status = row[0]
        if body.status == "APPLIED":
            cur.execute(
                """
                UPDATE jobs SET status = %s, applied_at = %s, status_updated_at = %s
                WHERE id = %s RETURNING *
                """,
                (body.status, datetime.now(timezone.utc),
                 datetime.now(timezone.utc), job_id),
            )
        else:
            cur.execute(
                """
                UPDATE jobs SET status = %s, applied_at = NULL, status_updated_at = %s
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
