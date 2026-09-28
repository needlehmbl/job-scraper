from manual_fetch import normalize_url, site_from_url, parse_jobstreet_og
import manual_fetch


def test_normalize_strips_tracking_but_keeps_identity():
    url = normalize_url(
        "https://ph.indeed.com/viewjob?jk=ABC123&utm_source=x&fbclid=y")
    assert "jk=ABC123" in url
    assert "utm_source" not in url
    assert "fbclid" not in url


def test_normalize_lowercases_host_and_strips_slash():
    assert normalize_url("https://PH.TRABAJO.ORG/job-abc/") == \
        "https://ph.trabajo.org/job-abc"


def test_normalize_bare_domain_gets_scheme():
    assert normalize_url("ph.trabajo.org/job-abc") == \
        "https://ph.trabajo.org/job-abc"


def test_site_detection():
    assert site_from_url("https://ph.trabajo.org/job-abc") == "trabajo"
    assert site_from_url("https://www.linkedin.com/jobs/view/123") == "linkedin"
    assert site_from_url("https://example.com/x") == "example.com"


def test_parse_jobstreet_og():
    t, loc = parse_jobstreet_og(
        "Solutions Architect Job in Taguig City, Metro Manila - Jobstreet")
    assert t == "Solutions Architect"
    assert loc == "Taguig City, Metro Manila"
    assert parse_jobstreet_og("Just a moment...") == ("", "")


def test_browser_fallback_used_when_fast_path_walled(monkeypatch):
    class Walled:
        status_code = 403
        text = "<html><head><title>Just a moment...</title></head></html>"
    monkeypatch.setattr(manual_fetch.requests, "get",
                        lambda *a, **k: Walled())
    monkeypatch.setattr(
        manual_fetch, "fetch_with_browser",
        lambda url, site: {"title": "Solutions Architect",
                           "company": "IT Managers, Inc.",
                           "location": "Taguig City, Metro Manila",
                           "description": None, "site": site,
                           "fetch_limited": False})
    out = manual_fetch.fetch_job_from_url("https://ph.jobstreet.com/job/1")
    assert out["title"] == "Solutions Architect"
    assert out["company"] == "IT Managers, Inc."
    assert out["fetch_limited"] is False


def test_no_browser_fallback_for_plain_hosts(monkeypatch):
    called = []
    class Walled:
        status_code = 403
        text = "nope"
    monkeypatch.setattr(manual_fetch.requests, "get",
                        lambda *a, **k: Walled())
    monkeypatch.setattr(
        manual_fetch, "fetch_with_browser",
        lambda url, site: called.append(site) or {"title": "X"})
    out = manual_fetch.fetch_job_from_url("https://example.com/x")
    assert called == []
    assert out["title"] == ""
    assert out["fetch_limited"] is True
