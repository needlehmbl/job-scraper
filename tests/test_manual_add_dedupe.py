"""Route-level dedupe tests: manual-add fingerprint move + edit collision.

The route functions are plain callables, exercised directly with a stubbed
db connection and a stubbed network fetch — no live Postgres, no HTTP.
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.models import JobDetailsUpdate, ManualAddRequest
from api.routes import jobs as jobs_routes


class FakeCursor:
    """Result-set queue: each execute pops (columns, rows); UPDATE returns a
    fixed RETURNING row."""

    def __init__(self, result_sets, capture):
        self._result_sets = list(result_sets)
        self.capture = capture
        self.description = None
        self._current = []

    def execute(self, sql, params=None):
        self.capture.append((sql, params))
        kind = sql.strip().upper()
        if kind.startswith("UPDATE"):
            self.description = [type("D", (), {"name": n})()
                                for n in ("id", "title", "company",
                                          "location")]
            self._current = [(7, "Junior Dev", "Acme", "Makati")]
        elif kind.startswith("SELECT") or "RETURNING" in kind:
            if self._result_sets:
                cols, rows = self._result_sets.pop(0)
                self.description = [type("D", (), {"name": n})()
                                    for n in cols]
                self._current = list(rows)
            else:
                self.description = []
                self._current = []
        # plain INSERT (history rows): no result set consumed

    def fetchone(self):
        return self._current.pop(0) if self._current else None

    def fetchall(self):
        rows, self._current = self._current, []
        return rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, result_sets, capture):
        self._result_sets = result_sets
        self.capture = capture
        self.committed = False

    def cursor(self):
        return FakeCursor(self._result_sets, self.capture)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run_manual_add(monkeypatch, result_sets, payload, fetched):
    capture = []
    conn = FakeConn(result_sets, capture)
    monkeypatch.setattr(jobs_routes.db, "connect", lambda: conn)
    import manual_fetch as mf
    monkeypatch.setattr(mf, "fetch_job_from_url", lambda url, timeout=15: fetched)
    import score as score_mod
    monkeypatch.setattr(score_mod, "score_job",
                        lambda t, c, d: (0, ""))
    out = jobs_routes.manual_add(ManualAddRequest(**payload))
    return out, capture, conn


def test_manual_add_typed_fields_dedupe_move(monkeypatch):
    # Fetch failed; user typed title+company that fingerprint-matches an
    # existing row -> moved, not duplicated.
    fetched = {"title": "", "company": "", "location": None,
               "description": None, "site": "linkedin", "fetch_limited": True}
    result_sets = [
        (("id", "status", "stage"), []),                                  # URL miss
        (("id", "title", "company", "status", "stage"),
         [(9, "Junior Dev", "Acme", "SAVED", None)]),                    # fp scan
        (("title", "company", "location", "description"),
         [("Junior Dev", "Acme", None, None)]),                          # backfill
        (("id", "title", "company", "location"),
         [(9, "Junior Dev", "Acme", "Makati")]),                         # final row
    ]
    out, capture, conn = run_manual_add(
        monkeypatch, result_sets,
        {"url": "https://www.linkedin.com/jobs/view/999",
         "title": "Junior Dev", "company": "Acme"}, fetched)
    assert out["moved"] is True
    assert out["job"]["id"] == 9
    assert conn.committed


def test_manual_add_placeholder_title_not_merged(monkeypatch):
    # Fetch failed and nothing typed -> "Untitled (manual)". Even with a
    # company match, placeholder rows must not be auto-merged (the fp scan
    # is skipped entirely, so no fingerprint result set is consumed).
    fetched = {"title": "", "company": "", "location": None,
               "description": None, "site": "linkedin", "fetch_limited": True}
    result_sets = [
        (("id", "status", "stage"), []),                                  # URL miss
        (("id", "title", "company", "location"),
         [(10, "Untitled (manual)", "Acme", None)]),                      # INSERT
    ]
    out, capture, conn = run_manual_add(
        monkeypatch, result_sets,
        {"url": "https://www.linkedin.com/jobs/view/999"}, fetched)
    assert out["moved"] is False
    assert out["job"]["id"] == 10
    assert conn.committed


def test_manual_add_fresh_row_inserted(monkeypatch):
    # Typed title+company with no fingerprint match -> inserted as APPLIED.
    fetched = {"title": "", "company": "", "location": None,
               "description": None, "site": "jobstreet", "fetch_limited": True}
    result_sets = [
        (("id", "status", "stage"), []),                                  # URL miss
        (("id", "title", "company", "status", "stage"),
         [(9, "Other Title", "Other Co", "SAVED", None)]),                # fp scan
        (("id", "title", "company", "location"),
         [(10, "Brand New Role", "New Co", None)]),                       # INSERT
    ]
    out, capture, conn = run_manual_add(
        monkeypatch, result_sets,
        {"url": "https://ph.jobstreet.com/job/1",
         "title": "Brand New Role", "company": "New Co"}, fetched)
    assert out["moved"] is False
    assert out["job"]["id"] == 10
    assert conn.committed


def run_update(monkeypatch, result_sets, **fields):
    capture = []
    conn = FakeConn(result_sets, capture)
    monkeypatch.setattr(jobs_routes.db, "connect", lambda: conn)
    out = jobs_routes.update_details(7, JobDetailsUpdate(**fields))
    return out, capture, conn


def test_edit_collision_rejected(monkeypatch):
    # Editing job 7's title+company onto job 9's fingerprint -> 409.
    result_sets = [
        (("id", "title", "company"), [(7, "Old Title", "Old Co")]),        # current
        (("id", "title", "company"), [(9, "Junior Dev", "Acme")]),        # others
    ]
    with pytest.raises(HTTPException) as ei:
        run_update(monkeypatch, result_sets, title="Junior Dev",
                   company="Acme")
    assert ei.value.status_code == 409
    assert "matches existing job #9" in ei.value.detail


def test_edit_title_only_collision_rejected(monkeypatch):
    # Editing only the title to match another row (same company) -> 409.
    result_sets = [
        (("id", "title", "company"), [(7, "Old Title", "Acme")]),          # current
        (("id", "title", "company"), [(9, "Junior Dev", "Acme")]),        # others
    ]
    with pytest.raises(HTTPException) as ei:
        run_update(monkeypatch, result_sets, title="Junior Dev")
    assert ei.value.status_code == 409


def test_edit_unique_title_passes(monkeypatch):
    # Editing to a unique title+company -> 200, committed.
    result_sets = [
        (("id", "title", "company"), [(7, "Old Title", "Old Co")]),        # current
        (("id", "title", "company"), [(9, "Junior Dev", "Acme")]),        # others
    ]
    out, capture, conn = run_update(monkeypatch, result_sets,
                                    title="Unique New Title")
    assert out["id"] == 7
    assert conn.committed
    update_sql = [s for s, _ in capture if s.strip().upper().startswith("UPDATE")]
    assert len(update_sql) == 1
    assert "title" in update_sql[0]
    assert "company" not in update_sql[0]


def test_edit_same_values_no_collision(monkeypatch):
    # Re-saving the row's own title+company is not a collision (id <> self).
    result_sets = [
        (("id", "title", "company"), [(7, "Junior Dev", "Acme")]),        # current
        (("id", "title", "company"), [(9, "Other", "Co")]),                # others
    ]
    out, capture, conn = run_update(monkeypatch, result_sets,
                                    title="Junior Dev", company="Acme")
    assert out["id"] == 7
    assert conn.committed
