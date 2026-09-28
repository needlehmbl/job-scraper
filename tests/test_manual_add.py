from manual_fetch import normalize_url, site_from_url


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
