import os
import pathlib

from api.routes.admin import resolve_version


def _tree(tmp_path, version, layout="package"):
    """Build a fake install layout and return the dir resolution starts from."""
    if layout == "frozen":
        root = tmp_path / "JobScraper" / "_internal"
        (root / "dashboard" / "dist").mkdir(parents=True)
        start = root
    else:
        # mirrors the shipped tree: <root>/VERSION and <root>/api/routes/admin.py
        start = tmp_path / "api" / "routes"
        start.mkdir(parents=True)
        root = tmp_path
    if version is not None:
        (root / "VERSION").write_text(version)
    return start


def test_no_env_and_no_file_reports_dev(tmp_path):
    # A source checkout: no JOBSCRAPER_VERSION, no baked VERSION file.
    start = _tree(tmp_path, None)
    assert resolve_version(None, None, start) == "dev"


def test_env_var_wins(tmp_path):
    start = _tree(tmp_path, "v0.2.1\n")
    assert resolve_version("v0.2.9", None, start) == "v0.2.9"


def test_file_beside_the_package_is_found(tmp_path):
    # The tarball case: VERSION sits next to api/ and the Dockerfile's COPY . .
    # lands it in /app/VERSION, two levels above api/routes/admin.py.
    start = _tree(tmp_path, "v0.2.6\n")
    assert resolve_version(None, None, start) == "v0.2.6"


def test_frozen_build_reads_the_meipass_dir(tmp_path):
    # The Windows case: the spec ships VERSION as a data file, so it lands in
    # sys._MEIPASS next to _internal/dashboard/dist, not next to api/routes.
    start = _tree(tmp_path, "v0.2.6", layout="frozen")
    assert resolve_version(None, start, tmp_path / "nowhere") == "v0.2.6"


def test_whitespace_and_newlines_are_stripped(tmp_path):
    start = _tree(tmp_path, "  v0.2.6 \n\n")
    assert resolve_version(None, None, start) == "v0.2.6"


def test_a_non_tag_file_is_not_trusted(tmp_path):
    # A stale VERSION left behind by a local build must not make a source
    # checkout claim to be a release -- that is what the banner bug was.
    start = _tree(tmp_path, "dev\n")
    assert resolve_version(None, None, start) == "dev"


def test_a_tag_shaped_file_stops_the_walk_at_the_nearest_one(tmp_path):
    # The walk goes upward from api/routes, so a nested VERSION beats the one
    # in the root. Nothing nests one in the real layouts; this pins the order.
    start = _tree(tmp_path, "v0.2.6\n")
    (start / "VERSION").write_text("v0.0.1\n")
    assert resolve_version(None, None, start) == "v0.0.1"


def test_default_start_is_the_repo_root(tmp_path, monkeypatch):
    # The no-argument call must work against the real checkout, not only
    # against the injected paths the other tests use.
    monkeypatch.delenv("JOBSCRAPER_VERSION", raising=False)
    monkeypatch.setattr("sys.frozen", False, raising=False)
    assert resolve_version() == "dev"


def test_jupyter_style_relative_paths_do_not_crash(tmp_path):
    # Some launchers import the app with a relative sys.path entry. Never let a
    # version lookup turn into an exception in /updates/check.
    assert resolve_version(None, None, pathlib.Path("relative/api/routes")) == "dev"
    assert resolve_version(None, None, pathlib.Path(os.sep)) == "dev"
