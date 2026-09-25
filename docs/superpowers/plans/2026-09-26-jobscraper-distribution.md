# JobScraper Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship containerized Linux releases and a Windows installer/wizard so a complete beginner can install, configure, run, update, and shut down JobScraper without touching a terminal.

**Architecture:** Docker Compose (`db`/`api`/`web`) on Linux; PyInstaller onedir `JobScraper.exe` + Inno Setup on Windows with `winget` Postgres fallback; one stdlib `setup_wizard.py` + `launcher.py` shared by both; `POST /shutdown` powers the dashboard shutdown button.

**Tech Stack:** Docker Compose, Postgres 16, Python 3.12 (containers) / 3.14 (local dev), FastAPI + uvicorn, Vite prebuilt `dashboard/dist` served by nginx (Linux) or FastAPI StaticFiles (Windows exe), PyInstaller 6, Inno Setup 6, Tkinter (wizard GUI), GitHub Actions (`ubuntu-latest`, `windows-latest`).

**Spec:** `docs/superpowers/specs/2026-09-26-jobscraper-distribution-design.md`

## Global Constraints

- Trunk-only `main`, plain imperative commit messages (repo convention).
- Never commit `.env`, `dashboard/dist/`, `storage_state/`, or live API keys (all gitignored).
- `dashboard/dist/` stays gitignored; releases build it via `npm ci && npm run build`.
- Dev flow keeps working: `venv`, systemd units, `run_and_open.sh`, `venv/bin/python main.py`.
- Everything user-facing opens in the OS default browser; Playwright Chromium is headless scrape-only.
- Postgres stays; migrations via existing `ensure_tracking_schema()` on API startup.
- Host URL stays `http://localhost:5173`; API stays `http://127.0.0.1:8000`.
- Windows installer is unsigned for v1 (note the SmartScreen warning in release notes + README).
- Wizard/installer copy is plain-language (no "venv", "compose", "peer auth") with Back buttons.

## Review Focus

- Empty OpenRouter key + Skip must still produce a working `.env` with AI filter off (not a crash or empty file).
- Zero search terms or zero boards selected must be rejected by validation with a plain-language message, not written to `config.yaml`.
- `POST /shutdown` from a non-localhost host must return 403 and stop nothing.
- Running the vN+1 installer/tarball must preserve `.env`, `config.yaml`, and all Postgres rows (verify row count before/after).
- `run.sh` with no Docker daemon must print the native-fallback hint, not a raw traceback.

---

## File map

| File | Responsibility |
|---|---|
| `docker-compose.yml` (new) | `db` (postgres:16, `pgdata` volume, healthcheck), `api` (python:3.12-slim + requirements + Chromium, uvicorn), `web` (nginx serving `dashboard/dist`, host 5173→container 80) |
| `.env.example` (new) | Documented empty keys (`OPENROUTER_API_KEY=`, other providers, `DATABASE_URL=` commented) |
| `launcher.py` (new) | Cross-platform entry: ensure config → ensure DB → wait `/health` → `webbrowser.open()` dashboard → blocking wait; `--check-updates`, `--native-fallback` flags |
| `run.sh` (new) | Linux launcher: `compose up -d` (or `--native` → existing systemd/nohup path) → wait → `xdg-open` |
| `stop.sh` (modify) | Add `compose down` when the compose stack is active; keep systemd/nohup kills |
| `JobScraper.desktop` (new) | Linux desktop entry (`Exec=run.sh`, `Terminal=false`) installed by `setup.sh` |
| `api/routes/admin.py` (new) + `api/main.py` (modify: include router) | `POST /shutdown` (localhost-only, 403 otherwise) + `GET /updates/check` (GitHub Releases API) |
| `dashboard/src/App.jsx` (modify) | Header power button + confirm modal + stopped state; footer update banner |
| `setup_wizard.py` (new) | Stdlib wizard: key → terms/location → boards → dir → deps; `--check`, `--upgrade` modes; Tkinter on Windows, CLI elsewhere |
| `setup.sh` (new) | Linux wrapper: `python3 setup_wizard.py "$@"` + `.desktop` install + `compose up -d db` + `schema.sql` |
| `installer/jobscraper.iss` (new) | Inno Setup 6 script (fixed AppId, dir page, shortcuts, `winget` Postgres, finish-page wizard) |
| `installer/JobScraper.spec` (new) | PyInstaller onedir spec for `launcher.py` + `dashboard/dist` data |
| `run.ps1`, `stop.ps1` (new) | Windows launcher/stop scripts for portable-zip users |
| `.github/workflows/release.yml` (new) | Tag `v*` builds: dashboard dist, Windows exe+zip, Linux tarball, `sha256sums.txt` |
| `README.md` (modify: Setup + Run sections) | Beginner install path first, dev path second |

**Interfaces:**
- `setup_wizard.py` produces `.env` (`OPENROUTER_API_KEY=...`) and edits `config.yaml` (`search.search_terms`, `search.location`, `search.site_names`); `launcher.py` and the API consume both.
- `GET /health` (existing) is the readiness signal `launcher.py`/`run.sh` poll.
- `POST /shutdown` (Task 3) is consumed by the dashboard power button (Task 3) and `stop.ps1`/`stop.sh`.
- `GET /updates/check` returns `{"update_available": bool, "latest": "vX.Y.Z"}`; consumed by the dashboard banner and `launcher.py --check-updates`.

### Task 1: Compose stack + `.env.example`

**Files:**
- Create: `docker-compose.yml`, `.env.example`
- Test: `docker compose config` (no daemon needed)

**Interfaces:**
- Consumes: `requirements.txt`, `schema.sql`, `dashboard/dist` (built at release/launch time, gitignored).
- Produces: service names `db`/`api`/`web`, `DATABASE_URL=postgresql://jobscraper:jobscraper@db:5432/job_scraper` consumed by `db.py` (already honors env).

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: jobscraper
      POSTGRES_PASSWORD: jobscraper
      POSTGRES_DB: job_scraper
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U jobscraper -d job_scraper"]
      interval: 5s
      timeout: 3s
      retries: 12
  api:
    build: .
    environment:
      DATABASE_URL: postgresql://jobscraper:jobscraper@db:5432/job_scraper
    depends_on:
      db:
        condition: service_healthy
  web:
    image: nginx:alpine
    ports:
      - "5173:80"
    volumes:
      - ./dashboard/dist:/usr/share/nginx/html:ro
    depends_on:
      - api

volumes:
  pgdata:
```

Plus a `Dockerfile` (new, root) for `api`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium
COPY . .
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`.dockerignore` must exclude `venv/`, `dashboard/node_modules/`, `__pycache__/`, `.env`:

```
venv/
__pycache__/
*.pyc
.env
dashboard/node_modules/
dashboard/dist/
storage_state/
.git/
```

- [ ] **Step 2: Write `.env.example`**

```
# Copy to .env (the setup wizard does this for you).
OPENROUTER_API_KEY=
GROQ_API_KEY=
MISTRAL_API_KEY=
GEMINI_API_KEY=
# Docker Compose sets this automatically; uncomment for native runs:
# DATABASE_URL=postgresql:///job_scraper
```

- [ ] **Step 3: Validate compose file**

Run: `docker compose config` (from repo root)
Expected: prints resolved YAML with `db`, `api`, `web`, no errors.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml Dockerfile .dockerignore .env.example
git commit -m "Distribution: add Docker Compose stack and .env.example"
```

### Task 2: `launcher.py` + `run.sh` + `stop.sh` + desktop entry

**Files:**
- Create: `launcher.py`, `run.sh`, `JobScraper.desktop`
- Modify: `stop.sh` (append compose-aware block, keep existing kills)
- Test: `python3 launcher.py --help`, `bash -n run.sh`, `bash -n stop.sh`

**Interfaces:**
- Consumes: `GET /health` on `http://127.0.0.1:8000`; `docker-compose.yml` from Task 1.
- Produces: running stack + default-browser tab; `--check-updates` output `update available: vX (current vY)` or `up to date`.

- [ ] **Step 1: Write `launcher.py`** (stdlib only: `argparse`, `os`, `sys`, `time`, `urllib.request`, `webbrowser`, `subprocess`, `pathlib`)

```python
"""Cross-platform JobScraper launcher (default browser, beginner-safe).

Ensures .env/config.yaml (runs setup_wizard.py if missing), ensures the
backend is up, waits for GET /health, then opens the dashboard in the OS
default browser. Blocking: Ctrl+C or the dashboard power button stops it.
"""
import argparse, os, subprocess, sys, time, urllib.request, webbrowser
from pathlib import Path


def base_dir():
    # PyInstaller onedir: data files sit next to the exe, __file__ points
    # into the temp _MEI bundle dir -- use the exe's dir when frozen.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = base_dir()
API_HEALTH = "http://127.0.0.1:8000/health"
DASH_URL = "http://localhost:5173"


def wait_ready(timeout=60):
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(API_HEALTH, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def ensure_config():
    if not (ROOT / ".env").exists():
        print("[launcher] first run -- opening setup wizard.")
        subprocess.run([sys.executable, str(ROOT / "setup_wizard.py")], check=False)


def compose_up():
    subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT, check=False)


def check_updates():
    import json
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/needlehmbl/job-auto-apply/releases/latest",
            headers={"User-Agent": "jobscraper-launcher"})
        with urllib.request.urlopen(req, timeout=10) as r:
            latest = json.load(r)["tag_name"]
        print(f"latest release: {latest}")
    except Exception as e:
        print(f"[launcher] update check failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-updates", action="store_true")
    ap.add_argument("--native-fallback", action="store_true",
                    help="skip docker, expect API started externally")
    args = ap.parse_args()
    if args.check_updates:
        return check_updates()
    ensure_config()
    if not args.native_fallback:
        try:
            subprocess.run(["docker", "info"], capture_output=True, check=True)
            compose_up()
        except Exception:
            print("[launcher] Docker not available -- start the API manually "
                  "(venv/bin/uvicorn api.main:app) and re-run with --native-fallback.")
            return 1
    if not wait_ready():
        print("[launcher] API did not become ready -- see api.log.")
        return 1
    print(f"[launcher] opening {DASH_URL} in your default browser.")
    webbrowser.open(DASH_URL)
    print("[launcher] running -- close this window or use the dashboard power button to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify launcher help + compile**

Run: `python3 launcher.py --help && python3 -m py_compile launcher.py`
Expected: help text prints; compile passes. (`setup_wizard.py` is compiled in Task 4.)

- [ ] **Step 3: Write `run.sh`** (executable, Linux; mirrors `run_and_open.sh` waits)

```bash
#!/usr/bin/env bash
# JobScraper launcher (Linux): start the stack, open the default browser.
# Usage: ./run.sh [--native]  (--native reuses systemd/nohup like run_and_open.sh)
set -e
cd "$(dirname "$0")"
if [ "${1:-}" = "--native" ]; then
  exec python3 launcher.py --native-fallback
fi
exec python3 launcher.py
```

Run: `chmod +x run.sh && bash -n run.sh`
Expected: no syntax errors.

- [ ] **Step 4: Extend `stop.sh`** — prepend (keep all existing kills):

```bash
# 0. Compose stack (no-op when Docker or the stack is absent)
if docker compose ps >/dev/null 2>&1; then
    echo "[stop] stopping compose stack"
    docker compose down 2>/dev/null || true
    stopped=1
fi
```

(`stopped=0` initialization already exists below; place this block after it. If `stopped` is referenced before assignment, add `stopped=0` above the block.)

Run: `bash -n stop.sh`
Expected: no syntax errors; `./stop.sh` on a non-running machine still prints "already stopped".

- [ ] **Step 5: Write `JobScraper.desktop`**

```
[Desktop Entry]
Type=Application
Name=JobScraper
Comment=Review junior developer job leads
Exec=/bin/bash -c 'cd %k/.. 2>/dev/null; ./run.sh'
Icon=jobscraper
Terminal=false
Categories=Utility;
```

(`setup.sh` in Task 4 substitutes the real install path into `Exec` and copies this to `~/.local/share/applications/`.)

- [ ] **Step 6: Commit**

```bash
git add launcher.py run.sh stop.sh JobScraper.desktop
git commit -m "Distribution: add cross-platform launcher, run/stop scripts, desktop entry"
```

### Task 3: Shutdown endpoint + dashboard power button + update banner

**Files:**
- Create: `api/routes/admin.py`
- Modify: `api/main.py` (add `from api.routes import admin`, `app.include_router(admin.router, tags=["admin"])`), `dashboard/src/App.jsx` (header power button + confirm modal + stopped screen; footer update banner polling `GET /updates/check`)

**Interfaces:**
- Consumes: `GET /health` pattern from `api/main.py`; scrape-router `_state` style is NOT reused (shutdown is stateless).
- Produces: `POST /shutdown -> {"ok": true}`; `GET /updates/check -> {"update_available": bool, "latest": str | null}`.

- [ ] **Step 1: Write `api/routes/admin.py`**

```python
"""Local-only admin endpoints: shutdown + update check.

POST /shutdown      -- stop the packaged stack (localhost only, else 403).
GET  /updates/check -- latest GitHub release tag vs current version.
"""
import os
import urllib.request
import json
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

VERSION = os.environ.get("JOBSCRAPER_VERSION", "dev")
REPO = os.environ.get("JOBSCRAPER_REPO", "needlehmbl/job-auto-apply")


def _local_only(request: Request):
    host = (request.client.host if request.client else "")
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="shutdown is local-only")


@router.post("/shutdown")
def shutdown(request: Request):
    _local_only(request)
    # Packaged launchers (compose / exe) supervise this process: exiting here
    # stops the stack. Dev systemd units restart on exit -- use stop.sh there.
    os._exit(0)
    return {"ok": True}  # unreachable; keeps type-checkers quiet


@router.get("/updates/check")
def updates_check():
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"User-Agent": "jobscraper-api"})
        with urllib.request.urlopen(req, timeout=10) as r:
            latest = json.load(r)["tag_name"]
        return {"update_available": latest != VERSION, "latest": latest,
                "current": VERSION}
    except Exception as e:
        return {"update_available": False, "latest": None, "current": VERSION,
                "error": str(e)}
```

- [ ] **Step 2: Verify non-localhost is rejected** (run API, curl with spoofed host is not possible via client.host; instead unit-check `_local_only` raises for `127.0.0.2`):

Run: `python3 -c "from api.routes.admin import _local_only; from types import SimpleNamespace as S; _local_only(S(client=S(host='127.0.0.2')))"`
Expected: traceback ending `HTTPException: 403`. Then `127.0.0.1` passes silently.

- [ ] **Step 3: Wire router into `api/main.py`** — extend the routes import and add after the tailor include:

```python
app.include_router(admin.router, tags=["admin"])
```

Run: `python3 -c "import api.main; print([r.path for r in api.main.app.routes if 'shutdown' in r.path or 'updates' in r.path])"`
Expected: prints paths including `/shutdown` and `/updates/check`.

- [ ] **Step 4: Dashboard power button** in `dashboard/src/App.jsx` header: a power-icon button opening the existing confirm-modal pattern ("Stop JobScraper? The dashboard and scraper will stop. Restart with the desktop shortcut."), Confirm → `fetch("http://127.0.0.1:8000/shutdown", {method: "POST"})` → replace page with "JobScraper stopped — you can close this tab." card. Footer: on mount, `fetch(.../updates/check)`; if `update_available`, show banner "Update available: {latest} — run setup.sh --upgrade (Linux) or re-run the installer (Windows)."

- [ ] **Step 5: Verify dashboard builds**

Run: `npm run build` (in `dashboard/`)
Expected: build succeeds; `dist/` contains updated assets.

- [ ] **Step 6: Commit**

```bash
git add api/routes/admin.py api/main.py dashboard/src/App.jsx
git commit -m "Distribution: add local-only shutdown endpoint and dashboard power button"
```

### Task 4: Setup wizard + `setup.sh` (key → terms → boards → dir → deps)

**Files:**
- Create: `setup_wizard.py`, `setup.sh`
- Test: `python3 setup_wizard.py --check`, `bash -n setup.sh`

**Interfaces:**
- Consumes: `config.yaml` keys `search.search_terms`, `search.location`, `search.site_names`; `.env` provider keys.
- Produces: valid `.env` + edited `config.yaml`; exit codes 0/1 for `--check`; `--upgrade <tarball>` preserves config + `pgdata`.

- [ ] **Step 1: Write `setup_wizard.py`** — stdlib only (`argparse`, `tkinter` guarded import, `urllib`, `yaml` via PyYAML which is already a runtime dep). Structure (full code in implementation; contract below):

  - `load_config()/save_config()` round-trip `config.yaml` preserving unknown keys.
  - `validate_key()` — non-empty, ≥20 chars (OpenRouter `sk-or-v1-...`); Test button calls `GET https://openrouter.ai/api/v1/auth/key` with the key, shows "Key works" / error.
  - `validate_terms_location_boards()` — ≥1 non-blank term, non-blank location, ≥1 board from `{indeed, linkedin, jobstreet, glassdoor, trabajo}`; failures print plain-language messages (`"Add at least one job title, e.g. Junior Developer."`).

```python
BOARDS = ["indeed", "linkedin", "jobstreet", "glassdoor", "trabajo"]

def validate_terms_location_boards(terms, location, boards):
    """Return True when valid, else a plain-language error string."""
    if not [t for t in terms if t.strip()]:
        return "Add at least one job title, e.g. Junior Developer."
    if not location.strip():
        return "Type the location as you'd type it on a job site, e.g. Metro Manila, Philippines."
    if not [b for b in boards if b in BOARDS]:
        return "Tick at least one job board."
    return True


def validate_key(key):
    """Return True when the key looks usable, else a plain-language error."""
    if not key.strip():
        return True  # empty + Skip = AI filter stays off, not an error
    if len(key.strip()) < 20:
        return "That key looks too short -- copy the full key from the OpenRouter keys page."
    return True
```

  - Interactive modes: Tkinter wizard (Windows: 6 pages matching spec §4 with Back/Next, "Get a free key" button → `webbrowser.open("https://openrouter.ai/keys")`) or CLI prompts (Linux/stdin not a TTY → error telling the user to run `setup.sh`). Both modes call the same `validate_*` functions above and the same `load_config()/save_config()` round-trip (`config.yaml` keys `search.search_terms`, `search.location`, `search.site_names`; unknown keys preserved).
  - `--check`: load `.env` + `config.yaml`, run validators, try DB connect (`DATABASE_URL` or local socket), print each result, exit 1 on any failure.
  - `--upgrade <tarball>`: copy `.env` + `config.yaml` to temp, extract tarball over install dir, restore the two files, print "Update keeps everything — your jobs, key, and search terms stay."
  - Interactive modes: Tkinter wizard (Windows: 6 pages matching spec §4 with Back/Next, "Get a free key" button → `webbrowser.open("https://openrouter.ai/keys")`) or CLI prompts (Linux/stdin not a TTY → error telling the user to run `setup.sh`).
  - Deps step: if `docker info` works → `docker compose up -d db`, wait healthy, `psql -f schema.sql` once; elif Windows → `winget install --accept-source-agreements PostgreSQL.PostgreSQL.16`, `createdb job_scraper`, `psql -f schema.sql`; else print native-Postgres steps from `README.md` §2. `playwright install chromium` verified, failures show Retry prompt.

- [ ] **Step 2: Failing-first validation check** — with a scratch copy (never the real files): empty terms list must fail:

Run: `cp config.yaml /tmp/cfg-bak.yaml && python3 - <<'EOF'
import setup_wizard
bad = setup_wizard.validate_terms_location_boards([], "Metro Manila, Philippines", ["indeed"])
print("rejected:" , bad is not True)
EOF`
Expected: prints `rejected: True`. (Implement `validate_terms_location_boards` to return `True` when valid, error string otherwise.)

- [ ] **Step 3: Write `setup.sh`** (executable):

```bash
#!/usr/bin/env bash
# JobScraper first-time setup (Linux): wizard -> desktop shortcut -> DB.
# Usage: ./setup.sh [--dir PATH] | ./setup.sh --check | ./setup.sh --upgrade TARBALL
set -e
cd "$(dirname "$0")"
python3 setup_wizard.py "$@"
if [ "${1:-}" != "--check" ]; then
  sed "s|^Exec=.*|Exec=$(pwd)/run.sh|" JobScraper.desktop > ~/.local/share/applications/JobScraper.desktop
fi
```

Run: `chmod +x setup.sh && bash -n setup.sh && ./setup.sh --check`
Expected: `--check` reports key/config/DB status lines (fails gracefully if `.env` missing: "No .env yet — run ./setup.sh").

- [ ] **Step 4: Commit**

```bash
git add setup_wizard.py setup.sh
git commit -m "Distribution: add beginner setup wizard and Linux setup script"
```

### Task 5: Windows packaging (PyInstaller + Inno + ps1)

**Files:**
- Create: `installer/JobScraper.spec`, `installer/jobscraper.iss`, `run.ps1`, `stop.ps1`
- Test: `iscc` syntax check in CI (Task 6); local `pyinstaller --noconfirm installer/JobScraper.spec` on a Windows dev machine.

**Interfaces:**
- Consumes: `launcher.py`, `setup_wizard.py`, prebuilt `dashboard/dist`, `api/` package.
- Produces: `dist/JobScraper/JobScraper.exe` (single folder) consumed by the Inno script and the portable zip.

- [ ] **Step 1: Write `installer/JobScraper.spec`**

```python
# -*- mode: python -*-
block_cipher = None
a = Analysis(
    ["../launcher.py"],
    pathex=[".."],
    binaries=[],
    datas=[("../dashboard/dist", "dashboard/dist"), ("../config.yaml", "."),
           ("../schema.sql", "."), ("../setup_wizard.py", ".")],
    hiddenimports=["uvicorn", "fastapi", "playwright"],
    excludes=[],
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="JobScraper",
          console=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="JobScraper")
```

- [ ] **Step 2: Write `installer/jobscraper.iss`** (Inno Setup 6; fixed AppId so reruns detect + update):

```ini
#define AppVersion GetEnv("APP_VERSION")

[Setup]
AppId={{A3F1C9E2-7B4D-4E8A-9C2F-5D6B7A8E9F01}
AppName=JobScraper
AppVersion={#AppVersion}
DefaultDirName={autopf}\JobScraper
UsePreviousAppDir=yes
DirExistsWarning=auto
DefaultGroupName=JobScraper
OutputBaseFilename=JobScraper-Setup-{#AppVersion}
Compression=lzma2
PrivilegesRequired=lowest

[Files]
Source: "..\dist\JobScraper\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{group}\JobScraper"; Filename: "{app}\JobScraper.exe"
Name: "{commondesktop}\JobScraper"; Filename: "{app}\JobScraper.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: checkedonce

[Run]
; First launch auto-opens the setup wizard (launcher.py ensure_config runs
; setup_wizard.py when .env is missing), then the app in the default browser.
Filename: "{app}\JobScraper.exe"; Description: "Open JobScraper now"; Flags: postinstall skipifsilent
```

- [ ] **Step 3: Write `run.ps1` / `stop.ps1`**

```powershell
# run.ps1 -- portable launcher: start exe, open default browser is done by the exe.
Start-Process -FilePath (Join-Path $PSScriptRoot "JobScraper.exe")
```

```powershell
# stop.ps1 -- tell the local API to shut down, then kill leftovers.
try { Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/shutdown" } catch {}
Get-Process -Name "JobScraper" -ErrorAction SilentlyContinue | Stop-Process
```

- [ ] **Step 4: Commit**

```bash
git add installer/JobScraper.spec installer/jobscraper.iss run.ps1 stop.ps1
git commit -m "Distribution: add Windows PyInstaller spec, Inno installer, ps1 launchers"
```

### Task 6: Release workflow + README

**Files:**
- Create: `.github/workflows/release.yml`
- Modify: `README.md` (§2 Setup, §3 Run: beginner path first, dev path second; SmartScreen note; exe-OR-zip choice; update instructions)

**Interfaces:**
- Consumes: all Tasks 1–5 outputs.
- Produces: GitHub Release assets `JobScraper-Setup-v*.exe`, `jobscraper-win64.zip`, `jobscraper-linux.tar.gz`, `sha256sums.txt`.

- [ ] **Step 1: Write `.github/workflows/release.yml`**

```yaml
name: release
on:
  push:
    tags: ["v*"]
jobs:
  linux:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: {node-version: 20}
      - run: cd dashboard && npm ci && npm run build
      - run: >
          tar -czf jobscraper-linux.tar.gz docker-compose.yml Dockerfile
          .dockerignore run.sh stop.sh setup.sh setup_wizard.py launcher.py
          JobScraper.desktop schema.sql config.yaml .env.example
          dashboard/dist README.md
      - uses: actions/upload-artifact@v4
        with: {name: linux-tarball, path: jobscraper-linux.tar.gz}
  windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - uses: actions/setup-node@v4
        with: {node-version: 20}
      - run: cd dashboard; npm ci; npm run build
      - run: pip install -r requirements.txt pyinstaller
      - run: python -m playwright install chromium
      - run: pyinstaller --noconfirm installer/JobScraper.spec
        env: {APP_VERSION: ${{ github.ref_name }}}
      - uses: Minionguyjpro/Inno-Setup-Action@v1.2.2
      - run: iscc installer/jobscraper.iss
        env: {APP_VERSION: ${{ github.ref_name }}}
      - run: Compress-Archive -Path dist/JobScraper -DestinationPath jobscraper-win64.zip
      - uses: actions/upload-artifact@v4
        with: {name: windows-assets, path: "JobScraper-Setup-*.exe, jobscraper-win64.zip"}
  publish:
    needs: [linux, windows]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: {path: assets, merge-multiple: true}
      - run: cd assets && sha256sum * > sha256sums.txt
      - uses: softprops/action-gh-release@v2
        with: {files: "assets/*"}
```

- [ ] **Step 2: Validate workflow YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"`
Expected: parses without error.

- [ ] **Step 3: Rewrite `README.md` §2–§3** — beginner install first (Windows exe vs portable zip table; Linux tarball + `./setup.sh`), "Update keeps everything" subsection (exe rerun / `--upgrade`), dev path second (existing venv/systemd content kept, not deleted), SmartScreen unsigned-exe note.

- [ ] **Step 4: End-to-end smoke (Linux, local)**

Run: `docker compose config && ./setup.sh --check && python3 launcher.py --help && bash -n run.sh && bash -n stop.sh`
Expected: all pass; `--check` explains any missing piece in plain language.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml README.md
git commit -m "Distribution: add release workflow and beginner install docs"
```

## Rollout order

Tasks 1→6 in order (each is independently testable; 2 needs 1; 5 needs 2+4; 6 needs all). Tag `v0.1.0` after Task 6 smoke to exercise the release workflow.
