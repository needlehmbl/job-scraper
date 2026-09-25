"""Scrape trigger endpoints for the dashboard's "Scrape new jobs" button.

POST /scrape         -- start a scrape in a background thread (409 if busy)
GET  /scrape/status  -- poll the current/last scrape state

Runs the exact same `pipeline.run_scrape()` code as `venv/bin/python
main.py`, so dashboard-triggered scrapes dedupe, write `scrape_runs` +
`runs.log`, and feed the notifier exactly like CLI runs.
"""
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import run_scrape  # noqa: E402

try:
    import scraper as scraper_mod  # noqa: E402  (live progress snapshots)
except Exception:  # pragma: no cover - scraper always importable in practice
    scraper_mod = None

router = APIRouter()

_lock = threading.Lock()
_state: dict = {
    "state": "idle",  # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "added": None,
    "scraped": None,
    "summary": None,
    "error": None,
    "filtered": None,
    "filter_reasons": None,
    "filtered_saved": None,
    "source_stats": None,
    "warnings": None,
    "errors": None,
    "progress": None,
}


def _worker():
    global _state
    try:
        result = run_scrape()
        final_progress = None
        if scraper_mod is not None:
            try:
                final_progress = scraper_mod.get_progress()
            except Exception:
                final_progress = None
        with _lock:
            _state.update({
                "state": "done",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "added": result.get("added"),
                "scraped": result.get("scraped"),
                "summary": result.get("summary"),
                "error": None,
                "filtered": result.get("filtered"),
                "filter_reasons": result.get("filter_reasons"),
                "filtered_saved": result.get("filtered_saved"),
                "source_stats": result.get("source_stats"),
                "warnings": result.get("warnings"),
                "errors": result.get("errors"),
                "progress": final_progress,
            })
    except Exception as e:  # never leave the button stuck on "running"
        err_progress = None
        if scraper_mod is not None:
            try:
                err_progress = scraper_mod.get_progress()
            except Exception:
                err_progress = None
        with _lock:
            _state.update({
                "state": "error",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
                "progress": err_progress,
            })


@router.post("/scrape", status_code=202)
def start_scrape():
    with _lock:
        if _state["state"] == "running":
            raise HTTPException(status_code=409, detail="scrape already running")
        _state.update({
            "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "added": None,
            "scraped": None,
            "summary": None,
            "error": None,
            "filtered": None,
            "filter_reasons": None,
            "filtered_saved": None,
            "source_stats": None,
            "warnings": None,
            "errors": None,
            "progress": None,
        })
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"ok": True, "state": "running", "started_at": _state["started_at"]}


@router.get("/scrape/status")
def scrape_status():
    with _lock:
        snap = dict(_state)
    # While a scrape is running, overlay the live progress snapshot so the
    # dashboard can show stage / source / search-term without extra polling.
    if snap.get("state") == "running" and scraper_mod is not None:
        try:
            snap["progress"] = scraper_mod.get_progress()
        except Exception:
            pass
    return snap
