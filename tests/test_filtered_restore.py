"""The restore response carries the jobs row it moved the posting into.

The dashboard's Filtered review does a compound action: restore the posting,
then mark it APPLIED. It cannot learn the new jobs id any other way, and the
undo of that action has to know the status the row had *before* the restore --
a row this call created is NEW (so un-restoring drops it), while a URL that was
already tracked keeps whatever status it had. Both travel in the response.
"""
import pytest

from api import db as db_module
from api.models import FilteredJob
from api.routes import filtered as filtered_routes


def _fake_db(monkeypatch, job):
    monkeypatch.setattr(db_module, "ensure_filtered_schema", lambda: None, raising=False)
    monkeypatch.setattr(db_module, "restore_filtered_job", lambda fid: job, raising=False)
    monkeypatch.setattr(
        db_module,
        "list_filtered_jobs",
        lambda include_restored=False: [
            {"id": 7, "title": "Backend Engineer", "company": "Acme", "url": "https://x/1"}
        ],
        raising=False,
    )


def test_restore_reports_the_new_jobs_row(monkeypatch):
    _fake_db(monkeypatch, {"id": 42, "status": "NEW", "url": "https://x/1"})

    row = filtered_routes.restore_filtered(7)

    assert row["job_id"] == 42
    assert row["job_status"] == "NEW"


def test_restore_reports_a_preexisting_status(monkeypatch):
    # The URL was already tracked, so the restore did not create anything and
    # the row keeps its old status -- the undo has to put that status back.
    _fake_db(monkeypatch, {"id": 9, "status": "REVIEWED", "url": "https://x/1"})

    row = filtered_routes.restore_filtered(7)

    assert (row["job_id"], row["job_status"]) == (9, "REVIEWED")


def test_response_model_carries_the_new_fields(monkeypatch):
    _fake_db(monkeypatch, {"id": 42, "status": "NEW", "url": "https://x/1"})

    model = FilteredJob(**filtered_routes.restore_filtered(7))

    assert (model.job_id, model.job_status) == (42, "NEW")


def test_restore_404s_when_the_posting_is_gone(monkeypatch):
    _fake_db(monkeypatch, None)

    with pytest.raises(Exception) as err:
        filtered_routes.restore_filtered(7)

    assert getattr(err.value, "status_code", None) == 404
