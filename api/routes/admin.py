"""Local-only admin endpoints: shutdown + update check.

POST /shutdown      -- stop the packaged stack (localhost only, else 403).
GET  /updates/check -- latest GitHub release tag vs current version.
"""
import os
import pathlib
import sys
import urllib.request
import json
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

REPO = os.environ.get("JOBSCRAPER_REPO", "needlehmbl/job-scraper")

VERSION_FILENAME = "VERSION"


def _read_version_file(path) -> str:
    """The tag in a baked VERSION file, or "" if it is missing or not a tag."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text if text.startswith("v") else ""


def resolve_version(env_value=None, frozen_dir=None, start=None) -> str:
    """Which release this install is, as a "vX.Y.Z" tag or "dev".

    CI cannot hand a version to a packaged build through the environment: the
    Docker container only gets what docker-compose.yml passes in, and a
    PyInstaller bundle has no environment to pass. So each release bakes the
    tag into a VERSION file next to the app instead, and that is what a
    packaged build reads. A source checkout has no such file and reports "dev".

    The env var still wins, so a system service or a manual run can pin it, and
    a file that does not look like a tag is ignored -- a stale VERSION left in
    the tree by a local build must never make a checkout claim to be a release.
    """
    if env_value is None:
        env_value = os.environ.get("JOBSCRAPER_VERSION", "")
    if (env_value or "").strip():
        return env_value.strip()

    # Frozen: the spec ships VERSION as a data file, so it lands in _MEIPASS.
    if frozen_dir is None:
        frozen_dir = getattr(sys, "_MEIPASS", None)
    if frozen_dir:
        version = _read_version_file(pathlib.Path(frozen_dir) / VERSION_FILENAME)
        if version:
            return version

    if start is None:
        start = pathlib.Path(__file__).parent
    try:
        # The tarball keeps VERSION beside api/, and the Dockerfile's COPY . .
        # lands it in /app -- two levels up from this file. Walk to the root.
        here = pathlib.Path(start).resolve()
        for parent in [here, *here.parents]:
            version = _read_version_file(parent / VERSION_FILENAME)
            if version:
                return version
    except OSError:
        pass  # a relative or otherwise unresolvable path must not break the check
    return "dev"


VERSION = resolve_version()


def needs_update(current: str, latest) -> bool:
    """Whether the dashboard should offer an upgrade.

    A build with no release version baked in -- a source checkout reports "dev"
    -- has nothing meaningful to compare, so it must never be told to upgrade:
    "v0.2.3" != "dev" stays true forever, and the banner showed up on every
    run even when the checkout was already sitting on the newest tag.
    """
    if not latest or not (current or "").startswith("v"):
        return False
    return current != latest


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
        return {"update_available": needs_update(VERSION, latest), "latest": latest,
                "current": VERSION}
    except Exception as e:
        return {"update_available": False, "latest": None, "current": VERSION,
                "error": str(e)}
