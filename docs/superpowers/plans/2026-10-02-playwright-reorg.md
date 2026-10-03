# 2026-10-02 — Playwright package + source reorg + new browser scrapers

## Intent (agreed)
- Browser-gated boards (Jora, GrabJobs Cloudflare-walled; Kalibrr JS-heavy)
  get Playwright scrapers shaped exactly like `jobstreet.py`.
- All Playwright-exclusive scraping lives in `playwright/`.
- De-bloat root: group stray source modules into packages.
- Release (PyInstaller spec, tarball guard, workflows, Dockerfile) follows
  the move — no stale paths.

## Scope / non-goals
- No paid APIs, no Bossjob (prior ruling stands).
- No behavior change to filters, dedupe, dashboard, feedback learner.
- Kalibrr/Jora/GrabJobs selectors are best-effort v1 (verified live where
  possible); Cloudflare walls mean first runs may still 0-row and warn.

## Target layout (new packages, `__init__.py` re-exports keep old import
## paths working during transition, removed after green run)
- `playwright/__init__.py` — lazy re-exports (jobstreet, glassdoor, jora,
  kalibrr, grabjobs, common) so `import playwright.jobstreet` works and
  `import jobstreet` still works via shim.
- `playwright/common.py` — shared Chromium context helper (UA, locale,
  storage_state autodetect, `--disable-blink-features`, `--no-sandbox`),
  factored from JobStreetScraper.__enter__ (no behavior change).
- `playwright/jobstreet.py`, `playwright/glassdoor.py` — moved as-is except
  `from locations` → `from core.locations` (or relative) + storage_state
  path `Path(__file__).parent.parent / storage_state`.
- `playwright/jora.py`, `playwright/kalibrr.py`, `playwright/grabjobs.py` —
  new, same class shape: `XScraper` context manager + `scrape_x(terms,
  where, max_results, hours_old, headless, storage_state)` returning
  tracker columns + `site='<name>'`.
- `boards/` — `greenhouse.py`, `lever.py`, `ashby.py` (moved, import fix).
- `feeds/` — `trabajo.py`, `remoteok.py` (moved, import fix).
- Root shims (`jobstreet.py`, etc.) — thin re-export + DeprecationWarning,
  deleted in final task after tests green (keeps `git log --follow` clean
  via `git mv`).
- Core stays at root for this pass (`scraper.py`, `pipeline.py`,
  `locations.py`, `dedupe.py`, `manual_fetch.py`, …) — full `src/` move is
  out of scope and would churn the spec/tarball unnecessarily.

## Import map (all touched)
- `scraper.py`: `import jobstreet` → `from playwright import jobstreet,
  glassdoor, jora, kalibrr, grabjobs`; `import greenhouse/lever` →
  `from boards import …`; `import trabajo/remoteok` → `from feeds import …`.
- `pipeline.py`, `api/routes/scrape.py`, `backfill_details.py`, tests:
  same import updates; `site_from_url` needles unchanged (host-based).
- `setup_wizard.py` BOARDS += `jora`, `kalibrr`, `grabjobs`.
- `config.yaml` site_names += `jora`, `kalibrr`, `grabjobs` (default on;
  each block warns-and-continues on 0 rows so a wall never breaks a run).

## Release updates (must not be skipped)
- `installer/JobScraper.spec`: `pathex`, `hiddenimports` gain
  `playwright.jobstreet` etc. + `boards.*` + `feeds.*`; datas entries that
  reference moved files updated; keep `collect_all("tls_client")`.
- `installer/make-tarball.sh`: guard list uses new paths; `git ls-files`
  already picks up new dirs (no hand list), but guard must assert
  `playwright/jobstreet.py`, `boards/greenhouse.py`, `feeds/trabajo.py`, …
- `.github/workflows/release.yml`: no path hardcodes except
  `installer/jobscraper.iss` + `dist/JobScraper` (unchanged); verify
  Windows `pyinstaller installer/JobScraper.spec` step still resolves.
- `Dockerfile`: `COPY . .` covers new dirs; keep `playwright install
  --with-deps chromium`.

## Test plan (per task, TDD where new parsing exists)
- Moved modules: `pytest tests/ -q` stays 63 passed after each move.
- New scrapers: unit-test pure helpers (`_parse_listed`,
  `_relative_hours`, `_clean_url`, card extraction on saved HTML) without
  network; live probe is manual (`scrape_jora([...], max_results=5)`).
- Import check: `python -c "import scraper; print(scraper._SOURCE_WORKERS)"`.
- Live: RemoteOK/Ashby still return rows; new browser scrapers at least
  warn-and-empty (never raise) when walled.
- Tarball dry-run: `installer/make-tarball.sh /tmp/t.tgz` needs
  `dashboard/dist/index.html` — if missing, at least `bash -n` + guard-list
  eyeball; spec change eyeballed via `pyinstaller --noconfirm` only if
  toolchain present (else skip, note in commit).

## Task list (each = testable, commit-worthy)
1. `playwright/` package + move jobstreet/glassdoor (+ shims, import fix).
2. `boards/` + `feeds/` packages (+ shims, import fix).
3. New `playwright/jora.py` + `kalibrr.py` + `grabjobs.py` + wire into
   `scraper.py` blocks/config/wizard.
4. Release files (spec, tarball guard, workflow check, Dockerfile check).
5. Delete root shims, full verify, commit.
