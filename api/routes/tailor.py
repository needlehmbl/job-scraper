"""Resume library + per-posting tailoring endpoints.

GET    /resumes                     -- list uploaded resumes + default
POST   /resumes/upload              -- drag-drop a .docx/.pdf (parsed server-side)
POST   /resumes/default             -- {name} set the default resume
DELETE /resumes/{name}              -- remove a resume + its bank sidecar

POST   /jobs/{job_id}/tailor        -- {resume?, job_text} run the LLM
                                       tailoring + heuristic skill-gap;
                                       returns preview JSON (nothing saved)
POST   /jobs/{job_id}/tailor/download -- {resume?, tailored} render the
                                       approved preview to .docx and download it

Tailoring costs one LLM call per click (separate "tailor" daily budget in
feedback.py) and never touches the scrape pipeline or auto-applies.
The jobs table stores no posting descriptions, so the dashboard sends
`job_text` (pasted from the listing, prefilled with title/company).
"""
import sys
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import render_resume  # noqa: E402
import resumes  # noqa: E402
import tailor  # noqa: E402
from api import db  # noqa: E402

router = APIRouter()

MAX_JOB_TEXT = 12000


class DefaultRequest(BaseModel):
    name: str


class TailorRequest(BaseModel):
    resume: str = ""
    job_text: str = ""


class DownloadRequest(BaseModel):
    resume: str = ""
    tailored: dict = {}


def _get_job(job_id: int) -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, company, location, url FROM jobs WHERE id = %s",
            (job_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))


@router.get("/resumes")
def list_resumes():
    return resumes.list_library()


@router.post("/resumes/upload")
async def upload_resume(file: UploadFile = File(...)):
    data = await file.read()
    entry, err = resumes.save_upload(file.filename or "resume.docx", data)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return entry


@router.post("/resumes/default")
def set_default_resume(body: DefaultRequest):
    err = resumes.set_default(body.name)
    if err:
        raise HTTPException(status_code=404, detail=err)
    return {"ok": True, "default": body.name}


@router.delete("/resumes/{name}")
def delete_resume(name: str):
    err = resumes.delete(name)
    if err:
        raise HTTPException(status_code=404, detail=err)
    return {"ok": True}


@router.post("/jobs/{job_id}/tailor")
def tailor_for_job(job_id: int, body: TailorRequest):
    job = _get_job(job_id)
    job_text = (body.job_text or "").strip()[:MAX_JOB_TEXT]
    if len(job_text) < 50:
        raise HTTPException(
            status_code=400,
            detail="paste the posting description (at least a paragraph) -- "
                   "the tracker stores titles only, so the text has to come "
                   "from the listing page.",
        )
    bank, err = resumes.load_bank(body.resume)
    if err:
        raise HTTPException(status_code=400, detail=err)
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    tailored, provider, terr = tailor.tailor_resume(
        cfg, bank, job.get("title", ""), job.get("company", ""), job_text)
    if terr:
        raise HTTPException(status_code=502, detail=f"tailoring failed: {terr}")
    skills = tailor.suggest_skills(bank, job_text)
    return {"tailored": tailored, "provider": provider, **skills,
            "job": {"id": job["id"], "title": job.get("title", ""),
                    "company": job.get("company", "")}}


@router.post("/jobs/{job_id}/tailor/download")
def download_tailored(job_id: int, body: DownloadRequest):
    job = _get_job(job_id)
    if not body.tailored or not isinstance(body.tailored, dict):
        raise HTTPException(status_code=400, detail="nothing to render")
    bank, err = resumes.load_bank(body.resume)
    if err:
        raise HTTPException(status_code=400, detail=err)
    tailored = dict(body.tailored)
    tailored.setdefault("education", bank.get("education", []))
    contact = bank.get("contact", {})
    path = render_resume.render(contact, tailored, resumes.LIB_DIR + "/output",
                                job.get("title", ""), job.get("company", ""))
    filename = Path(path).name
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument."
                   "wordprocessingml.document",
        filename=filename,
    )
