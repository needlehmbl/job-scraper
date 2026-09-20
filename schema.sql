CREATE TABLE IF NOT EXISTS jobs (
  id SERIAL PRIMARY KEY,
  source TEXT NOT NULL,           -- indeed | linkedin | jobstreet | glassdoor | google
  title TEXT NOT NULL,
  company TEXT NOT NULL,
  url TEXT UNIQUE NOT NULL,       -- dedupe key, mirrors current normalized-URL logic
  location TEXT,
  date_posted DATE,
  status TEXT NOT NULL DEFAULT 'NEW',  -- NEW/REVIEWED/APPLIED/SKIP/REJECTED/MISMATCH/EXP_GAP/EXPIRED
  scraped_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_at TIMESTAMPTZ,
  status_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()  -- last dashboard decision time
);

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