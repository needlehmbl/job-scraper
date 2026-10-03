"""Unit tests for DELETE /jobs/{job_id} + POST /jobs/reinsert (Applied delete).

The route functions are plain callables, so they are exercised directly
with stubbed db helpers — no live Postgres, no HTTP client.
"""
import pytest
from fastapi import HTTPException

from api.models import JobReinsert
from api.routes import jobs as jobs_routes


def test_delete_returns_ok_when_row_gone(monkeypatch):
    monkeypatch.setattr(jobs_routes.db, "delete_job", lambda jid: True,
                        raising=False)
    out = jobs_routes.delete_job(2269)
    assert out == {"ok": True, "id": 2269}


def test_delete_404s_when_missing(monkeypatch):
    monkeypatch.setattr(jobs_routes.db, "delete_job", lambda jid: False,
                        raising=False)
    with pytest.raises(HTTPException) as ei:
        jobs_routes.delete_job(999999)
    assert ei.value.status_code == 404


def test_delete_500s_on_db_error(monkeypatch):
    def boom(jid):
        raise RuntimeError("db down")
    monkeypatch.setattr(jobs_routes.db, "delete_job", boom, raising=False)
    with pytest.raises(HTTPException) as ei:
        jobs_routes.delete_job(1)
    assert ei.value.status_code == 500


def test_reinsert_returns_row(monkeypatch):
    row = {"id": 2270, "title": "Junior Dev", "company": "Acme",
           "url": "https://x/1", "status": "APPLIED", "stage": "APPLIED"}
    monkeypatch.setattr(jobs_routes.db, "reinsert_job", lambda snap: row,
                        raising=False)
    out = jobs_routes.reinsert_job(JobReinsert(
        title="Junior Dev", company="Acme", url="https://x/1",
        status="APPLIED", stage="APPLIED"))
    assert out["id"] == 2270


def test_reinsert_400s_on_empty_snapshot(monkeypatch):
    monkeypatch.setattr(jobs_routes.db, "reinsert_job", lambda snap: None,
                        raising=False)
    with pytest.raises(HTTPException) as ei:
        jobs_routes.reinsert_job(JobReinsert())
    assert ei.value.status_code == 400


def test_reinsert_model_defaults():
    m = JobReinsert()
    assert m.status == "NEW"
    assert m.stage is None
    assert m.salary_interval == "unknown"
