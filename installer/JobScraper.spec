# -*- mode: python -*-
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# jobspy scrapes with curl_cffi, which loads a prebuilt shared library out of
# tls_client/dependencies/ with ctypes. PyInstaller's walk finds the Python
# package but not that .so/.dll/.dylib, so the frozen exe died in
# tls_client/cffi.py with "Failed to load dynlib/dll". collect_all ships the
# binaries for every platform, which is what a cross-built spec needs.
tls_datas, tls_binaries, tls_hidden = collect_all("tls_client")

# The release tag, written to ../VERSION by the release job. It rides along as a
# data file so api/routes/admin.py can report the installed version; CI has no
# other way to tell the exe what it is. Optional: a local build that skipped the
# step still works, and the app then reports "dev".
#
# Anchor the path to SPECPATH (this file's own directory), not to os.getcwd():
# PyInstaller resolves the datas entries against SPECPATH, but the spec body
# itself still runs in the directory the command was launched from. Checking
# "../VERSION" against the cwd is False when you build from the repo root --
# and the data file is then dropped without a word.
import os
version_file = os.path.join(SPECPATH, os.pardir, "VERSION")
version_datas = [(version_file, ".")] if os.path.exists(version_file) else []

a = Analysis(
    ["../launcher.py"],
    pathex=[".."],
    binaries=tls_binaries,
    datas=[("../dashboard/dist", "dashboard/dist"), ("../config.yaml", "."),
           ("../schema.sql", "."), ("../setup_wizard.py", ".")]
          + tls_datas + version_datas,
    # launcher.py starts the API with uvicorn.run("api.main:app"), an import
    # string, so PyInstaller's static walk never sees the app and the frozen
    # exe dies with "No module named 'api'". Listing api.main as a hidden
    # import anchors the graph: everything api.main imports (api.routes.*,
    # api.db, db, pipeline, tailor, resumes, render_resume) comes along.
    hiddenimports=["api.main", "uvicorn", "fastapi", "playwright",
                 "scrapers", "scrapers.playwright", "scrapers.boards",
                 "scrapers.feeds", "scrapers.playwright.jobstreet",
                 "scrapers.playwright.glassdoor", "scrapers.playwright.jora",
                 "scrapers.playwright.kalibrr",
                 "scrapers.playwright.grabjobs",
                 "scrapers.playwright.common", "scrapers.boards.greenhouse",
                 "scrapers.boards.lever", "scrapers.boards.ashby",
                 "scrapers.feeds.trabajo", "scrapers.feeds.remoteok"] + tls_hidden,
    excludes=[],
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="JobScraper",
          console=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="JobScraper")
