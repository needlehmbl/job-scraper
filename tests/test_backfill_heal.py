"""Heal-on-conflict (upsert) + salary extraction for the backfill.

Route/db functions are exercised with stubbed connections -- no live
Postgres, no HTTP.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup
from psycopg2.errors import UniqueViolation

import db
import manual_fetch as mf


class FakeCursor:
    def __init__(self, capture):
        self.capture = capture

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.capture.append((sql, params))
        if sql.strip().upper().startswith("INSERT"):
            raise UniqueViolation("duplicate key value")


class FakeConn:
    def __init__(self, capture):
        self.capture = capture
        self.committed = 0

    def cursor(self):
        return FakeCursor(self.capture)

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _row():
    return {"source": "linkedin", "title": "Dev", "company": "Acme",
            "url": "https://example.com/j/1", "location": "Makati",
            "date_posted": None, "status": "NEW", "score": 0,
            "score_reason": "", "description": "Real desc",
            "salary_raw": "PHP 30,000-40,000 monthly",
            "salary_currency": "PHP", "salary_min": 30000.0,
            "salary_max": 40000.0, "salary_interval": "monthly",
            "salary_monthly_min": 30000.0, "salary_monthly_max": 40000.0,
            "salary_display": "PHP 30k–40k/mo"}


def test_upsert_heals_empty_columns_on_conflict():
    cap = []
    assert db.upsert_job(_row(), conn=FakeConn(cap)) is False
    updates = [sql for sql, _ in cap
               if sql.strip().upper().startswith("UPDATE")]
    assert len(updates) == 1
    assert "description" in updates[0]
    assert "salary_display" in updates[0]
    # empty-only guard: never overwrite a stored value
    assert "ELSE description END" in updates[0]


def test_upsert_conflict_without_new_data_issues_no_update():
    row = _row()
    row["description"] = ""
    row["salary_display"] = ""
    cap = []
    assert db.upsert_job(row, conn=FakeConn(cap)) is False
    assert not [sql for sql, _ in cap
                if sql.strip().upper().startswith("UPDATE")]


def test_salary_snippet_extracts_pay_sentence():
    desc = ("We are hiring for many roles across the engineering org chart "
            "and product design studio in Makati Central Business District "
            "with 3-5 years experience needed from all applicants applying. "
            "Successful candidates join a collaborative team working on "
            "modern systems with mentorship and clear growth paths ahead. "
            "We offer Salary: ₱30,000 - ₱40,000 per month plus benefits "
            "and allowances galore for all permanent hires joining this "
            "quarter under the new compensation framework agreement.")
    out = mf.salary_snippet(desc)
    assert "₱30,000" in out
    assert "years experience" not in out
    from salary import parse_salary_text
    p = parse_salary_text(out)
    assert (p["currency"], p["min"], p["max"],
            p["interval"]) == ("PHP", 30000, 40000, "monthly")


def test_salary_snippet_rejects_non_pay():
    assert mf.salary_snippet("3-5 years experience. Day 1 onboarding.") == ""
    assert mf.salary_snippet("") == ""


def test_extract_salary_raw_prefers_selector():
    soup = BeautifulSoup(
        '<div data-automation="jobSalary">₱30,000 – ₱40,000 a month</div>'
        '<article>Body without pay.</article>', "html.parser")
    assert "₱30,000" in mf.extract_salary_raw(soup, "jobstreet", "Body")


def test_jsonld_base_salary():
    item = {"@type": "JobPosting",
            "baseSalary": {"@type": "MonetaryAmount",
                           "currency": "PHP",
                           "value": {"@type": "QuantitativeValue",
                                     "minValue": 30000,
                                     "maxValue": 40000,
                                     "unitText": "MONTH"}}}
    out = mf._jsonld_salary(item)
    assert "30000" in out and "40000" in out and "PHP" in out


def test_age_phrase_never_sets_interval():
    from salary import parse_salary_text
    p = parse_salary_text(
        "Junior Dev 5 days ago Apply Metro Manila Full-time "
        "₱42,000 - ₱60,000 Contract")
    assert p["min"] == 42000 and p["max"] == 60000
    assert p["interval"] == "unknown"
    assert parse_salary_text("1 day ago")["min"] is None


def test_snippet_windows_around_amount():
    desc = ("Home Browse jobs General engineering directory listings for "
            "all seniority levels and technology stacks nationwide search "
            "Junior Dev 5 days ago Apply now button here "
            "Metro Manila, Philippines Asticom Technology Incorporated "
            "Full-time employment type listed here "
            "₱42,000 - ₱60,000 Contract Free with email or Google "
            "Save this job and keep your search organized in your personal "
            "dashboard workspace area with alerts and notifications enabled "
            "Create a free account to save jobs and manage applications")
    out = mf.salary_snippet(desc)
    assert "₱42,000" in out
    assert "Home Browse" not in out
    assert "free account" not in out
    from salary import parse_salary_text
    p = parse_salary_text(out)
    assert p["interval"] == "unknown"
