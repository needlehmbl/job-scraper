"""Shared headless-Chromium helpers for browser-gated scrapers.

Factored from JobStreetScraper.__enter__ so every Playwright scraper
(jora, kalibrr, grabjobs, …) launches the same way: browser UA, en-PH
locale, optional persistent storage_state, automation flag disabled.
No behavior change for existing scrapers.
"""
import pathlib as _pathlib

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
LOCALE = "en-PH"


def storage_state_path(name: str) -> _pathlib.Path:
    """Repo-root storage_state/<name>.json path for a scraper."""
    return (_pathlib.Path(__file__).parent.parent.parent
            / "storage_state" / f"{name}.json")


def launch_context(pw, *, headless: bool = True,
                   storage_state: str | None = None):
    """Launch Chromium + context + page. Caller owns teardown order:
    page → context → browser (mirrors JobStreetScraper teardown)."""
    browser = pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled",
              "--no-sandbox"],
    )
    ctx_kwargs = dict(user_agent=BROWSER_UA, locale=LOCALE)
    if storage_state:
        ctx_kwargs["storage_state"] = storage_state
    context = browser.new_context(**ctx_kwargs)
    page = context.new_page()
    return browser, context, page
