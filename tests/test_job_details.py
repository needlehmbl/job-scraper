"""Unit tests for PATCH /jobs/{job_id}/details (manual row correction).

The route functions are plain callables, so they are exercised directly
with a stubbed db connection — no live Postgres, no HTTP client.
"""
import pytest
from fastapi import HTTPException

from api.models import JobDetailsUpdate
from api.routes import jobs as jobs_routes


class FakeCursor:
    def __init__(self, fetch_results, capture):
        self._results = list(fetch_results)
        self.capture = capture
        self.description = None

    def execute(self, sql, params=None):
        self.capture.append((sql, params))
        if sql.strip().upper().startswith("UPDATE"):
            # RETURNING * row: (id, source, title, company, url, ...)
            self.description = [type("D", (), {"name": n})()
                                for n in ("id", "title", "company",
                                          "location")]
            self._pending_row = (7, "Junior Dev", "Acme", "Makati")

    def fetchone(self):
        if hasattr(self, "_pending_row"):
            row = self._pending_row
            del self._pending_row
            return row
        return self._results.pop(0) if self._results else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, fetch_results, capture):
        self._fetch_results = fetch_results
        self.capture = capture
        self.committed = False

    def cursor(self):
        return FakeCursor(self._fetch_results, self.capture)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run_update(monkeypatch, fetch_results, **fields):
    capture = []
    conn = FakeConn(fetch_results, capture)
    monkeypatch.setattr(jobs_routes.db, "connect", lambda: conn)
    out = jobs_routes.update_details(7, JobDetailsUpdate(**fields))
    return out, capture, conn


def test_partial_update_only_sends_given_fields(monkeypatch):
    out, capture, conn = run_update(monkeypatch, [(7,)], company="Acme")
    assert out["company"] == "Acme"
    assert conn.committed
    update_sql = [s for s, _ in capture if s.strip().upper().startswith("UPDATE")]
    assert len(update_sql) == 1
    assert "company" in update_sql[0]
    assert "title" not in update_sql[0]


def test_empty_body_rejected(monkeypatch):
    capture = []
    monkeypatch.setattr(jobs_routes.db, "connect",
                        lambda: FakeConn([], capture))
    with pytest.raises(HTTPException) as ei:
        jobs_routes.update_details(7, JobDetailsUpdate())
    assert ei.value.status_code == 400


def test_blank_title_rejected(monkeypatch):
    capture = []
    monkeypatch.setattr(jobs_routes.db, "connect",
                        lambda: FakeConn([], capture))
    with pytest.raises(HTTPException) as ei:
        jobs_routes.update_details(7, JobDetailsUpdate(title="   "))
    assert ei.value.status_code == 400


def test_missing_row_404(monkeypatch):
    capture = []
    monkeypatch.setattr(jobs_routes.db, "connect",
                        lambda: FakeConn([None], capture))
    with pytest.raises(HTTPException) as ei:
        jobs_routes.update_details(999, JobDetailsUpdate(title="Junior Dev"))
    assert ei.value.status_code == 404
