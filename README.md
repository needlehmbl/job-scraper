# Job Search Scraper + Dashboard

Scrape pipeline + local web dashboard: scrapes Indeed, LinkedIn
(via JobSpy), JobStreet and Glassdoor (via Playwright/Chromium), plus
Greenhouse/Lever
company boards (public JSON APIs), for
junior/entry-level roles in Metro Manila, applies keyword +
years-of-experience filters, dedupes, and stores new leads in **Postgres**.
See [Job boards](#job-boards) for coverage notes and how to enable/disable
boards in `config.yaml`.
A **FastAPI** backend + **React/Tailwind** dashboard let you review and apply
to postings from a browser tab.

- New leads land in `jobs` (Postgres) with `status: NEW`; re-runs never
  duplicate rows.
- The dashboard lists postings, filters by status/source/date/text, and has an
  **Apply** button per row that opens the posting as a new tab in your
  existing browser.
- Checkbox column + bulk bar for setting one status on many rows at once.
- The scraper learns from your decisions (`SKIP` / `MISMATCH` /
  `EXP_GAP` vs `APPLIED` / `REVIEWED`) and drops lookalikes next run.
- A **Resumes** library (drag-drop `.docx`) plus a per-row **Tailor** button:
  previews a posting-tailored resume (skill add/drop suggestions included)
  as downloadable `.docx` — you still apply manually.
- `applications.xlsx` support is kept behind `--legacy-xlsx` until you've
  confirmed the Postgres path on a few real runs.

## 1. Architecture

```
venv/bin/python main.py          scrape -> Postgres (jobs, scrape_runs)
pipeline.py                     shared scrape pipeline (CLI + API button use the same code)
greenhouse.py / lever.py          direct company-board scrapers (public JSON APIs, no browser) — slugs in `company_boards`
jobstreet.py / glassdoor.py       Playwright/Chromium scrapers (no jobspy provider / bot-walled API) — locations in `search.location` / `glassdoor_locations`
filtered_jobs (Postgres)          postings the auto-filter held out, with per-row reason — reviewed in the dashboard's Filtered tab
api/routes/filtered.py            GET /filtered, POST /filtered/{id}/restore, DELETE /filtered/{id}
job-dashboard-api.service         systemd user unit: FastAPI on :8000 (GET /jobs, PATCH, POST /jobs/{id}/apply, /stats, /runs/latest, POST /scrape, GET /scrape/status)
job-dashboard-web.service         systemd user unit: Vite + React + Tailwind dev server on :5173
dashboard/                        Vite + React + Tailwind dev server on :5173
run_and_open.sh                   ensure both servers, open the dashboard (open-only; scraping lives in the dashboard's Scrape button)
stop.sh                           stop the API/dashboard + any leftover browser processes
apply_helper.py                   opens a job URL in a real browser and prefills form fields
notifier.py                       tails runs.log -> desktop notification (unchanged)
extract_resume.py                 Harvard-style .docx parser -> structured resume bank YAML
resumes.py / tailor.py / render_resume.py   resume library, per-posting LLM tailoring, .docx rendering
```

The API and dashboard run as **systemd user units**
(`job-dashboard-api.service`, `job-dashboard-web.service`), so they start at
login, survive tab reloads, and auto-restart if they crash. `git` this repo
does not contain them; the unit files live in `~/.config/systemd/user/`.

## 2. Setup

```bash
# Postgres (native, no Docker)
sudo pacman -S postgresql              # Arch/Omarchy
sudo -u postgres initdb -D /var/lib/postgres/data
sudo systemctl enable --now postgresql
sudo -u postgres psql -c "CREATE ROLE <your_os_username> LOGIN SUPERUSER;"   # your OS user
createdb job_scraper
psql -d job_scraper -f schema.sql      # creates jobs + scrape_runs

# Python deps
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium            # JobStreet scraping + apply_helper.py

# Dashboard deps
cd dashboard && npm install && cd ..
```

`db.py` connects over the local socket as your OS user (peer auth); override
with the `DATABASE_URL` env var if you ever point it elsewhere.
New columns (`score`, `last_seen`, `follow_up_at`, …) migrate
automatically via `ensure_tracking_schema()` on the next scrape or API
start — no manual `ALTER TABLE` needed.

## 3. Run it

```bash
./run_and_open.sh                      # ensure servers are up + open dashboard (no scraping)
```

Scraping is done from the dashboard: open `http://localhost:5173` and hit
the **Scrape new jobs** button in the header. It runs the exact same pipeline
as `main.py` (via `pipeline.py`) in the background — the button shows
`Scraping…` while it runs, you can keep reviewing in the meantime, and the
table refreshes automatically with a `+N new` note when it finishes. Starting
a second scrape while one is running returns `409` and the button waits for
the in-flight run instead.

Or run the pieces by hand if you'd rather not use systemd:

```bash
venv/bin/python main.py                # scrape into Postgres (add --legacy-xlsx to mirror to xlsx)
systemctl --user start job-dashboard-api job-dashboard-web   # or uvicorn / npm run dev directly
```

`run_and_open.sh` is open-only: it ensures the API/dashboard servers are up (it starts the
systemd units if they're stopped, falling back to plain `nohup` processes if
systemd is unavailable), then opens `http://localhost:5173` in your default
browser. To scrape from a terminal instead of the dashboard button, run
`venv/bin/python main.py` directly (add `--legacy-xlsx` to also mirror new
rows to `applications.xlsx`, or pass `{"legacy_xlsx": true}` to `POST
/scrape`); running it repeatedly is safe because duplicates are skipped.

To shut everything down (including any stray `apply_helper.py` / Playwright
browser left over from manual CLI use):

```bash
./stop.sh          # -> systemctl --user stop job-dashboard-api job-dashboard-web
```

Both units are `Restart=always` services, so `kill`ing the processes by hand
won't stick — use `stop.sh`, which stops the units themselves.

### Scheduled runs (removed)

The old `job-auto-apply.timer` (Mon/Wed/Fri 09:00) has been **disabled and
unarmed** per request — no scheduled fires. `run_and_open.sh` is the manual
entry point for opening the dashboard in a browser tab (scraping now lives
behind the dashboard's **Scrape new jobs** button). The
timer/notifier unit files still exist under `~/.config/systemd/user/` if you
want to re-enable or repurpose them later.

The `job-notifier.service` desktop-notification daemon is unchanged and still
tails `runs.log`.

## 4. Review and apply

Open the dashboard and check the **NEW** rows. The **Jobs** / **Filtered
for review** tabs sit above the table:

- **Jobs tab** — the main table. The **Scraped** column shows when each
  posting entered the tracker (date + `xh ago`); click its header to sort
  newest/oldest, so a fresh scrape is easy to tell apart from older rows.
  The second line under each date is `seen …` (last scrape that still
  listed it) plus a `stale` tag when a posting hasn't been re-seen for
  30+ days — its link may be dead, check before applying.
- **Fit column** — heuristic relevance 0–100, sorted best-first by
  default (click the header to flip/clear). `+` = posting skills found on
  your resume, `missing` = posting asks for what you don't list,
  entry-level postings get a small bonus, and titles matching your past
  reject patterns lose points. Hover any score for the one-line
  breakdown. Computed locally from `resume_bank.yaml` (or your default
  uploaded resume) at scrape time — zero LLM cost. Title-only for older
  rows (the tracker stores no descriptions); new scrapes score with the
  full posting text.
- **Follow-up column** — date picker per row ("ping if no response by").
  Overdue dates highlight red; the `◌ Due follow-ups (n)` chip above the
  table filters to just those. Cleared by picking an empty date.
- **Filtered tab** — every posting the feedback learner held out of a
  scrape, newest first, with the exact reason (`title-keyword:…`,
  `location:…`, `company:…`, `ai:…`), the search term that found it, and an
  expandable copy of the posting text. **Restore** moves one back into Jobs
  as `NEW` so you can apply; **Dismiss** drops it permanently (a future
  scrape can still hold the same URL again if it keeps matching). Only
  scrapes run after this feature was added populate the tab — older
  filtered-out postings were never stored and can't be recovered.

Use the **Scrape new jobs** button in the header whenever you want fresh postings — no need to run
`main.py` from a terminal; duplicates are skipped, so
re-scraping is always safe. After a scrape the header note breaks down the
auto-filter, e.g. `+12 new (300 checked, 24 auto-filtered
(location:cebu×8, title-keyword:senior×5), 24 held for review in the Filtered tab)` —
the top drop reasons from the feedback learner (see below).

- **Status** badge is a dropdown — set `REVIEWED` / `SKIP` /
  `MISMATCH` / `EXP_GAP` / `EXPIRED` / `DUPLICATE` directly (`MISMATCH` = wrong role or
  fit, `EXP_GAP` = needs more experience than you have, `EXPIRED` = dead
  link, `DUPLICATE` = repeat posting of a row you're already tracking — see below). `APPLIED`
  is reachable only through the post-Apply confirm strip (below), and employer
  cuts are tracked as the `OUT` stage in the Applications tab — there is no
  `REJECTED` status anymore. Every change is
  also recorded in `job_status_history` with a timestamp, which powers the
  outcome graph and the scraper's feedback learner.
- **Apply** button opens the job URL in a new tab of your existing browser
  (`window.open`) and persists the row as `REVIEWED` (so the change survives
  a tab reload). A confirm strip then appears under the row: **Yes,
  applied ✓** moves it to the Applications tab, or pick a no-apply reason
  (`SKIP` / `EXP_GAP` / `MISMATCH` / `EXPIRED`) — so `APPLIED` always
  means you really submitted. (`apply_helper` never files anything for
  you — some ATS platforms detect automation.)
- **Bulk editing:** tick the checkboxes (header box selects all filtered
  rows) and a bulk bar appears — pick a status once, apply it to the whole
  batch. Useful for triaging a fresh scrape.
- **Hiring funnel:** `APPLIED` rows move out of Jobs into the
  **Applications** tab, where a separate `stage` is tracked: `APPLIED →
  INITIAL → TECHNICAL → FINAL → OFFER` (or `OUT` when the employer cuts
  you, `ACCEPTED` / `DECLINED` when you accept or turn down an offer).
  The tab has All / Interviews / Offers chips
  plus its own filter; each row expands to a status+stage timeline (so you
  can see which round preceded a cut) and, for offer stages, salary /
  benefits / pros / cons notes for comparing offers.   `↩ Move back to
  Jobs` sends a row back to triage as `REVIEWED`. Triage `status` and
  pipeline `stage` are separate axes — the Jobs status dropdown stays at 7
  values (`APPLIED` is reachable only through the post-Apply confirm), and
  stages never train the feedback learner, with one refinement: `APPLIED`
  rows sitting at the `OUT` stage are excluded from the GOOD side too, so
  a cut never teaches the filter that the role type is desirable
  (interview outcomes say how the process went, not whether the role was
  relevant).
- **Theme:** neutral black/gray chrome in light mode, full dark mode via
  the sun/moon button in the header (follows your OS preference on first
  visit, remembered after). Status pills, source badges, and graph lines
  keep their colors since those encode meaning.
- **Search** filters live as you type (title/company substring,
  case-insensitive) with a mini syntax: `"exact phrase"` keeps words
  together, `a, b` / `a | b` / `a OR b` match ANY alternative,
  space-separated words must ALL match, and `title:` / `company:`
  restrict the whole query (e.g. `title:python backend, jr | junior`).
  First matching term is highlighted in the table.
- **Negative filter-out tags:** the `✕ SKIP (n)` /
  `✕ MISMATCH (n)` / `✕ EXP_GAP (n)` / `✕ EXPIRED (n)` / `✕ DUPLICATE (n)` pills
  next to the status dropdown hide those postings from the table (counts
  shown). Picking an explicit status in the dropdown overrides them.
- **Duplicates:** dedupe is URL-based, so the same role reposted under a
  new link (or cross-posted across boards) still lands as separate rows —
  e.g. several identical `Application Support Engineer @ Accenture` rows
  with different Indeed/LinkedIn IDs. The **Possible duplicates** panel
  above the table groups these automatically (same normalized title +
  company, so `Accenture` and `Accenture in the Philippines` merge): open
  it, tick the rows worth keeping per group (the default tick is a row
  you already acted on, else the earliest scraped), and one click marks
  the unticked ones `DUPLICATE`. Grouping is only a suggestion — nothing changes
  until you confirm. Manual marking still works too (bulk-select works
  for this). Hide it all with the `✕ DUPLICATE` pill. Like `EXPIRED`,
  `DUPLICATE` archives the row and never trains the learner.

`apply_helper.py` is unchanged in purpose but is no longer triggered by the
dashboard (Apply now just opens a new tab). It's still there for manual CLI
use and debugging when you want field prefill:

```bash
python apply_helper.py "https://ph.indeed.com/viewjob?jk=..." "my_resume.docx"
```

The resume path is optional; without it, text fields get prefilled but no
file upload is attempted.

## API

- `GET /jobs` — query params: `status`, `source`, `date_from`, `date_to`,
  `search` (title/company substring), `sort` (`score` default desc |
  `scraped` | `posted`) + `direction` (`asc` to flip).
- `PATCH /jobs/{id}` — `{"status": "..."}`; sets `applied_at` when
  `APPLIED`, clears it when moving to any other status. Entering
  `APPLIED` starts the funnel (`stage: APPLIED`); leaving it clears the
  stage.
- `PATCH /jobs/{id}/stage` — `{"stage": "TECHNICAL"}` advances the funnel
  (`APPLIED|INITIAL|TECHNICAL|FINAL|OFFER|ACCEPTED|DECLINED|OUT`); on a
  non-`APPLIED` row it flips status to `APPLIED` too.
- `PATCH /jobs/{id}/followup` — `{"follow_up_at": "2026-09-28"}` (or
  `null` to clear) sets the ping-if-no-response reminder.
- `POST /jobs/{id}/apply` — resolves the job's URL (the dashboard opens it
  in a new tab; no browser automation).
- `GET /stats` — counts by status/source, new-this-week, per-outcome this-week
  counters, plus `applied_series`, `rejected_series`, `skipped_series`,
  `mismatch_series`, `expgap_series` and a
   merged `outcome_series` (`[{date, applied, rejected, skipped, mismatch,
   expgap}]`, one point
   per day) for the graph.
- `PATCH /jobs/{id}/offer` — save offer details (`offer_salary`,
  `offer_benefits`, `offer_pros`, `offer_cons`; only sent fields change).
- `GET /jobs/{id}/history` — status + stage timeline for one posting
  (shows which funnel stage a rejection came after).
- `GET /runs/latest` — most recent `scrape_runs` row.
- `POST /scrape` — start a scrape in the background (same code as
  `venv/bin/python main.py`); `409` if one is already running. Optional body
  `{"legacy_xlsx": true}` mirrors to `applications.xlsx`.
- `GET /scrape/status` — `idle | running | done | error` plus `added` /
  `scraped` counts, the run `summary`, `filtered_saved` (rows held in the
  Filtered tab), the feedback breakdown
  (`filtered`, `filter_reasons`), and source health (`source_stats` per
  board plus `warnings`, e.g. `linkedin: 0 rows across 16 searches` when a
  site layout likely changed); the dashboard polls this while the
  **Scrape new jobs** button shows `Scraping…`, and warnings are appended
  to the header note plus the `runs.log` line (so the desktop notifier
  shows them).
- `GET /filtered` — held-out postings, newest first
  (`?include_restored=true` keeps restored ones too).
- `POST /filtered/{id}/restore` — move a held posting back into `jobs`
  as `NEW` (filtered row kept with `restored: true` as an audit trail).
- `DELETE /filtered/{id}` — dismiss a held posting permanently.
- `GET /resumes` — uploaded resume library + default; `POST
  /resumes/upload` (multipart `.docx`, parsed server-side);
  `POST /resumes/default`; `DELETE /resumes/{name}`.
- `POST /jobs/{id}/tailor` — `{"resume"?, "job_text"}` runs the LLM
  tailoring + heuristic skill-gap and returns preview JSON (one AI call,
  separate `tailor` daily budget; nothing saved).
- `POST /jobs/{id}/tailor/download` — `{"resume"?, "tailored"}` renders
  the approved preview to `.docx` for download.
- Interactive docs at `http://127.0.0.1:8000/docs`.

## Feedback learner (scraper learns from your decisions)

Marking postings `SKIP` / `MISMATCH` / `EXP_GAP`
(vs `APPLIED` / `REVIEWED`) teaches the
next scrape what to drop, via `feedback.py` (see `config.yaml` → `feedback:`).
`NEW`, `EXPIRED` and `DUPLICATE` never train the learner, and neither do
`APPLIED` rows sitting at the `OUT` funnel stage (a cut says how the
process went, not whether the role was relevant).

- **Heuristic (no API needed):** once you have `min_samples` decided jobs
  (default 10), title tokens, two-word phrases, and companies you
  overwhelmingly reject (e.g. a staffing
  firm you always skip) are auto-excluded from new scrapes. `SKIP` means
  "not relevant" — and so do the
  finer-grained `MISMATCH` (wrong fit) and `EXP_GAP` (needs more
  experience). You don't have to be explicit beyond the status: every
  scraped posting's description is stored, and canonical stack skills
  found in rejected/skipped postings (`learn_desc_skills`, default on)
  are auto-excluded too — so a generic "Associate Engineer" title hiding
  a C# description gets caught from your skips alone, and the learned
  reason shows as `desc-skill:C#` in the Filtered tab. Thresholds
  (`min_hits`, `min_reject_rate`, `min_phrase_hits`, ...) are tunable;
  set `enabled: false` to turn off.
- **Restoring teaches too:** a Restore puts the posting back as `NEW`,
  which the learner ignores — so after restoring, mark it
  `REVIEWED`/`APPLIED` in Jobs. Those GOOD examples dilute the reject rate
  of the token that banned it, which is what stops the filter from holding
  its lookalikes next run.
- **AI (needs a key in `.env`):** when `use_ai` is true and a key exists
  (`GROQ_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, or
  `GEMINI_API_KEY` — auto-picked in that order, or pin one with
  `ai_provider`), the new batch is checked against GOOD/BAD examples by
  an LLM and matching postings are dropped. Each posting is judged with
  title + company + truncated description (`ai_desc_chars`, default
  1000), plus your resume's skill list and the learned reject signals as
  prompt context. Any failure degrades to
  heuristic-only; the learner never breaks a scrape.
- **Cost:** the batch is scored in chunks of `ai_jobs_per_call`
  postings (default 40, one LLM call each), up to
  `ai_max_chunks_per_scrape` chunks per scrape (default 3) — roughly
  ~11k input tokens per call, ~22k for a typical two-chunk scrape.
  Effectively $0 on the free tiers (Groq / OpenRouter free models /
  Gemini Flash), and the daily caps below are the hard guardrail.
- **Failover:** chunks try providers in preference order and fail over —
  if one chokes (rate limit even after backoff, 5xx, dead model ID),
  it's cooled for the rest of the run and the next provider picks the
  chunk up, so no single outage kills the run. (Parallel calls would
  spend shared rate-limit quota faster, not slower — sequential +
  failover is the deliberate shape.)
- **Rate limiter:** each scoring call is spaced by
  `min_seconds_between_calls` and counted against daily
  `max_ai_calls_per_day` / `max_ai_tokens_per_day` budgets tracked in
  `usage_state.json` (gitignored, resets daily). Over budget → the scrape
  continues heuristic-only with a log line.
- **Model IDs churn:** the Groq default is `openai/gpt-oss-20b` (both old
  `llama-*` defaults were decommissioned Aug 2026 — every AI call 404ed
  until the switch). If a provider starts 404ing, check its model catalog
  and override via `feedback.ai_model` in `config.yaml`.

## Resume tailoring (per-posting, on demand)

The dashboard's **Resumes** panel accepts drag-dropped `.docx` files
(stored gitignored under `resumes/` with an extracted bank sidecar each;
radio button picks the default). Every job row has a **Tailor** button:

1. Paste the posting description (the tracker stores titles only, so one
   paste from the listing is needed — prefilled with title/company).
2. **Run tailoring** — one LLM call (separate `tailor_*` daily budget in
   `config.yaml` → `feedback:`) selects/reorders/lightly rewords bullets
   from *your* bank. Contact details are stripped from the payload; facts
   (companies, titles, dates, schools) must be echoed exactly, never
   invented. A heuristic skill-gap runs alongside (no LLM): posting skills
   missing from your bank ("consider adding") vs bank items with zero
   posting overlap ("consider dropping").
3. Review the preview (summary, add/drop lists, reordered skills, bullets
   with dates), then **Download tailored .docx** and apply manually from
   the job link. Nothing auto-applies, ever.

Re-import a resume anytime with `venv/bin/python extract_resume.py
resume.docx` — the parser understands Harvard-style layouts (centered
section headings, bold-lead company/title/school lines, tab-spaced dates,
labeled skill groups) and keeps anything unrecognized under
`extra_sections` instead of dropping it.

This is a local single-user tool: no auth, no cloud deployment. CORS is
limited to localhost origins.

## Troubleshooting: 400 errors from the scraper

Two jobspy quirks cause most 400s:

1. **`country_indeed` is required for Indeed and defaults to
   `'USA'`.** If you're searching a non-US location and this isn't set in
   `config.yaml`'s `search.country_indeed`, Indeed will 400 (or silently
   search the wrong country). Must match jobspy's exact spelling.
2. **jobspy's Indeed integration rejects combining `is_remote` with
   `hours_old` in the same call.** `scraper.py` handles this: it only
   sends `is_remote` when it's `true`; for onsite/hybrid searches
   (`is_remote: false`) it omits the field and relies on `hours_old`
   instead.

Since `is_remote: false` isn't sent to the API as a filter, some remote-only
postings may slip through. Add `"remote"` to `search.exclude_title_keywords`
if you want those dropped by title.

If you still hit a 400 after both fixes, run
`venv/bin/python scraper.py` directly to see the raw error, and check
jobspy's GitHub issues for your exact site — its scrapers reverse-engineer
LinkedIn/Indeed's internal APIs, so they occasionally break when those
sites change something.

## Job boards

Current default (`config.yaml` → `search.site_names`): `indeed`,
`linkedin`, `google` (JobSpy), plus `jobstreet` and `glassdoor` (custom
Playwright scrapers in `jobstreet.py` / `glassdoor.py`, since JobSpy has
no JobStreet provider and its Glassdoor integration is bot-walled).

Adding/removing boards is a one-line config change. `scraper.py` runs one
JobSpy call **per site per term**, so a single failing board can't poison
the others. It builds a per-term
`google_search_term` when `google` is enabled, since Google Jobs filters
only via that parameter. JobSpy supports `linkedin`, `indeed`,
`glassdoor`, `google`, `zip_recruiter`, `bayt`, `naukri`, `bdjobs`.

Coverage notes for a Metro Manila search (verified live 2026-09-21,
jobspy 1.1.82):

- `glassdoor`: covered by a dedicated Playwright scraper
  (`glassdoor.py`), since jobspy's Glassdoor integration is bot-walled
  (no PH domain + 403 on its location API, verified 2026-09-21). Passive
  result-page loads from a home IP pass; the module never touches the
  search form (that triggers a challenge) and resolves locations via
  numeric IDs in `glassdoor_locations` (copy the IC number from any
  browser search URL). One page (30 cards) per term per location with a
  cooldown between loads; cards carry no usable age so these skip the
  `hours_old` gate like company boards, and all other filters apply.
- `google`: global aggregator, sometimes finds PH SMBs Indeed misses, but
  currently returns **zero rows** (JobSpy's Google parser appears
  broken upstream — the per-source warnings added to the scrape note
  will flag `google: 0 rows` if that persists, same as any board whose
  layout changes).
- Skipped by default: `zip_recruiter` (US/CA only), `bayt` / `naukri` /
  `bdjobs` (Middle East / India / Bangladesh focus).

Redundancy: safe but not free. Exact re-scrapes are skipped (normalized
`job_url` dedupe in-run in `scraper.py`, `UNIQUE(url)` + title/company
fallback in `db.py`), so extra boards never duplicate rows. But the same
role cross-posted on two boards has different URLs and will show up twice
(same as Indeed+LinkedIn overlap today) — Google Jobs increases this most
since it aggregates other boards. Each board also adds scrape time and a
little more 400/ban risk from JobSpy's reverse-engineered APIs.

Higher-signal than more JobSpy boards for junior Manila tech roles: wire up
`config.yaml`'s `company_boards` section — Greenhouse (`boards.greenhouse.io/<slug>`
via `boards-api.greenhouse.io`) and Lever (`lever.co/<slug>` via
`api.lever.co`) expose public JSON, stabler than scraping. Seeded and
validated 2026-09-20: `moneysmart` + `perform-careers` (Greenhouse),
`coins` = Coins.ph in Taguig (Lever). Board pulls ignore `hours_old`
(career pages list evergreen postings; dedupe keeps re-scrapes clean) and
use a looser location gate (bare `Philippines` / remote-PH passes; named
non-NCR cities still fail) — the full keyword + experience + learner
filters still apply downstream. For fully custom career pages, add
PH-specific Playwright scrapers in the style of `jobstreet.py` (e.g.
Kalibrr, Bossjob).

## Notes / limits

- The years filter is heuristic (text parsing). A posting that doesn't
  mention years is kept; one that says "20 years in business" near the
  word "experience" could get dropped. Review rows in the dashboard as
  needed.
- Title relevance is gated by `search.include_title_keywords` in
  `config.yaml` (a posting whose title contains none of them is dropped),
  because boards — JobStreet most of all — return loosely-related results
  for broad terms. Borderline keepers (e.g. BPO chat/voice support roles
  matching "support") still come through; SKIP them once and the feedback
  learner will filter their kind next run.
- `apply_helper.py` fills forms, it never clicks final submit — some ATS
  platforms (Workday especially) actively detect and block automation, so
  keep this manual step.
- `applications.xlsx` is still written when you pass `--legacy-xlsx`; the
  Postgres `jobs` table is the new source of truth and the dashboard reads
  only Postgres.
- On-demand per-posting tailoring lives in `tailor.py` + `resumes.py` +
  `render_resume.py` (see [Resume tailoring](#resume-tailoring)). The old
  batch pipeline under `.archive-tailoring/` stays archived — nothing
  imports it.
- Greenhouse/Lever/company-board direct scraping isn't wired up yet
  (`config.yaml`'s `company_boards` section is a placeholder) — those
  boards expose stabler JSON endpoints than LinkedIn/Indeed scraping if you
  want that added.
