"""
FastAPI app for the job-scraper dashboard.

Local-only, single-user API over the same Postgres DB the scraper writes to.
CORS is limited to localhost dev origins -- no auth needed for this tool.
"""
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.routes import admin, apply, export, filtered, jobs, scrape, stats, tailor  # noqa: E402

app = FastAPI(title="Job Scraper API", version="1.0.0")

try:
    from api import db as _db

    _db.ensure_tracking_schema()
    _db.ensure_filtered_schema()
except Exception as e:  # API must stay up even if the DB is unreachable
    print(f"[api] WARNING: schema migration failed: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(apply.router, prefix="/jobs", tags=["apply"])
app.include_router(filtered.router, prefix="/filtered", tags=["filtered"])
app.include_router(stats.router, tags=["jobs"])
app.include_router(scrape.router, tags=["scrape"])
app.include_router(export.router, tags=["export"])
app.include_router(tailor.router, tags=["tailor"])
app.include_router(admin.router, tags=["admin"])

# Serve dashboard static files in packaged mode (frozen by PyInstaller)
if getattr(sys, "frozen", False):
    dashboard_dist = Path(sys.executable).resolve().parent / "dashboard" / "dist"
    if dashboard_dist.is_dir():
        app.mount("/", StaticFiles(directory=dashboard_dist, html=True), name="dashboard")
    else:
        print(f"[api] WARNING: dashboard/dist not found at {dashboard_dist}")


@app.get("/health")
def health():
    return {"status": "ok"}