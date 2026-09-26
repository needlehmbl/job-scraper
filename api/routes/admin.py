"""Local-only admin endpoints: shutdown + update check.

POST /shutdown      -- stop the packaged stack (localhost only, else 403).
GET  /updates/check -- latest GitHub release tag vs current version.
"""
import os
import urllib.request
import json
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

VERSION = os.environ.get("JOBSCRAPER_VERSION", "dev")
REPO = os.environ.get("JOBSCRAPER_REPO", "needlehmbl/job-scraper")


def _local_only(request: Request):
    host = (request.client.host if request.client else "")
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="shutdown is local-only")


@router.post("/shutdown")
def shutdown(request: Request):
    _local_only(request)
    # Packaged launchers (compose / exe) supervise this process: exiting here
    # stops the stack. Dev systemd units restart on exit -- use stop.sh there.
    os._exit(0)
    return {"ok": True}  # unreachable; keeps type-checkers quiet


@router.get("/updates/check")
def updates_check():
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"User-Agent": "jobscraper-api"})
        with urllib.request.urlopen(req, timeout=10) as r:
            latest = json.load(r)["tag_name"]
        return {"update_available": latest != VERSION, "latest": latest,
                "current": VERSION}
    except Exception as e:
        return {"update_available": False, "latest": None, "current": VERSION,
                "error": str(e)}
