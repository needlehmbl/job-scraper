"""Repeat reposts never reach the Filtered tab.

`save_filtered_jobs` drops rows whose filter_reason starts with
`repeat-seen:` -- they are reposts of already-judged listings, so holding
them for review teaches nothing. Normal filtered rows are still saved.
"""
import db as db_module


class _FakeCursor:
    def __init__(self):
        self.inserts = 0

    def execute(self, *args):
        sql = args[0] if args else ""
        if sql.lstrip().upper().startswith("INSERT"):
            self.inserts += 1

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()
        self.committed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True


def _row(url, reason):
    return {
        "title": "Backend Engineer",
        "company": "Acme",
        "job_url": url,
        "site": "indeed",
        "description": "",
        "date_posted": None,
        "matched_search_term": "backend",
        "filter_reason": reason,
    }


def test_repeat_seen_rows_are_dropped_silently():
    conn = _FakeConn()
    n = db_module.save_filtered_jobs(
        [_row("https://x/repeat", "repeat-seen:skip:Acme|Backend Engineer")],
        conn=conn,
    )
    assert n == 0
    assert conn.cur.inserts == 0


def test_normal_rows_still_saved_alongside_repeats():
    conn = _FakeConn()
    n = db_module.save_filtered_jobs(
        [
            _row("https://x/repeat", "repeat-seen:skip:Acme|Backend Engineer"),
            _row("https://x/fresh", "title-keyword:senior"),
        ],
        conn=conn,
    )
    assert n == 1
    assert conn.cur.inserts == 1
