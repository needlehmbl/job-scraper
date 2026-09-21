"""
Feedback learner: the scraper learns from your dashboard decisions.

Every time you mark a posting REJECTED / SKIP / MISMATCH / EXP_GAP
(vs APPLIED / REVIEWED) in the
dashboard, that decision is stored in Postgres (`jobs.status` +
`job_status_history`). On the next scrape, this module reads those decisions
back and filters out new postings that look like the ones you rejected.

Statuses outside GOOD/BAD -- NEW (undecided), EXPIRED (dead link, not
a relevance judgment) and DUPLICATE (repeat posting, not a relevance
judgment) -- are never used for learning, so archiving an
expired posting as EXPIRED (or a repeat as DUPLICATE) can never teach
the filter to ban its title. Pipeline progress lives in a separate
`stage` column (INITIAL / TECHNICAL / …), not in `status`, so it never
touches learning either: outcomes say how the process went, not whether
the role was relevant.

Two layers, cheapest first:

1. Heuristic (always on, no API needed): learns from the location field
   first (cities you always skip), then from title/company words -- but a
   skip already explained by a bad location is NOT blamed on its title,
   so skipping "Junior Developer (Cebu)" for location can never teach
   the filter to ban "developer".

2. AI (only when an API key is present): sends a compact summary of past
   GOOD vs BAD decisions plus the new batch -- title, company AND a
   truncated description per posting, plus your resume's skill list and
   the learned reject signals -- to an LLM and drops whatever
   the model flags as matching your reject patterns. Supports the keys
   already in `.env` -- GROQ, OpenRouter, Mistral, Gemini -- picked in
   that order unless `feedback.ai_provider` pins one. Providers fail
   over: a choked provider is cooled for the run and the next in the
   chain picks the chunk up. Any failure (no DB,
   no keys, API error, bad JSON) degrades gracefully to heuristic-only;
   this module NEVER raises into the scrape pipeline.
"""
import json
import os
import re
import threading
import time
from collections import Counter
from datetime import date, datetime

GOOD = ("APPLIED", "REVIEWED")
# All negative dashboard decisions. SKIP is generic, MISMATCH means wrong
# role/field/city fit, EXP_GAP means the posting needs more experience
# than you have -- all three count exactly like REJECTED for learning.
BAD = ("REJECTED", "SKIP", "MISMATCH", "EXP_GAP")

_TOKEN_PAT = re.compile(r"[a-z0-9+#.]+")
_STOPWORDS = frozenset({
    "and", "the", "for", "with", "hiring", "wanted", "urgent", "role",
    "job", "jobs", "position", "jr", "sr", "i", "ii", "iii", "iv",
    "a", "an", "of", "in", "on", "to", "new",
})

_DEFAULTS = {
    "enabled": True,
    "min_samples": 10,       # decided jobs needed before learning kicks in
    # SKIP and REJECTED count equally as "bad" everywhere below: a skip
    # means "not relevant to my job search", same weight as a rejection.
    # MISMATCH (wrong fit) and EXP_GAP (needs more experience) are also
    # bad -- finer-grained reasons, same learning weight.
    "min_hits": 2,           # token must appear in this many decided jobs
    "min_reject_rate": 0.8,  # ... with this fraction rejected/skipped
    "min_phrase_hits": 2,    # same idea for two-word phrases
    "auto_exclude_companies": True,
    "min_company_hits": 2,
    "min_company_reject_rate": 0.75,
    "use_ai": True,
    "ai_provider": "auto",   # auto|groq|openrouter|mistral|gemini|off
    "ai_model": "",
    "max_jobs_per_ai_call": 60,
    "max_examples_per_side": 30,
    # Description context per posting in the AI filter prompt (chars,
    # whitespace-collapsed). ~1000 chars ≈ 250 tokens.
    "ai_desc_chars": 1000,
    # Batch chunking: the filter scores the batch in chunks of this many
    # postings (one LLM call each), up to ai_max_chunks_per_scrape chunks
    # per scrape -- the remainder is heuristic-only (auto-KEEP).
    "ai_jobs_per_call": 40,
    "ai_max_chunks_per_scrape": 3,
    # Rate limiter for AI scoring (protects free-tier quotas). At most one
    # scoring call happens per scrape, so these are daily guardrails.
    "min_seconds_between_calls": 2,
    "max_ai_calls_per_day": 20,
    "max_ai_tokens_per_day": 100000,
    "usage_state_path": "usage_state.json",
    # Separate daily budget for on-demand tailoring calls (dashboard
    # "Tailor" button). Tracked under its own usage entry so tailoring
    # never eats the scrape filter's budget and vice versa.
    "tailor_max_calls_per_day": 10,
    "tailor_max_tokens_per_day": 60000,
}


def _cfg(cfg: dict) -> dict:
    merged = dict(_DEFAULTS)
    merged.update((cfg or {}).get("feedback", {}) or {})
    return merged


def _load_env_file(path=".env") -> dict:
    """Parse KEY=VALUE lines from a dotenv file (no dependency needed)."""
    vals: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return vals


def _api_keys() -> dict[str, str]:
    file_vals = _load_env_file()
    keys = {}
    for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY"):
        val = os.environ.get(name) or file_vals.get(name)
        if val:
            keys[name] = val
    return keys


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_PAT.findall((text or "").lower()) if t not in _STOPWORDS]


def phrases(text: str) -> list[str]:
    """Adjacent token pairs ("power platform", "civil engineer") -- more
    precise than single tokens for skip patterns like niche stacks."""
    toks = tokens(text)
    return [f"{a} {b}" for a, b in zip(toks, toks[1:])]


def norm_title(text: str) -> str:
    return " ".join(tokens(text))


def load_decisions(conn=None) -> list[dict]:
    """Fetch decided jobs (title/company/location/status) from the dashboard DB."""
    import db

    own = conn is None
    if own:
        conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT title, company, location, status FROM jobs
                WHERE status = ANY(%s)
                ORDER BY id DESC LIMIT 2000
                """,
                (list(GOOD + BAD),),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()


def loc_tokens(text: str) -> list[str]:
    """Location tokens: split on non-alphanumerics, drop pure numbers and
    generic fillers so "Manila", "Cebu", "Makati" survive but "1015" doesn't."""
    toks = [t for t in _TOKEN_PAT.findall((text or "").lower())
            if t not in _STOPWORDS and not t.isdigit()
            and t not in {"city", "metro", "manila", "philippines", "ncr",
                          "hybrid", "onsite", "remote"}]
    return toks


def learn_patterns(decisions: list[dict], fc: dict) -> dict:
    """Derive reject patterns from past decisions.

    Returns {"keywords": [...], "phrases": [...], "companies": [...],
    "locations": [...], "n_good": int, "n_bad": int} -- empty lists when
    there isn't enough signal.

    Skips are attributed to the most specific cause first: a BAD job whose
    location already matches a learned-bad location is "explained" by
    location, so its title/company words are EXCLUDED from title/company
    learning. Without this, skipping e.g. "Junior Developer (Cebu)" for
    location would wrongly teach the filter to ban "junior"/"developer".
    """
    good = [d for d in decisions if d.get("status") in GOOD]
    bad = [d for d in decisions if d.get("status") in BAD]
    patterns: dict = {"keywords": [], "phrases": [], "companies": [],
                      "locations": [], "n_good": len(good), "n_bad": len(bad)}
    if len(decisions) < fc["min_samples"]:
        return patterns

    # Pass 1: location patterns (where do your skips cluster?).
    lhits: Counter = Counter()
    lbad: Counter = Counter()
    for d in decisions:
        toks = set(loc_tokens(d.get("location", "")))
        for t in toks:
            lhits[t] += 1
            if d.get("status") in BAD:
                lbad[t] += 1
    bad_locs = set()
    for tok, n in lhits.items():
        if n >= fc["min_hits"] and lbad[tok] / n >= fc["min_reject_rate"]:
            patterns["locations"].append(tok)
            bad_locs.add(tok)
    patterns["locations"].sort()

    def location_explained(d: dict) -> bool:
        return bool(set(loc_tokens(d.get("location", ""))) & bad_locs)

    # Pass 2: title/company patterns, ignoring BAD jobs whose skip is
    # already explained by location (their title words are innocent).
    teachable = [d for d in decisions
                 if d.get("status") in GOOD or not location_explained(d)]

    hits: Counter = Counter()
    bad_hits: Counter = Counter()
    for d in teachable:
        toks = set(tokens(d.get("title", "")))
        for t in toks:
            hits[t] += 1
            if d.get("status") in BAD:
                bad_hits[t] += 1
    for tok, n in hits.items():
        if n >= fc["min_hits"] and bad_hits[tok] / n >= fc["min_reject_rate"]:
            patterns["keywords"].append(tok)
    patterns["keywords"].sort()

    phits: Counter = Counter()
    pbad: Counter = Counter()
    for d in teachable:
        phrs = set(phrases(d.get("title", "")))
        for p in phrs:
            phits[p] += 1
            if d.get("status") in BAD:
                pbad[p] += 1
    for phr, n in phits.items():
        if n >= fc["min_phrase_hits"] and pbad[phr] / n >= fc["min_reject_rate"]:
            patterns["phrases"].append(phr)
    patterns["phrases"].sort()

    if fc["auto_exclude_companies"]:
        chits: Counter = Counter()
        cbad: Counter = Counter()
        for d in teachable:
            c = (d.get("company") or "").strip().lower()
            if not c:
                continue
            chits[c] += 1
            if d.get("status") in BAD:
                cbad[c] += 1
        for comp, n in chits.items():
            if n >= fc["min_company_hits"] and cbad[comp] / n >= fc["min_company_reject_rate"]:
                patterns["companies"].append(comp)
        patterns["companies"].sort()
    return patterns


def _cell(value) -> str:
    """Stringify a scraped DataFrame cell; NaN/NaT/None become ''."""
    if value is None:
        return ""
    try:
        import pandas as pd
        if value is pd.NaT or pd.isna(value):
            return ""
    except Exception:
        pass
    if isinstance(value, float):
        import math
        if math.isnan(value):
            return ""
    return str(value).strip()


def _norm_date(value):
    """Normalize a scraped date_posted cell to date/datetime/None (NaT-safe)."""
    if value is None:
        return None
    try:
        import pandas as pd
        if value is pd.NaT or pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()
    except Exception:
        pass
    if isinstance(value, (datetime, date)):
        return value
    s = str(value).strip()
    return s or None


def _dropped_row(row, reason: str) -> dict:
    """Serialize one filtered-out posting for review persistence.

    `row` is a pandas Series from the scraped batch; keys are defensive
    because JobSpy and JobStreet frames don't share every column.
    """
    return {
        "title": _cell(row.get("title")),
        "company": _cell(row.get("company")),
        "location": _cell(row.get("location")),
        "job_url": _cell(row.get("job_url")),
        "site": _cell(row.get("site")),
        "description": _cell(row.get("description")),
        "date_posted": _norm_date(row.get("date_posted")),
        "matched_search_term": _cell(row.get("matched_search_term")),
        "filter_reason": reason or "",
    }


def apply_heuristic(df, patterns: dict):
    """Drop rows matching learned keywords/phrases/companies/locations.
    Returns (kept_df, n_dropped, reasons, dropped_df) where dropped_df
    carries a per-row `filter_reason` column for review persistence."""
    if df is None or getattr(df, "empty", True):
        return df, 0, [], df.head(0) if df is not None else df
    if not patterns["keywords"] and not patterns.get("phrases") \
            and not patterns["companies"] and not patterns.get("locations"):
        return df, 0, [], df.head(0)
    kw = set(patterns["keywords"])
    phrs = set(patterns.get("phrases", []))
    comps = set(patterns["companies"])
    locs = set(patterns.get("locations", []))

    def bad_row(row) -> str | None:
        loc_hit = sorted(set(loc_tokens(row.get("location", ""))) & locs)
        if loc_hit:
            return f"location:{','.join(loc_hit)}"
        nt = norm_title(row.get("title", ""))
        title_toks = set(nt.split())
        hit = sorted(title_toks & kw)
        if hit:
            return f"title-keyword:{','.join(hit)}"
        phit = sorted(p for p in phrs if p in nt)
        if phit:
            return f"title-phrase:{'|'.join(phit)}"
        comp = str(row.get("company", "") or "").strip().lower()
        if comp and comp in comps:
            return f"company:{comp}"
        return None

    reasons = df.apply(bad_row, axis=1)
    mask = reasons.notna()
    dropped = df[mask].copy()
    dropped["filter_reason"] = reasons[mask].values
    return (df[~mask].reset_index(drop=True), int(mask.sum()),
            reasons[mask].tolist(), dropped.reset_index(drop=True))


# ---------------------------------------------------------------- AI layer

_PROVIDER_ORDER = (
    # NOTE (2026-09): Groq decommissioned llama-3.1-8b-instant and
    # llama-3.3-70b-versatile (Aug 2026). gpt-oss-20b is the current
    # cheap/fast production default. Override per-provider via
    # `feedback.ai_model` in config.yaml (applies to whichever provider
    # is picked) -- check https://console.groq.com/docs/models when a
    # provider starts 404ing; free-tier model IDs churn often.
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions",
     "openai/gpt-oss-20b"),
    ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions",
     "meta-llama/llama-3.1-8b-instruct:free"),
    ("mistral", "MISTRAL_API_KEY", "https://api.mistral.ai/v1/chat/completions",
     "mistral-small-latest"),
    ("gemini", "GEMINI_API_KEY", "", "gemini-2.0-flash"),
)


def _pick_providers(fc: dict, keys: dict) -> list[tuple]:
    """All usable providers in preference order -- the failover chain.

    When one provider chokes (429 even after backoff, 5xx, timeout,
    dead model ID), the caller moves to the next instead of failing.
    """
    want = (fc["ai_provider"] or "auto").lower()
    out = []
    for name, env, url, model in _PROVIDER_ORDER:
        if want not in ("auto", name):
            continue
        if env in keys:
            out.append((name, keys[env], url, fc["ai_model"] or model))
    return out


def _pick_provider(fc: dict, keys: dict) -> tuple | None:
    chain = _pick_providers(fc, keys)
    return chain[0] if chain else None


def _chat_with_failover(providers: list[tuple], system: str, user: str,
                        cooled: set) -> tuple[str, int | None, str, str]:
    """One logical LLM call with cross-provider failover.

    Tries each provider in preference order, skipping ones already
    cooled-down this run. A provider that fails (rate limit, server
    error, network, dead model) is cooled so later chunks in the same
    run don't waste time on it. Returns (reply, tokens, name, model);
    raises RuntimeError when every provider fails.
    """
    last_err = "no providers available"
    for name, key, url, model in providers:
        if name in cooled:
            continue
        try:
            if name == "gemini":
                reply, used = _chat_gemini(key, model, system, user)
            else:
                reply, used = _chat_openai_compatible(url, key, model,
                                                      system, user)
            return reply, used, name, model
        except Exception as e:
            cooled.add(name)
            print(f"[feedback] provider {name} failed ({e}); failing over.")
            last_err = f"{name}: {e}"
    raise RuntimeError(f"all AI providers failed ({last_err})")


def _chat_openai_compatible(url: str, key: str, model: str, system: str, user: str
                          ) -> tuple[str, int | None]:
    """Returns (reply_text, total_tokens or None when the provider omits usage)."""
    import requests

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if "openrouter" in url:
        headers["HTTP-Referer"] = "http://localhost:5173"
        headers["X-Title"] = "job-auto-apply feedback filter"
    resp = requests.post(
        url,
        headers=headers,
        json={"model": model, "temperature": 0,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
        timeout=30,
    )
    if resp.status_code == 429:
        # Free-tier rate limit: honor the server's backoff once instead of
        # surfacing an instant failure (bounded wait so a user-clicked
        # button never hangs for minutes).
        try:
            wait = float(resp.headers.get("retry-after", "5"))
        except ValueError:
            wait = 5.0
        wait = min(max(wait, 1.0), 30.0)
        print(f"[feedback] rate-limited (429), waiting {wait:.0f}s then retrying once.")
        time.sleep(wait)
        resp = requests.post(
            url,
            headers=headers,
            json={"model": model, "temperature": 0,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=30,
        )
    resp.raise_for_status()
    data = resp.json()
    total = (data.get("usage") or {}).get("total_tokens")
    msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
    content = msg.get("content") or ""
    if isinstance(content, list):  # content-block style responses
        content = "".join(
            b.get("text", "") for b in content if isinstance(b, dict))
    return str(content), total


def _chat_gemini(key: str, model: str, system: str, user: str
                 ) -> tuple[str, int | None]:
    """Returns (reply_text, total_tokens or None when the provider omits usage)."""
    import requests

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
           f":generateContent?key={key}")
    resp = requests.post(
        url,
        json={"system_instruction": {"parts": [{"text": system}]},
              "contents": [{"parts": [{"text": user}]}],
              "generationConfig": {"temperature": 0}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    total = (data.get("usageMetadata") or {}).get("totalTokenCount")
    return data["candidates"][0]["content"]["parts"][0]["text"], total


def _parse_ai_decisions(text: str, n: int) -> dict[int, str]:
    """Extract {batch_index: 'SKIP'|'KEEP'} from a model reply (JSON array)."""
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        items = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    out: dict[int, str] = {}
    for it in items if isinstance(items, list) else []:
        try:
            i = int(it.get("i"))
            dec = str(it.get("decision", "")).upper()
            if 0 <= i < n and dec in ("KEEP", "SKIP"):
                out[i] = dec
        except (AttributeError, TypeError, ValueError):
            continue
    return out


# ------------------------------------------------- tokens + rate limiter

def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars/token for English text).

    Used to predict a scoring call's cost up front and to enforce the
    daily token budget when a provider doesn't return exact usage.
    """
    return max(1, len(text or "") // 4)


class _RateLimiter:
    """Min-interval throttle shared by all AI scoring calls in this process."""

    def __init__(self):
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self, min_interval: float):
        if not min_interval or min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._last + min_interval - now
            if wait > 0:
                print(f"[feedback] rate limit: waiting {wait:.1f}s between AI calls.")
                time.sleep(wait)
                now = time.monotonic()
            self._last = now


_LIMITER = _RateLimiter()


def _load_usage(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_usage(path: str, usage: dict):
    with open(path, "w") as f:
        json.dump(usage, f, indent=2)


def _usage_entry(usage: dict, name: str = "feedback_ai") -> dict:
    today = date.today().isoformat()
    entry = usage.get(name)
    if not entry or entry.get("date") != today:
        entry = {"date": today, "calls": 0, "tokens": 0}
        usage[name] = entry
    return entry


def _budget_allows(fc: dict, est_in_tokens: int) -> tuple[bool, str]:
    """Check the daily AI call/token caps. Returns (ok, reason).

    Fails open: a corrupt/unreadable state file must never block scoring.
    """
    try:
        entry = _usage_entry(_load_usage(fc["usage_state_path"]))
        max_calls = fc["max_ai_calls_per_day"] or 0
        if max_calls and entry["calls"] >= max_calls:
            return False, f"daily AI call budget reached ({entry['calls']}/{max_calls})"
        max_toks = fc["max_ai_tokens_per_day"] or 0
        if max_toks and entry["tokens"] + est_in_tokens > max_toks:
            return False, (f"daily AI token budget would be exceeded "
                           f"({entry['tokens']} used + ~{est_in_tokens} est > {max_toks})")
        return True, ""
    except Exception:
        return True, ""


def _record_usage(fc: dict, tokens_used: int) -> tuple[int, int]:
    """Add one call to today's persistent usage. Returns (calls, tokens)."""
    try:
        usage = _load_usage(fc["usage_state_path"])
        entry = _usage_entry(usage)
        entry["calls"] += 1
        entry["tokens"] += max(1, int(tokens_used))
        _save_usage(fc["usage_state_path"], usage)
        return entry["calls"], entry["tokens"]
    except Exception as e:
        print(f"[feedback] WARNING: could not record AI usage: {e}")
        return 0, 0


def _desc_snippet(value, limit: int) -> str:
    """Posting description collapsed to one line, truncated at a word
    boundary. Empty/missing descriptions become an explicit placeholder
    so the model tolerates short-snippet sources (JobStreet) gracefully."""
    s = re.sub(r"\s+", " ", _cell(value)).strip()
    limit = max(100, int(limit or 0))
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return s or "—"


def _resume_skill_line(max_skills: int = 25) -> str:
    """One-line resume context from the skill lexicon, so the filter can
    flag postings whose core stack has no overlap with the candidate."""
    try:
        import score as score_mod
        from tailor import SKILL_LEXICON
        bank_low = score_mod.load_bank_text()
        if not bank_low:
            return ""
        present = [s for s, aliases in SKILL_LEXICON.items()
                   if any(a in bank_low for a in aliases)]
        if not present:
            return ""
        return ("Candidate's current stack (from their resume): "
                + ", ".join(present[:max_skills]) + ".")
    except Exception:
        return ""


def _reject_hint_line(decisions: list[dict], fc: dict) -> str:
    """Compact restatement of the learned heuristic patterns, so the AI
    layer benefits from them instead of re-deriving them from examples."""
    try:
        patterns = learn_patterns(decisions, fc)
        bits = []
        if patterns.get("keywords"):
            bits.append("reject titles containing: "
                        + ", ".join(patterns["keywords"][:12]))
        if patterns.get("phrases"):
            bits.append("reject title phrases: "
                        + ", ".join(patterns["phrases"][:8]))
        if patterns.get("companies"):
            bits.append("reject companies: "
                        + ", ".join(patterns["companies"][:8]))
        if patterns.get("locations"):
            bits.append("reject locations: "
                        + ", ".join(patterns["locations"][:8]))
        if not bits:
            return ""
        return "Learned reject signals (" + " / ".join(bits) + ")."
    except Exception:
        return ""


def _build_scoring_prompt(decisions: list[dict], batch_rows: list[dict],
                          fc: dict) -> tuple[str, str]:
    """Build the (system, user) prompt for one AI scoring call.

    Each posting carries title + company + a truncated description, so
    verdicts use stack/seniority evidence instead of title vibes."""
    good = [d for d in decisions if d.get("status") in GOOD][:fc["max_examples_per_side"]]
    bad = [d for d in decisions if d.get("status") in BAD][:fc["max_examples_per_side"]]

    def fmt(d):
        return f"[{d.get('status', '')}] {d.get('title', '')} @ {d.get('company', '')}".strip()[:130]

    def fmt_row(i: int, r: dict) -> str:
        title = _cell(r.get("title"))[:120]
        company = _cell(r.get("company"))[:80]
        desc = _desc_snippet(r.get("description"), fc["ai_desc_chars"])
        return f"{i}: {title} @ {company}\n   {desc}"

    system = (
        "You filter job postings for a junior/entry-level software developer "
        "in Metro Manila, Philippines. The user reviews scraped postings and marks "
        "good ones APPLIED/REVIEWED and bad ones REJECTED/SKIP/MISMATCH/EXP_GAP. "
        "SKIP is a generic pass, MISMATCH means wrong role or fit, EXP_GAP means "
        "it needs more experience -- treat all three with exactly "
        "the same weight as REJECTED. Flag new postings that resemble the BAD ones "
        "(wrong seniority, wrong role family, wrong field, companies they avoid). "
        "Use the description's stack and seniority evidence, not just the title. "
        + _resume_skill_line() + " " + _reject_hint_line(decisions, fc)
        + " When unsure, KEEP. Reply ONLY with a JSON "
        "array like [{\"i\": 0, \"decision\": \"KEEP\"}, {\"i\": 1, \"decision\": \"SKIP\"}]."
    )
    lines = [fmt_row(i, r) for i, r in enumerate(batch_rows)]
    user = (
        "GOOD examples (user applied/reviewed):\n"
        + "\n".join(f"- {fmt(d)}" for d in good[:20])
        + "\n\nBAD examples (user rejected/skipped):\n"
        + "\n".join(f"- {fmt(d)}" for d in bad[:30])
        + "\n\nNEW postings to judge (title @ company, then description):\n"
        + "\n".join(lines)
    )
    return system, user


def ai_filter(df, decisions: list[dict], fc: dict, keys: dict):
    """Ask an LLM which new postings match your reject patterns.

    The batch is scored in chunks (one LLM call per chunk, capped per
    scrape), each with title + description evidence. Chunks fail over
    across providers; a dead chunk is kept, never dropped. Returns
    (kept_df, n_dropped, provider_names, dropped_df). Never raises.
    """
    if df is None or getattr(df, "empty", True):
        return df, 0, "", df.head(0) if df is not None else df
    providers = _pick_providers(fc, keys)
    if not providers:
        return df, 0, "", df.head(0)
    chain = "+".join(name for name, _, _, _ in providers)

    good = [d for d in decisions if d.get("status") in GOOD][:fc["max_examples_per_side"]]
    bad = [d for d in decisions if d.get("status") in BAD][:fc["max_examples_per_side"]]
    if not bad:
        return df, 0, "", df.head(0)

    per_call = max(1, int(fc["ai_jobs_per_call"] or fc["max_jobs_per_ai_call"] or 40))
    max_chunks = max(1, int(fc["ai_max_chunks_per_scrape"] or 1))
    cover = min(len(df), per_call * max_chunks)
    if len(df) > cover:
        print(f"[feedback] AI scoring covers {cover} of {len(df)} postings "
              f"({max_chunks} chunk(s) x {per_call}); the rest is heuristic-only.")
    work = df.head(cover)
    chunks = [work.iloc[i:i + per_call] for i in range(0, len(work), per_call)]

    has_desc = "description" in work.columns
    cooled: set = set()
    drop_labels: list = []
    drop_from: dict = {}
    used_models: list = []
    for ci, batch in enumerate(chunks, 1):
        rows = []
        for _, r in batch.iterrows():
            rows.append({
                "title": r.get("title", ""),
                "company": r.get("company", ""),
                "description": r.get("description", "") if has_desc else "",
            })
        system, user = _build_scoring_prompt(decisions, rows, fc)

        est_in = estimate_tokens(system) + estimate_tokens(user)
        ok, reason = _budget_allows(fc, est_in)
        if not ok:
            print(f"[feedback] AI scoring stopped ({reason}); rest is heuristic-only.")
            break
        print(f"[feedback] AI scoring chunk {ci}/{len(chunks)} "
              f"(~{est_in} in-tokens, {len(batch)} postings; chain: {chain}).")
        _LIMITER.wait(float(fc["min_seconds_between_calls"] or 0))
        try:
            reply, used, name, model = _chat_with_failover(
                providers, system, user, cooled)
        except Exception as e:
            print(f"[feedback] AI chunk {ci} failed ({e}); keeping it.")
            continue
        verdicts = _parse_ai_decisions(reply, len(batch))
        spent = used if used else est_in + estimate_tokens(reply)
        calls, toks = _record_usage(fc, spent)
        if calls:
            print(f"[feedback] AI usage today: {calls} calls, {toks} tokens.")
        if not verdicts:
            print(f"[feedback] AI chunk {ci} ({name}) returned no usable verdicts.")
            continue
        used_models.append(f"{name}/{model}")
        for i, v in verdicts.items():
            if v == "SKIP":
                label = batch.index[i]
                drop_labels.append(label)
                drop_from[label] = name
        print(f"[feedback] AI chunk {ci} ({name}/{model}) skipped "
              f"{sum(1 for v in verdicts.values() if v == 'SKIP')} "
              f"of {len(batch)} postings.")

    seen, uniq_labels = set(), []
    for label in drop_labels:
        if label not in seen:
            seen.add(label)
            uniq_labels.append(label)
    if not uniq_labels:
        return df, 0, "", df.head(0)
    dropped = df.loc[uniq_labels].copy()
    dropped["filter_reason"] = [f"ai:{drop_from.get(ix, '?')}"
                                for ix in dropped.index]
    kept = df.drop(index=uniq_labels).reset_index(drop=True)
    ai_names = "+".join(sorted({m.split("/")[0] for m in used_models}))
    print(f"[feedback] AI ({ai_names}) skipped {len(uniq_labels)} of {cover} "
          f"scored postings based on past negative decisions.")
    return kept, len(uniq_labels), ai_names, dropped.reset_index(drop=True)


def generate_text(system: str, user: str, cfg: dict,
                  usage_name: str = "tailor") -> tuple[str, str, str]:
    """One generic LLM call reusing the provider picker, rate limiter and
    daily budgets. Returns (reply_text, provider_name, error).

    `usage_name` selects the tracked budget bucket: "feedback_ai" shares
    the scrape filter's caps, anything else (e.g. "tailor") gets the
    tailor_* caps from the same config block. Providers fail over: if one
    chokes, the next in the chain picks the call up. Never raises --
    failures come back as ("", "", reason).
    """
    fc = _cfg(cfg)
    if not fc["use_ai"] or fc["ai_provider"] == "off":
        return "", "", "AI disabled in config (feedback.use_ai/ai_provider)"
    try:
        keys = _api_keys()
    except Exception:
        keys = {}
    providers = _pick_providers(fc, keys)
    if not providers:
        return "", "", "no AI API keys in .env"

    if usage_name == "feedback_ai":
        max_calls, max_toks = fc["max_ai_calls_per_day"], fc["max_ai_tokens_per_day"]
    else:
        max_calls, max_toks = fc["tailor_max_calls_per_day"], fc["tailor_max_tokens_per_day"]
    est_in = estimate_tokens(system) + estimate_tokens(user)
    ok, reason = _budget_allows_fc(fc, usage_name, est_in, max_calls, max_toks)
    if not ok:
        return "", "", reason
    _LIMITER.wait(float(fc["min_seconds_between_calls"] or 0))
    try:
        reply, used, name, model = _chat_with_failover(
            providers, system, user, set())
    except Exception as e:
        return "", "", f"all providers failed: {e}"
    reply = (reply or "").strip()
    if not reply:
        # Reasoning models occasionally return an empty content field on
        # an otherwise successful call -- report it so callers can retry.
        _record_usage_name(fc, usage_name, est_in)
        return "", f"{name}/{model}", "empty reply from model (transient)"
    spent = used if used else est_in + estimate_tokens(reply)
    _record_usage_name(fc, usage_name, spent)
    return reply, f"{name}/{model}", ""


def _budget_allows_fc(fc: dict, usage_name: str, est_in_tokens: int,
                      max_calls: int, max_toks: int) -> tuple[bool, str]:
    try:
        entry = _usage_entry(_load_usage(fc["usage_state_path"]), usage_name)
        if max_calls and entry["calls"] >= max_calls:
            return False, f"daily AI call budget reached ({entry['calls']}/{max_calls})"
        if max_toks and entry["tokens"] + est_in_tokens > max_toks:
            return False, (f"daily AI token budget would be exceeded "
                           f"({entry['tokens']} used + ~{est_in_tokens} est > {max_toks})")
        return True, ""
    except Exception:
        return True, ""


def _record_usage_name(fc: dict, usage_name: str, tokens_used: int):
    try:
        usage = _load_usage(fc["usage_state_path"])
        entry = _usage_entry(usage, usage_name)
        entry["calls"] += 1
        entry["tokens"] += max(1, int(tokens_used))
        _save_usage(fc["usage_state_path"], usage)
        print(f"[feedback] AI usage today ({usage_name}): "
              f"{entry['calls']} calls, {entry['tokens']} tokens.")
    except Exception as e:
        print(f"[feedback] WARNING: could not record AI usage: {e}")


def apply_feedback(df, cfg: dict, report: dict | None = None):
    """Main entry: filter a freshly-scraped frame using dashboard feedback.

    Runs heuristic always, AI when keys allow. Never raises -- on any
    problem the input frame is returned unchanged. When `report` (a dict)
    is given, it is filled with heuristic_dropped / heuristic_reasons /
    ai_dropped / ai_provider plus `dropped_rows` (one dict per filtered
    posting with its filter_reason) so callers can persist them for review.
    """
    fc = _cfg(cfg)
    if not fc["enabled"] or df is None or getattr(df, "empty", True):
        return df
    try:
        decisions = load_decisions()
    except Exception as e:
        print(f"[feedback] no dashboard data available ({e}); skipping learning.")
        return df
    n_good = sum(1 for d in decisions if d.get("status") in GOOD)
    n_bad = sum(1 for d in decisions if d.get("status") in BAD)
    if len(decisions) < fc["min_samples"] or not n_bad:
        print(f"[feedback] only {len(decisions)} decided jobs "
              f"({n_good} good / {n_bad} bad) -- need {fc['min_samples']}; skipping.")
        return df

    patterns = learn_patterns(decisions, fc)
    if patterns["keywords"] or patterns.get("phrases") or patterns["companies"] \
            or patterns.get("locations"):
        print(f"[feedback] learned from {len(decisions)} decisions "
              f"({n_good} good / {n_bad} bad): "
              f"keywords={patterns['keywords']} phrases={patterns.get('phrases', [])} "
              f"companies={patterns['companies']} "
              f"locations={patterns.get('locations', [])}")
    df, n_heur, heur_reasons, dropped_heur = apply_heuristic(df, patterns)
    if n_heur:
        print(f"[feedback] heuristic dropped {n_heur} postings matching reject patterns.")
    dropped_frames = []
    if dropped_heur is not None and not dropped_heur.empty:
        dropped_frames.append(dropped_heur)
    if report is not None:
        report["heuristic_dropped"] = n_heur
        report["heuristic_reasons"] = heur_reasons
        report["ai_dropped"] = 0
        report["ai_provider"] = ""

    if fc["use_ai"] and fc["ai_provider"] != "off":
        try:
            keys = _api_keys()
        except Exception:
            keys = {}
        if keys:
            df, n_ai, ai_name, dropped_ai = ai_filter(df, decisions, fc, keys)
            if dropped_ai is not None and not dropped_ai.empty:
                dropped_frames.append(dropped_ai)
            if report is not None:
                report["ai_dropped"] = n_ai
                report["ai_provider"] = ai_name
        else:
            print("[feedback] no AI API keys in .env; heuristic-only.")
    if report is not None:
        rows: list[dict] = []
        for frame in dropped_frames:
            for _, r in frame.iterrows():
                rows.append(_dropped_row(r, r.get("filter_reason", "")))
        report["dropped_rows"] = rows
    return df
