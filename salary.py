"""Salary-range capture + normalization to monthly estimates.

Sources:
- trabajo cards: spans like "₱42,000-₱60,000 Contract", "₱450,000-₱650,000 Contract"
  (often yearly figures on trabajo -- interval detection below handles that).
- jobstreet cards: data-automation="jobSalary" text, e.g. "₱30,000 – ₱40,000 a month".
- jobspy DF: min_amount / max_amount / interval / currency columns.

Normalized output keys (used for DB + API + dashboard):
  salary_raw, salary_currency, salary_min, salary_max,
  salary_interval (hourly/daily/weekly/monthly/yearly/unknown),
  salary_monthly_min, salary_monthly_max, salary_display
"""
import re

CURRENCY_SYMBOLS = {
    "₱": "PHP",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
}

_INTERVAL_STRONG = {
    "hourly": (r"per hour", r"/hr\b", r"\bhourly\b", r"an? hour\b"),
    "daily": (r"per day", r"/day\b", r"\bdaily\b", r"a day\b"),
    "weekly": (r"per week", r"/w(e)?e?k\b", r"\bweekly\b", r"a week\b"),
    "monthly": (r"per month", r"/m(o|onth)\b", r"\bmonthly\b", r"a month\b"),
    "yearly": (r"per year", r"per annum", r"/y(r|ear)\b", r"\bannual\w*\b",
               r"\byearly\b", r"a year\b", r"p\.a\."),
}
# Bare nouns ("Day 1", "8-hour shift") are only a fallback -- they must not
# outrank an explicit cadence elsewhere in the text.
_INTERVAL_WEAK = {
    "hourly": (r"\bhours?\b", r"\bhr\b"),
    "daily": (r"\bdays?\b",),
    "weekly": (r"\bweeks?\b",),
    "monthly": (r"\bmonths?\b",),
    "yearly": (r"\byears?\b",),
}

# monthly estimate factors
_HOURS_PER_MONTH = 160.0
_DAYS_PER_MONTH = 22.0
_WEEKS_PER_MONTH = 52.0 / 12.0


def detect_interval(text: str) -> str:
    low = (text or "").lower()
    for tier in (_INTERVAL_STRONG, _INTERVAL_WEAK):
        for interval, patterns in tier.items():
            if any(re.search(p, low) for p in patterns):
                return interval
    return "unknown"


def detect_currency(text: str) -> str:
    t = text or ""
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in t:
            return code
    m = re.search(r"\b(PHP|USD|EUR|GBP|JPY|INR|AUD|CAD|SGD)\b", t, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # code glued to the amount ("Php19,000", "$80k" handled by symbol)
    m = re.search(r"(PHP|USD|EUR|GBP|JPY|INR|AUD|CAD|SGD)\s?[\d]",
                  t, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return ""


def _parse_number(tok: str) -> float | None:
    tok = tok.strip().lower().replace(",", "").replace(" ", "")
    if not tok:
        return None
    mult = 1.0
    if tok.endswith("k"):
        mult = 1000.0
        tok = tok[:-1]
    elif tok.endswith("m"):
        mult = 1_000_000.0
        tok = tok[:-1]
    try:
        return float(tok) * mult
    except ValueError:
        return None


# Posting age ("5 days ago", "1 day ago") is never a pay cadence --
# strip it before interval/amount parsing so it can't win "daily".
_AGE_RE = re.compile(
    r"\b\d+\s+(seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago\b",
    re.IGNORECASE)


def parse_salary_text(text: str) -> dict:
    """Parse a raw salary string into min/max/currency/interval.

    Returns min/max None unless the text carries a pay signal (a currency
    symbol/code or a pay keyword like "salary"/"per month"): this keeps
    full-description backfills from turning "3 years experience" or
    "1 day ago" into phantom salaries.
    """
    raw = (text or "").strip()
    empty = {"raw": raw, "currency": "", "min": None, "max": None,
             "interval": "unknown"}
    if not raw:
        return empty
    text = _AGE_RE.sub("", raw)
    low = text.lower()
    # explicit pay-signal check:
    has_pay_signal = (
        any(sym in text for sym in CURRENCY_SYMBOLS)
        or re.search(r"\b(PHP|USD|EUR|GBP|JPY|INR|AUD|CAD|SGD)\b",
                     text, re.IGNORECASE) is not None
        or any(w in low for w in ("salary", "salar", "pay", "wage",
                                  "compensation", "remuneration",
                                  "per hour", "per day", "per week",
                                  "per month", "per year", "a month",
                                  "monthly", "yearly", "hourly",
                                  "/hr", "/day", "/week", "/mo",
                                  "/month", "/yr", "/year"))
    )
    if not has_pay_signal:
        return empty
    currency = detect_currency(text)
    interval = detect_interval(text)
    # Amounts anchored to a currency marker ("₱27,000", "Php19,000 -
    # Php27,000") win over any stray numbers elsewhere in the text.
    # Magnitude suffix (k/m) counts only when directly attached to the
    # digits ("42k", "1.5m") -- a space-separated word initial ("27,000
    # Monthly") is NOT a multiplier.
    anchored = re.findall(
        r"(?:₱|PHP|\$|€|£|¥|₹)\s*([\d][\d,]*(?:\.\d+)?[kKmM]?)(?![\w])",
        text, re.IGNORECASE)
    vals = []
    for n in anchored:
        v = _parse_number(n)
        if v is not None:
            vals.append(v)
    if not vals:
        # No currency-anchored amount: accept an unanchored range/single
        # only as a fallback (card text without symbols, manual edits).
        m = re.search(
            r"([\d][\d,]*(?:\.\d+)?[kKmM]?)(?![\w])\s*(?:–|—|-|to)\s*"
            r"([\d][\d,]*(?:\.\d+)?[kKmM]?)(?![\w])",
            text, re.IGNORECASE)
        if m:
            for g in m.groups():
                v = _parse_number(g)
                if v is not None:
                    vals.append(v)
        else:
            m = re.search(r"([\d][\d,]*(?:\.\d+)?[kKmM]?)(?![\w])", text)
            if m:
                v = _parse_number(m.group(1))
                if v is not None:
                    vals.append(v)
    lo = hi = None
    if len(vals) == 1:
        lo = hi = vals[0]
    elif len(vals) >= 2:
        lo, hi = min(vals[0], vals[1]), max(vals[0], vals[1])
    # Plausibility floor: sub-peso-centavo figures are stray numbers, not
    # pay (e.g. "Day 1", "13th month"). PHP legs below these are noise.
    if hi is not None and currency == "PHP":
        floor = {"hourly": 50.0, "daily": 300.0, "weekly": 1000.0,
                 "monthly": 5000.0, "yearly": 60000.0,
                 "unknown": 1000.0}[interval]
        if hi < floor:
            lo = hi = None
    if hi is not None and hi <= 0:
        lo = hi = None
    return {"raw": raw, "currency": currency, "min": lo, "max": hi,
            "interval": interval}


def to_monthly(value: float | None, interval: str) -> float | None:
    if value is None:
        return None
    if interval == "monthly" or interval == "unknown":
        # unknown passes through unchanged (caller decides whether to also
        # show a /12 yearly guess); monthly is identity.
        return value if interval == "monthly" else None
    if interval == "yearly":
        return value / 12.0
    if interval == "hourly":
        return value * _HOURS_PER_MONTH
    if interval == "daily":
        return value * _DAYS_PER_MONTH
    if interval == "weekly":
        return value * _WEEKS_PER_MONTH
    return None


def _fmt_amt(v: float | None) -> str:
    if v is None:
        return ""
    if v >= 1000:
        if v >= 1_000_000 and v % 1_000_000 == 0:
            return f"{v/1_000_000:.0f}M"
        if v % 1000 == 0:
            return f"{v/1000:.0f}k"
        return f"{v:,.0f}"
    return f"{v:,.0f}"


def format_salary(currency: str, lo: float | None, hi: float | None,
                  interval: str, mlo: float | None, mhi: float | None) -> str:
    """Short display string, always with currency when known."""
    cur = currency or ""
    prefix = f"{cur} " if cur else ""
    if lo is None:
        return ""
    rng = _fmt_amt(lo) if lo == hi else f"{_fmt_amt(lo)}–{_fmt_amt(hi)}"
    base = f"{prefix}{rng}"
    if interval == "unknown":
        return base
    if interval == "monthly":
        return f"{base}/mo"
    if interval == "yearly" and mlo is not None:
        mr = _fmt_amt(mlo) if mlo == mhi else f"{_fmt_amt(mlo)}–{_fmt_amt(mhi)}"
        return f"{base}/yr (~{prefix}{mr}/mo)"
    suffix = {"hourly": "/hr", "daily": "/day", "weekly": "/wk"}.get(interval, "")
    return f"{base}{suffix}"


def normalize_job_salary(job: dict) -> dict:
    """Accept a scraper row (many shapes) -> normalized salary dict.

    Shapes handled:
    - {"salary_raw": "..."} (trabajo / jobstreet new path)
    - {"salary": "..."} (jobstreet legacy key)
    - jobspy: min_amount/max_amount/interval/currency (also compensation dict)
    """
    raw = (job.get("salary_raw") or job.get("salary") or "").strip()
    currency = (job.get("currency") or job.get("salary_currency") or "").strip().upper()
    lo = job.get("salary_min", job.get("min_amount"))
    hi = job.get("salary_max", job.get("max_amount"))
    interval = (job.get("salary_interval") or job.get("interval") or "").strip().lower()

    comp = job.get("compensation")
    if isinstance(comp, dict):
        lo = lo if lo not in (None, "") else comp.get("min_amount")
        hi = hi if hi not in (None, "") else comp.get("max_amount")
        interval = interval or str(comp.get("interval") or "").lower()
        currency = currency or str(comp.get("currency") or "").upper()

    if raw:
        parsed = parse_salary_text(raw)
        currency = currency or parsed["currency"]
        if lo in (None, ""):
            lo = parsed["min"]
        if hi in (None, ""):
            hi = parsed["max"]
        if not interval or interval == "unknown":
            interval = parsed["interval"]
    else:
        # jobspy-only shape: synthesize raw for audit trail
        if lo is not None or hi is not None:
            try:
                lof = float(lo) if lo not in (None, "") else None
            except (TypeError, ValueError):
                lof = None
            try:
                hif = float(hi) if hi not in (None, "") else None
            except (TypeError, ValueError):
                hif = None
            lo, hi = lof, hif
            interval = interval or "unknown"
            raw = ""

    def _num(v):
        if v is None or v == "":
            return None
        try:
            f = float(v)
            return f
        except (TypeError, ValueError):
            return None

    lo, hi = _num(lo), _num(hi)
    if lo is not None and hi is not None and hi < lo:
        lo, hi = hi, lo
    if not interval:
        interval = "unknown"
    # trabajo heuristic: large round PHP figures with no explicit interval
    # are usually yearly listings -> mark yearly so the monthly estimate shows.
    src = str(job.get("site") or job.get("source") or "").lower()
    if interval == "unknown" and currency == "PHP" and lo is not None \
            and lo >= 200_000 and ("trabajo" in src or "trabajo" in raw.lower()):
        interval = "yearly"

    mlo = to_monthly(lo, interval)
    mhi = to_monthly(hi, interval)
    # unknown interval: no monthly guess (avoid implying a cadence we didn't see)
    display = format_salary(currency, lo, hi, interval, mlo, mhi)
    return {
        "salary_raw": raw,
        "salary_currency": currency,
        "salary_min": lo,
        "salary_max": hi,
        "salary_interval": interval,
        "salary_monthly_min": mlo,
        "salary_monthly_max": mhi,
        "salary_display": display,
    }
