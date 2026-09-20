"""Filtered-postings review endpoints for the dashboard's "Filtered" tab.

GET /filtered                  -- held-out postings, newest first
POST /filtered/{fid}/restore   -- move one back into `jobs` as NEW
DELETE /filtered/{fid}         -- dismiss one permanently (it was filtered right)
"""
from fastapi import APIRouter, HTTPException

from api import db
from api.models import FilteredJob

router = APIRouter()


def _ensure():
    try:
        db.ensure_filtered_schema()
    except Exception:
        pass


@router.get("", response_model=list[FilteredJob])
def list_filtered(include_restored: bool = False):
    _ensure()
    try:
        return db.list_filtered_jobs(include_restored=include_restored)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"filtered list failed: {e}")


@router.post("/{fid}/restore", response_model=FilteredJob)
def restore_filtered(fid: int):
    _ensure()
    try:
        job = db.restore_filtered_job(fid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"restore failed: {e}")
    if job is None:
        raise HTTPException(status_code=404, detail="filtered posting not found")
    # Return the filtered row (now marked restored) so the tab can update.
    rows = db.list_filtered_jobs(include_restored=True)
    for r in rows:
        if r["id"] == fid:
            return r
    raise HTTPException(status_code=500, detail="restore succeeded but row vanished")


@router.delete("/{fid}")
def delete_filtered(fid: int):
    _ensure()
    try:
        gone = db.delete_filtered_job(fid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"delete failed: {e}")
    if not gone:
        raise HTTPException(status_code=404, detail="filtered posting not found")
    return {"ok": True, "id": fid}
