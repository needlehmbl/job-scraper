# JobScraper Distribution Design (container + Windows installer + releases)

Date: 2026-09-26. Status: approved in chat, awaiting spec review before `writing-plans`.

## 1. Goal / success criteria

Keep the current dev setup working (`venv`, systemd units, `run_and_open.sh`,
`venv/bin/python main.py`), while adding public releases a complete beginner
can install:

- Linux: `tar.gz` + Docker Compose, one `setup.sh` wizard, one launcher.
- Windows: `JobScraper-Setup-v*.exe` (Inno Setup wizard) + portable
  `jobscraper-win64.zip`, no terminal / Python / Node knowledge required.
- First-run wizard order: OpenRouter API key (with signup link) ->
  search terms + location -> job-board toggles -> install dir.
- Desktop / Start Menu shortcut behaves like `run_and_open.sh` (start stack +
  open app in the user's **default browser**); dashboard has a shutdown/power
  button; Apply action keeps opening in the user's **current/default browser**.
- GitHub Releases ship `JobScraper-Setup-*.exe`, `jobscraper-win64.zip`,
  `jobscraper-linux.tar.gz`, `sha256sums.txt` on every `v*` tag.

Non-goals: auto-applying/submitting applications, cloud hosting, mobile app,
SQLite migration (stays Postgres).

## 2. Constraints / decisions already made

- Audience: both personal + public. Trunk-only `main`, plain imperative
  commits (repo convention).
- Hybrid per-OS: Docker Compose on Linux, PyInstaller onedir + Inno on
  Windows. Docker Desktop on Windows is **optional**, not required.
- Postgres stays (no SQLite port). Linux bundles it as a container; Windows
  no-Docker fallback installs Postgres 16 via `winget` then `schema.sql`.
  `db.py` already honors `DATABASE_URL`, `ensure_tracking_schema()` migrates.
- Windows installer tech: PyInstaller onedir + Inno Setup (dir page,
  shortcuts, uninstaller). Unsigned exe is acceptable for v1.
- Browser rule (new): everything user-facing opens in the OS default browser
  (`webbrowser` / `xdg-open` / `Start-Process`). The dashboard Apply button
  already does this (`api/routes/apply.py` returns the URL, dashboard
  `window.open`s it in the existing browser); the launcher must do the same,
  never force Chromium/Playwright for normal use. Playwright Chromium stays
  headless-only for scraping (`jobstreet.py`, `glassdoor.py`).
- Beginner-friendly bar: no CLI flags to learn, every destructive/irreversible
  choice has a Back button, every external prerequisite is installed or linked
  by the wizard, plain-language copy (no "peer auth", "venv", "compose").

## 3. Architecture

```
Linux tarball:  setup.sh -> docker compose up (db/api/web) -> run.sh -> default browser
Windows exe:    Inno wizard -> JobScraper.exe (launcher.py: start API+serve dist, ensure Postgres) -> default browser
Dev (kept):     venv + systemd units + run_and_open.sh --native (unchanged behavior)
```

- `docker-compose.yml` (new, root): `db` (postgres:16, volume `pgdata`,
  healthcheck), `api` (python:3.12-slim, `pip install -r requirements.txt`,
  `playwright install --with-deps chromium`, `uvicorn api.main:app
  --host 0.0.0.0:8000`), `web` (nginx:alpine serving prebuilt
  `dashboard/dist`, host port `5173` -> container `80` so the URL stays
  `http://localhost:5173`). Only `web` port (e.g. 5173->80) published; API reachable
  via compose network + `127.0.0.1:8000` for the launcher healthcheck.
- `launcher.py` (new, cross-platform entry): ensure `.env`/`config.yaml`
  (launch wizard if missing) -> ensure DB up (compose / winget Postgres /
  native) -> run migrations via API startup -> wait `GET /health` -> open
  dashboard URL with `webbrowser.open()` (default browser) -> show tray/splash
  with Open + Stop. PyInstaller builds this into `JobScraper.exe`; `run.sh`
  thin-wraps it (`compose up -d` + `python3 launcher.py --native-fallback`).
- API serves dashboard statically in packaged mode: mount `dashboard/dist`
  (prebuilt, no Node at runtime) so the Windows exe needs only one process.
  Dev keeps Vite `:5173` + CORS as-is.
- New `api/routes/admin.py`: `POST /shutdown` (localhost-only token) stops
  compose children / exe subprocesses; dashboard header power button calls it
  with confirm modal, then shows "stopped, you can close this tab".

## 4. Setup wizard (beginner-first, full flow)

Single `setup_wizard.py` (stdlib only: Tkinter GUI on Windows, CLI prompts on
Linux; same validation), invoked by `setup.sh`, Inno finish-page, and
auto-launch when `.env` is missing:

1. Welcome (1 screen, plain words: what the app does, ~5 min, what it needs).
2. OpenRouter key: "Get a free key" button opens
   `https://openrouter.ai/keys` in the **default browser**; paste box with
   show/hide; Test button (`GET https://openrouter.ai/api/v1/auth/key`);
   Skip link (AI filter stays off, `use_ai` unchanged otherwise). Writes `.env`
   (`OPENROUTER_API_KEY=...`, preserves other provider keys).
3. What jobs: search-terms listbox (prefilled from
   `config.yaml:search.search_terms`, Add/Remove, reset-to-defaults) +
   location textbox (prefilled `search.location`). Plain hint: "City, Country
   as you'd type it on a job site".
4. Job boards: checkboxes prefilled from `config.yaml:search.site_names`
   (indeed / linkedin / jobstreet / glassdoor / trabajo) with one-line
   beginner descriptions ("Indeed — biggest listings", etc.). Writes
   `site_names`.
5. Where to install (installer-level on Windows via Inno dir page, echoed in
   wizard; `--dir` on Linux, default `~/.local/share/jobscraper`): creates
   dir, copies `config.yaml`/`.env`, records data path.
6. Dependencies step (automatic, progress bar, no choices): Docker path
   (`compose up -d db`, wait healthy, `psql -f schema.sql` once) OR native
   path (Windows without Docker: `winget install PostgreSQL.PostgreSQL.16`, create
   `job_scraper` db + role, run `schema.sql`; Linux without Docker: reuse
   existing native-Postgres instructions from `README.md:2. Setup`);
   `playwright install chromium` (packaged builds prefetch at build time,
   wizard only verifies). Every failure shows plain-language fix + Retry.
7. Finish: "Open JobScraper" button (default browser), checkbox "Create
   desktop shortcut" (checked by default).

`setup.sh --check` dry-run validates `.env`/`config.yaml`/DB reachability and
exits nonzero with human-readable errors (mirrors `--check` convention).

## 5. Launchers, shortcuts, shutdown

- `run.sh` (Linux): `compose up -d` (or `--native` -> systemd/nohup path from
  `run_and_open.sh:26-52`) -> wait `GET /health` + web 200 (30s loop as today)
  -> `xdg-open` dashboard URL (default browser) -> done. Installs
  `JobScraper.desktop` (`Exec=run.sh`, icon, Terminal=false).
- Windows: Inno creates Start Menu `JobScraper` + optional Desktop shortcut ->
  `JobScraper.exe` (no console window); first launch without config opens the
  wizard, then the app in the default browser via `webbrowser.open()`.
  `stop.ps1` + in-app power button cover shutdown; uninstaller stops processes
  and optionally keeps/removes data dir (radio choice, default keep).
- `stop.sh` kept, extended to `compose down` when the compose stack is active;
  dashboard power button calls `POST /shutdown` with confirm modal, then both
  launchers and the button show the stopped state.

## 6. Releases

- New `.github/workflows/release.yml` on tag `v*`: build
  `dashboard/` (`npm ci && npm run build`); Windows job (`windows-latest`):
  PyInstaller onedir `JobScraper.exe` + `iscc installer/jobscraper.iss` ->
  `JobScraper-Setup-v*.exe` + `jobscraper-win64.zip` (exe dir + `setup`,
  `run/stop.ps1`); Linux job: `jobscraper-linux.tar.gz` (compose file,
  `run.sh`/`stop.sh`/`setup.sh`, `schema.sql`, `dashboard/dist`, no binaries);
  write `sha256sums.txt`; attach all to the GitHub Release. `installer/`
  dir holds `jobscraper.iss`, `setup_wizard.py`, `launcher.py`, icons.
- `.env.example` added (keys empty); real `.env` stays gitignored (it
  currently holds live keys — never package it).

## 7. Testing

- `setup.sh --check` + wizard validation unit checks (key format, ≥1 term,
  ≥1 board, writable dir).
- Fresh-install smoke per OS (VM or clean user): install -> wizard -> open in
  default browser -> Scrape 1 term -> Apply opens default-browser tab ->
  power button stops stack -> reinstall keeps data.
- Release CI builds both artifacts; checksum verify; `compose config` lint.

## 8. Rollout

Spec approval -> `writing-plans` -> implement in order: compose + launcher +
  admin/shutdown -> wizard + Inno -> release workflow -> README install
  rewrite (beginner path first, dev path second). No existing dev behavior
  removed.
