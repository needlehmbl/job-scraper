from manual_fetch import (_clean_company, fetch_job_from_url, normalize_url,
                           parse_jobstreet_og, parse_linkedin_shell,
                           parse_trabajo_title, site_from_url)
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


def test_browser_fallback_for_plain_hosts(monkeypatch):
    # The browser retry is not limited to walled boards: any site whose
    # plain GET fails gets one render. When the browser also fails, the
    # row is saved blank (fetch_limited) instead of blocking.
    called = []
    class Walled:
        status_code = 403
        text = "nope"
    monkeypatch.setattr(manual_fetch.requests, "get",
                        lambda *a, **k: Walled())
    monkeypatch.setattr(
        manual_fetch, "fetch_with_browser",
        lambda url, site: called.append(site) or {})
    out = manual_fetch.fetch_job_from_url("https://example.com/x")
    assert called == ["example.com"]
    assert out["title"] == ""
    assert out["fetch_limited"] is True


def test_indeed_canonical_keeps_only_jk():
    url = normalize_url(
        "https://ph.indeed.com/viewjob?jk=ABC123&from=mobRdr&tk=xyz&xpse=1&xfps=2&xkcb=3")
    assert url == "https://ph.indeed.com/viewjob?jk=ABC123"


def test_walled_note_present_for_indeed(monkeypatch):
    class Walled:
        status_code = 401
        text = "Authenticating..."
    monkeypatch.setattr(manual_fetch.requests, "get",
                        lambda *a, **k: Walled())
    monkeypatch.setattr(manual_fetch, "fetch_with_browser", lambda u, s: {})
    out = manual_fetch.fetch_job_from_url("https://ph.indeed.com/viewjob?jk=ABC")
    assert out["title"] == ""
    assert "blocks anonymous fetching" in out["fetch_note"]
def test_resolve_override_wins_over_fetched():
    from manual_fetch import resolve_manual_fields
    out = resolve_manual_fields(
        {"title": "Wrong Title", "company": "Wrong Co", "location": "Cebu"},
        {"title": "Junior Dev", "company": "Acme", "location": ""})
    assert out == {"title": "Junior Dev", "company": "Acme",
                   "location": "Cebu"}


def test_resolve_blank_override_falls_back_to_fetched():
    from manual_fetch import resolve_manual_fields
    out = resolve_manual_fields(
        {"title": "Fetched Title", "company": "Fetched Co",
         "location": "Makati"},
        {"title": "  ", "company": None, "location": None})
    assert out["title"] == "Fetched Title"
    assert out["company"] == "Fetched Co"
    assert out["location"] == "Makati"


def test_resolve_nothing_gives_placeholder():
    from manual_fetch import resolve_manual_fields, is_placeholder_title
    out = resolve_manual_fields(
        {"title": "", "company": "", "location": None}, {})
    assert is_placeholder_title(out["title"])
    assert is_placeholder_title("Untitled (manual)")
    assert not is_placeholder_title("Junior Dev")


def _parse(html, site):
    from bs4 import BeautifulSoup
    return manual_fetch._parse_html(BeautifulSoup(html, "html.parser"), site)


def test_parse_linkedin_og_title_company_after_at():
    # Real LinkedIn posting: og:title "<Title> at <Company> — <Location> | <site>"
    out = _parse('<html><head><meta property="og:title" content="Junior Java Developer (Work Experience needed) at Computer Professionals Inc. — Manila, National Capital Region, Philippines | LinkedIn Jobs"></head><body></body></html>', "linkedin")
    assert out[0] == "Junior Java Developer (Work Experience needed)"
    assert out[1] == "Computer Professionals Inc."


def test_parse_linkedin_shell_company_first():
    # Anonymous shell: "<Company> hiring <Title> in <Location>" -> shell flag
    out = _parse('<html><head><title>Computer Professionals Inc. hiring Junior Java Developer (Work Experience needed) in Manila</title></head><body></body></html>', "linkedin")
    assert out[0] == "Junior Java Developer (Work Experience needed)"
    assert out[1] == "Computer Professionals Inc."
    assert out[2] == "Manila"
    assert out[4] is True


def test_parse_linkedin_shell_helper():
    assert parse_linkedin_shell(
        "Acme Corp hiring Senior Dev in Taguig") == \
        ("Senior Dev", "Acme Corp", "Taguig")
    assert parse_linkedin_shell("Junior Dev at Acme — Makati") == ("", "", "")


def test_parse_trabajo_title_company_last():
    # Trabajo <title>: "<Title> in <Location> - <Company>"
    out = _parse('<html><head><meta property="og:title" content="[Pooling] Software Engineer"><title>[Pooling] Software Engineer in Manila - White Cloak Technologies</title></head><body><h1>[Pooling] Software Engineer</h1></body></html>', "trabajo")
    assert out[0] == "[Pooling] Software Engineer"
    assert out[1] == "White Cloak Technologies"
    assert out[2] == "Manila"


def test_parse_trabajo_helper():
    assert parse_trabajo_title(
        "Junior Dev in Taguig - Acme Corp") == \
        ("Junior Dev", "Acme Corp", "Taguig")
    assert parse_trabajo_title("Just A Title") == ("", "", "")


def test_clean_company_strips_trailing_decoration():
    assert _clean_company("Computer Professionals Inc. — ") == \
        "Computer Professionals Inc."
    assert _clean_company("Acme | LinkedIn Jobs") == "Acme"
    assert _clean_company("Acme Corp") == "Acme Corp"
    assert _clean_company("Acme - ") == "Acme"


def test_browser_fallback_for_non_walled_host(monkeypatch):
    # Trabajo is not in _BROWSER_HOSTS, but when the plain GET fails the
    # browser retry must still run (Cloudflare intermittently blocks it).
    class Walled:
        status_code = 403
        text = "nope"
    monkeypatch.setattr(manual_fetch.requests, "get",
                        lambda *a, **k: Walled())
    monkeypatch.setattr(
        manual_fetch, "fetch_with_browser",
        lambda url, site: {"title": "[Pooling] Software Engineer",
                           "company": "White Cloak Technologies",
                           "location": "Manila", "description": None,
                           "site": site, "fetch_limited": False})
    out = fetch_job_from_url("https://ph.trabajo.org/job-123")
    assert out["title"] == "[Pooling] Software Engineer"
    assert out["company"] == "White Cloak Technologies"
    assert out["fetch_limited"] is False


def test_linkedin_shell_triggers_browser_retry(monkeypatch):
    # Fast path returns the anonymous shell -> browser retry for clean data.
    html = ('<html><head><title>Computer Professionals Inc. hiring '
            'Junior Java Developer in Manila</title></head></html>')
    class Resp:
        status_code = 200
        text = html
    monkeypatch.setattr(manual_fetch.requests, "get",
                        lambda *a, **k: Resp())
    monkeypatch.setattr(
        manual_fetch, "fetch_with_browser",
        lambda url, site: {"title": "Junior Java Developer",
                           "company": "Computer Professionals Inc.",
                           "location": "Manila", "description": None,
                           "site": site, "fetch_limited": False})
    out = fetch_job_from_url("https://www.linkedin.com/jobs/view/1")
    assert out["title"] == "Junior Java Developer"
    assert out["company"] == "Computer Professionals Inc."
    assert out["location"] == "Manila"


def test_linkedin_shell_browser_failure_keeps_parsed(monkeypatch):
    # Browser also fails -> keep the shell-parsed fields (best effort).
    html = ('<html><head><title>Acme Corp hiring Senior Dev in Taguig'
            '</title></head></html>')
    class Resp:
        status_code = 200
        text = html
    monkeypatch.setattr(manual_fetch.requests, "get",
                        lambda *a, **k: Resp())
    monkeypatch.setattr(manual_fetch, "fetch_with_browser",
                        lambda url, site: {})
    out = fetch_job_from_url("https://www.linkedin.com/jobs/view/1")
    assert out["title"] == "Senior Dev"
    assert out["company"] == "Acme Corp"
    assert out["location"] == "Taguig"
