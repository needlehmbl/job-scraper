"""
Heuristic relevance score for triage (no LLM, zero cost).

Score is 0-100, computed from data the scrape already has
(title + description + company) against your resume bank:

  base 50
  +5 per posting skill found in your bank (cap +30)
  -4 per posting skill missing from your bank (floor -20)
  +10 / +5 / +0 junior-fit bonus from explicit experience requirement
      (0 yrs / fresh-grad => +10, 1 yr => +5, 2 yrs / unstated => +0)
  -25 company in learned-bad list, -10 per learned-bad title token (cap -20)

Learned-bad lists come from feedback.learn_patterns() (your dashboard
REJECTED/SKIP/MISMATCH/EXP_GAP decisions). Survivors of the feedback
filter rarely hit them -- the penalty mainly orders borderline rows.

Bank source: default resume in resumes/library.json, falling back to
resume_bank.yaml (repo root) so scoring works before any .docx upload.
"""
import os

import yaml

BANK_FALLBACK_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "resume_bank.yaml")

_MATCH_POINTS = 5
_MATCH_CAP = 30
_MISSING_POINTS = 4
_MISSING_FLOOR = 20
_COMPANY_PENALTY = 25
_TOKEN_PENALTY = 10
_TOKEN_PENALTY_CAP = 20

_bank_cache: dict | None = None
_bank_low_cache: str = ""
_patterns_cache: dict | None = None


def _flatten_bank(bank: dict) -> str:
    parts = [" ".join(bank.get("summary", {}).get("variants", []))]
    for g in bank.get("skills", []) or []:
        parts.append(g.get("group", ""))
        parts.extend(g.get("items", []) or [])
    for job in bank.get("experience", []) or []:
        parts.append(job.get("title", ""))
        for b in job.get("bullets", []) or []:
            parts.append(b.get("text", "") if isinstance(b, dict) else str(b))
    for s in bank.get("soft_skills", []) or []:
        parts.append(s)
    return "\n".join(parts)


def load_bank_text() -> str:
    """Resume bank as one string (cached). Empty string when no bank found."""
    global _bank_cache, _bank_low_cache
    if _bank_cache is not None:
        return _bank_low_cache
    bank: dict = {}
    try:
        import resumes
        bank, err = resumes.load_bank()
        if err:
            bank = {}
    except Exception:
        bank = {}
    if not bank:
        try:
            with open(BANK_FALLBACK_PATH) as f:
                bank = yaml.safe_load(f) or {}
        except (OSError, ValueError):
            bank = {}
    _bank_cache = bank
    _bank_low_cache = _flatten_bank(bank).lower()
    return _bank_low_cache


def _mentions(text_low: str, aliases) -> bool:
    return any(a in text_low for a in aliases)


def load_patterns() -> dict:
    """Learned reject patterns, cached per process. Empty dict on failure."""
    global _patterns_cache
    if _patterns_cache is not None:
        return _patterns_cache
    try:
        import feedback
        import yaml as _yaml
        with open("config.yaml") as f:
            cfg = _yaml.safe_load(f)
        fc = feedback._cfg(cfg)
        decisions = feedback.load_decisions()
        _patterns_cache = feedback.learn_patterns(decisions, fc)
    except Exception:
        _patterns_cache = {}
    return _patterns_cache


def learned_penalties(title: str, company: str,
                      patterns: dict | None = None) -> tuple[int, list[str]]:
    """Penalty from your past reject patterns. Best-effort, never raises."""
    try:
        import feedback
        patterns = patterns if patterns is not None else load_patterns()
    except Exception:
        return 0, []
    notes: list[str] = []
    penalty = 0
    comp = (company or "").strip().lower()
    if comp and comp in set(patterns.get("companies", [])):
        penalty -= _COMPANY_PENALTY
        notes.append(f"avoided company ({company.strip()})")
    toks = set(feedback.norm_title(title or "").split())
    hits = sorted(toks & set(patterns.get("keywords", [])))
    if hits:
        hit_pen = min(len(hits) * _TOKEN_PENALTY, _TOKEN_PENALTY_CAP)
        penalty -= hit_pen
        notes.append(f"reject-pattern: {','.join(hits[:3])}")
    else:
        nt = feedback.norm_title(title or "")
        phit = [p for p in patterns.get("phrases", []) if p and p in nt][:2]
        if phit:
            penalty -= _TOKEN_PENALTY
            notes.append(f"reject-pattern: {'|'.join(phit)}")
    return penalty, notes


def score_job(title: str, company: str, description: str = "",
              bank_low: str | None = None,
              patterns: dict | None = None) -> tuple[int, str]:
    """Return (score 0-100, one-line reason). Never raises."""
    try:
        from tailor import SKILL_LEXICON
    except Exception:
        SKILL_LEXICON = {}
    try:
        from scraper import max_experience_years
    except Exception:
        def max_experience_years(_t):
            return None
    if bank_low is None:
        try:
            bank_low = load_bank_text()
        except Exception:
            bank_low = ""
    job_low = f"{title or ''}\n{description or ''}".lower()

    matched, missing = [], []
    for skill, aliases in (SKILL_LEXICON or {}).items():
        if _mentions(job_low, aliases):
            (matched if bank_low and _mentions(bank_low, aliases)
             else missing).append(skill)

    score = 50
    score += min(len(matched) * _MATCH_POINTS, _MATCH_CAP)
    score -= min(len(missing) * _MISSING_POINTS, _MISSING_FLOOR)

    try:
        req = max_experience_years(description or "")
    except Exception:
        req = None
    junior_note = ""
    if req == 0:
        score += 10
        junior_note = "entry-level"
    elif req == 1:
        score += 5
        junior_note = "1-yr fit"

    pen, pen_notes = learned_penalties(title or "", company or "", patterns)
    score += pen
    score = max(0, min(100, score))

    bits: list[str] = []
    if matched:
        bits.append("+" + ", ".join(matched[:4]))
        if len(matched) > 4:
            bits.append(f"+{len(matched) - 4} more")
    if missing:
        bits.append("missing " + ", ".join(missing[:3]))
    if junior_note:
        bits.append(junior_note)
    bits.extend(pen_notes)
    reason = "; ".join(bits)[:280] or "no skill overlap detected"
    return score, reason


def reset_cache():
    global _bank_cache, _bank_low_cache
    _bank_cache, _bank_low_cache = None, ""


def rescore_stored(limit: int | None = None) -> int:
    """Backfill score/score_reason for stored jobs (title-only: the jobs
    table keeps no descriptions). Returns rows updated."""
    import db
    bank_low = load_bank_text()
    patterns = load_patterns()
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, company FROM jobs "
                        + ("WHERE score = 0 ORDER BY id LIMIT %s"
                           if limit else "WHERE score = 0 ORDER BY id"),
                        ((limit,) if limit else None) if limit else None)
            rows = cur.fetchall()
            n = 0
            for jid, title, company in rows:
                s, reason = score_job(title or "", company or "", "",
                                      bank_low, patterns)
                cur.execute("UPDATE jobs SET score = %s, score_reason = %s "
                            "WHERE id = %s", (s, reason, jid))
                n += 1
            conn.commit()
            return n
    finally:
        conn.close()
