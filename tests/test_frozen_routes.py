"""Route order in the frozen (PyInstaller) build.

When frozen, the app mounts dashboard/dist at "/". Starlette matches routes in
registration order and a Mount at "/" matches every path, so any route added
*after* the mount is unreachable. /health is the endpoint launcher.py polls to
decide the API is up, so a mount registered before it makes the packaged app
hang for 60s and then give up -- the exe looked broken with no error anywhere.
"""
import importlib
import sys

import pytest
from starlette.routing import Mount


@pytest.fixture
def frozen_app(tmp_path, monkeypatch):
    """Import api.main the way the packaged exe sees it, and restore it after."""
    exe_dir = tmp_path / "JobScraper"
    dist = exe_dir / "_internal" / "dashboard" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "JobScraper.exe"))

    import api.main

    module = importlib.reload(api.main)
    try:
        yield module.app
    finally:
        monkeypatch.undo()
        importlib.reload(api.main)


def test_frozen_build_serves_the_dashboard(frozen_app):
    mounts = [r for r in frozen_app.routes if isinstance(r, Mount)]
    assert len(mounts) == 1, "the frozen build should mount dashboard/dist"


def test_health_is_not_shadowed_by_the_dashboard_mount(frozen_app):
    paths = [getattr(r, "path", None) for r in frozen_app.routes]
    assert "/health" in paths, "the launcher needs GET /health to detect a ready API"
    assert paths.index("/health") < min(
        i for i, r in enumerate(frozen_app.routes) if isinstance(r, Mount)
    ), "/health is registered after the catch-all mount, so it 404s when frozen"
