CREATE TABLE IF NOT EXISTS jobs (
  id SERIAL PRIMARY KEY,
  source TEXT NOT NULL,           -- indeed | linkedin | jobstreet | glassdoor | google
  title TEXT NOT NULL,
  company TEXT NOT NULL,
  url TEXT UNIQUE NOT NULL,       -- dedupe key, mirrors current normalized-URL logic
  location TEXT,
  date_posted DATE,
  description TEXT DEFAULT NULL,   -- full posting text: feeds the learner's
                                   -- desc-skill patterns (NULL for old rows)
  status TEXT NOT NULL DEFAULT 'NEW',  -- NEW/REVIEWED/APPLIED/SKIP/REJECTED/MISMATCH/EXP_GAP/EXPIRED/DUPLICATE
  scraped_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- first_seen
  last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), -- last scrape that still listed it
  applied_at TIMESTAMPTZ,
  status_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- last dashboard decision time
  score INT NOT NULL DEFAULT 0,        -- heuristic relevance 0-100 (score.py)
  score_reason TEXT NOT NULL DEFAULT '', -- one-line why (matched/missing skills)
  follow_up_at DATE DEFAULT NULL,      -- "ping if no response by" reminder
  stage TEXT DEFAULT NULL,             -- hiring-funnel stage (NULL = not in
                                       -- pipeline; invariant: set ⟺ APPLIED)
  offer_salary TEXT NOT NULL DEFAULT '',   -- offer details (Applications tab)
  offer_benefits TEXT NOT NULL DEFAULT '',
  offer_pros TEXT NOT NULL DEFAULT '',
  offer_cons TEXT NOT NULL DEFAULT ''
);

-- Stage-change audit trail for pipeline rows (mirrors job_status_history,
-- which only tracks status changes).
CREATE TABLE IF NOT EXISTS job_stage_history (
  id SERIAL PRIMARY KEY,
  job_id INT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  old_stage TEXT,
  new_stage TEXT,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS job_stage_history_job_id_idx ON job_stage_history(job_id);

-- Audit trail of every dashboard status change. Powers the
-- rejected/skipped/applied-over-time graph and the feedback learner
-- (scraper learns which titles/companies you reject/skip).
CREATE TABLE IF NOT EXISTS job_status_history (
  id SERIAL PRIMARY KEY,
  job_id INT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  old_status TEXT,
  new_status TEXT NOT NULL,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS job_status_history_job_id_idx ON job_status_history(job_id);
CREATE INDEX IF NOT EXISTS job_status_history_changed_at_idx ON job_status_history(changed_at);

CREATE TABLE IF NOT EXISTS scrape_runs (
  id SERIAL PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  new_jobs_count INT,
  summary TEXT
);

-- Postings the feedback filter held out of `jobs`, for review in the
-- dashboard's Filtered tab. Restoring moves a row into `jobs` as NEW
-- (filtered row kept with restored=TRUE as an audit trail).
CREATE TABLE IF NOT EXISTS filtered_jobs (
  id SERIAL PRIMARY KEY,
  source TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  company TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  location TEXT,
  date_posted DATE,
  description TEXT,
  search_term TEXT,
  filter_reason TEXT NOT NULL DEFAULT '',
  filtered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  restored BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE UNIQUE INDEX IF NOT EXISTS filtered_jobs_url_idx
  ON filtered_jobs (url) WHERE url <> '';
CREATE INDEX IF NOT EXISTS filtered_jobs_restored_idx ON filtered_jobs (restored);