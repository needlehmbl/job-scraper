"""
Extract a structured resume bank from a .docx or .pdf resume.

Understands the Harvard-style layout (centered name header, centered
UPPER/lowercase section headings, two-column company+location /
title+dates lines split by tabs or wide spacing, bulleted achievements,
labeled skill groups) instead of dumping every paragraph into one pile:

    python extract_resume.py resume.docx [out.yaml]
    python extract_resume.py resume.pdf [out.yaml]

Also importable -- `extract_bank(path)` (docx) and
`extract_bank_from_pdf(path)` (pdf) return the bank dict so the
dashboard upload endpoint can parse dropped-in resumes server-side.
PDFs are read with pypdf as plain text lines (no bold/centering
survives, so headings are detected by text match and company/title
columns by tab/wide-space splits); scanned/image PDFs with no
extractable text raise a clear error instead of an empty bank.

Output schema matches resume_bank.yaml (contact / summary / skills /
experience / projects / education / certifications / soft_skills /
languages / interests). Anything the parser can't place is kept under
`extra_sections` in the YAML rather than silently dropped, so a quick
glance at the file shows what needs hand-fixing.
"""
import re
import sys

import yaml
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

CENTERED = {WD_ALIGN_PARAGRAPH.CENTER}

# Normalized (lowercased, '&' -> 'and', no trailing colon) section headings
# mapped onto bank keys. "Skills & Interests" is one Harvard section that
# fans out into skills / soft_skills / languages / interests via its
# bold "Label:" sub-headings (see _SKILL_LABELS).
SECTIONS = {
    "summary": "summary",
    "professional summary": "summary",
    "profile": "summary",
    "education": "education",
    "academic background": "education",
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment history": "experience",
    "skills": "skills",
    "skills and interests": "skills",
    "technical skills": "skills",
    "core competencies": "skills",
    "projects": "projects",
    "personal projects": "projects",
    "certifications": "certifications",
    "licenses": "certifications",
    "certifications and licenses": "certifications",
}

# Bold "Label:" lines inside the skills section -> bank key. Unlisted labels
# fall through to a generic skill group named after the label.
_SKILL_LABELS = {
    "technical skills": "skills",
    "technologies": "skills",
    "soft skills": "soft_skills",
    "language": "languages",
    "languages": "languages",
    "interests": "interests",
    "interest": "interests",
}

# Bullet glyphs: standard marks plus private-use \uf0b7 (what LibreOffice
# and Word-to-PDF exports emit for round bullets).
_BULLET_CLASS = r"[•▪●◦·\uf0b7\-\u2013\u2014*]"
_BULLET_PAT = re.compile(_BULLET_CLASS + r"\s+")
_BULLET_ONLY_PAT = re.compile(r"[.\s•·\uf0b7-]+")
_URL_PAT = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.]+")
_EMAIL_PAT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_PAT = re.compile(r"(\+?\d[\d\s().-]{6,}\d)")
_DATE_PAT = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2},?\s+)?(\d{4})",
    re.IGNORECASE,
)
_MONTH_NUM = {m.lower(): i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}

TAG_NEEDLES = {
    "linux": ["linux", "ubuntu", "arch", "bash", "sysadmin", "administration"],
    "python": ["python", "django", "flask"],
    "web-frontend": ["react", "next.js", "frontend", "front-end", "javascript",
                     "typescript", "html", "css", "ui", "ux"],
    "web-backend": ["node", "backend", "back-end", "rest", "api", "oauth",
                    "full-stack", "fullstack", "supabase", "firebase"],
    "mobile": ["mobile", "android", "ios", "flutter", "react native"],
    "database": ["sql", "database", "postgres", "mysql", "mongodb", "nosql", "dbms"],
    "devops": ["docker", "container", "kubernetes", "k8s", "ci/cd", "cicd",
               "github actions", "pipeline", "git"],
    "qa": ["quality assurance", "qa", "testing", "test", "audit"],
    "ai": ["ai", "ml", "machine learning", "llm", "gpu", "model", "ollama",
           "prompt", "pytorch", "comfyui", "agentic"],
    "automation": ["automat", "script", "workflow"],
    "cloud": ["aws", "azure", "gcp", "cloud"],
    "networking": ["network", "dns", "firewall", "vpn", "tcp", "lan", "wan"],
    "it-support": ["troubleshoot", "helpdesk", "help desk", "service desk",
                   "desktop", "windows", "microsoft office", "office 365",
                   "technical support", "hardware", "printer"],
    "project": ["agile", "scrum", "managed", "coordinat", "led", "facilitat"],
    "data": ["data", "excel", "analytics", "report"],
    "design": ["design", "figma", "photoshop", "media", "content", "marketing"],
}


def guess_tags(text: str):
    lower = text.lower()
    return [tag for tag, needles in TAG_NEEDLES.items()
            if any(n in lower for n in needles)] or ["general"]


# ------------------------------------------------------------ docx helpers

def _para_info(p):
    """Flatten a paragraph to (text, is_centered, bold_prefix, rest).

    Harvard two-column lines put the field (company / title / school) in
    bold runs and the facing column (location / dates) in regular runs, so
    splitting on the first non-bold run recovers both columns.
    """
    text = (p.text or "").strip()
    centered = p.alignment in CENTERED
    runs = [(r.text or "", bool(r.bold)) for r in p.runs]
    bold_prefix, rest = [], None
    for rtext, rbold in runs:
        if rest is None and rbold and rtext.strip():
            bold_prefix.append(rtext)
        elif rest is None and not rtext.strip():
            # whitespace-only run: belongs to whichever side is adjacent;
            # keep it with the bold prefix only if bold continues after it.
            bold_prefix.append(rtext)
        else:
            if rest is None:
                rest = ""
                # drop whitespace-only runs already banked that turn out to
                # be trailing the prefix (they get stripped later anyway).
            rest += rtext
    prefix = "".join(bold_prefix).strip()
    # If nothing was bold, the whole line is the "rest".
    if not prefix:
        return text, centered, "", text
    return text, centered, prefix, (rest or "").strip()


def _is_bullet(p, text: str) -> bool:
    style = (p.style.name or "").lower()
    if "list" in style or "bullet" in style:
        return True
    return bool(_BULLET_PAT.match(text))


def _norm_heading(text: str) -> str:
    h = text.strip().lower().rstrip(":").strip()
    return re.sub(r"\s+", " ", h.replace("&", "and"))


def _is_section_heading(text: str, centered: bool, all_bold: bool,
                        style_name: str) -> str | None:
    """Return the bank section key when a paragraph is a section heading."""
    if not centered or len(text) > 40 or len(text.split()) > 4:
        return None
    if not (all_bold or (style_name or "").lower().startswith("heading")):
        return None
    return SECTIONS.get(_norm_heading(text))


def _split_columns(text: str):
    """Split a 'left<TAB/multiple spaces>right' line into (left, right)."""
    parts = re.split(r"\t+|\s{3,}", text.strip())
    parts = [x.strip(" ,|") for x in parts if x.strip(" ,|\t")]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    m = _DATE_PAT.search(text)
    if m:  # trailing date with only single-space separation ("Degree May 2022")
        return text[:m.start()].strip(" ,"), m.group(0).strip()
    return text.strip(), ""


def _month_to_num(date_str: str):
    m = _DATE_PAT.search(date_str or "")
    if not m:
        return ""
    return f"{m.group(3)}-{_MONTH_NUM[m.group(1).lower()]:02d}"


def _compact_len(text: str) -> int:
    """Length with Harvard column padding collapsed (tab/space-aligned
    company+location lines can be 100+ chars of mostly whitespace)."""
    return len(re.sub(r"\s+", " ", text).strip())


def _clean_location(loc: str) -> str:
    loc = re.sub(r"\s+", " ", loc).strip(" ,|")
    return re.sub(r"\s*,\s*,+", ",", loc).strip(" ,")


# ------------------------------------------------------------ section parsers

def _parse_contact(lines):
    contact = {"name": "", "email": "", "phone": "", "location": "",
               "links": []}
    for text, centered, prefix, rest in lines:
        if not contact["name"] and prefix and len(prefix.split()) <= 6 \
                and not _URL_PAT.search(prefix) and "@" not in prefix \
                and not _PHONE_PAT.search(prefix):
            contact["name"] = prefix
            continue
        em = _EMAIL_PAT.search(text)
        if em and not contact["email"]:
            contact["email"] = em.group(0)
        ph = _PHONE_PAT.search(text)
        if ph and not contact["phone"]:
            contact["phone"] = re.sub(r"\s+", " ", ph.group(0)).strip()
        for url in _URL_PAT.findall(text):
            if "@" in url and "://" not in url and not url.startswith("www."):
                continue
            # Label from the text segment right before this URL (split the
            # "Github: <u1> | LinkedIn: <u2>" line on pipes first, so the
            # second URL doesn't inherit the first label).
            before = text[:text.find(url)].split("|")[-1].lower()
            label = "Portfolio"
            for key in ("github", "linkedin", "portfolio", "website",
                        "email", "phone", "mobile"):
                if key in before:
                    label = key.capitalize()
                    break
            else:
                words = re.findall(r"[A-Za-z]{3,}", before)
                if words:
                    label = words[-1].strip(":").capitalize()
            if not any(l["url"] == url for l in contact["links"]):
                contact["links"].append({"label": label, "url": url})
        if "•" in text and not contact["location"]:
            bits = [b.strip() for b in text.split("•")]
            for b in bits:
                if _EMAIL_PAT.search(b) or _PHONE_PAT.search(b) \
                        or _URL_PAT.search(b) or not b:
                    continue
                if re.search(r"\d{4}", b) or "," in b:  # street/city line
                    contact["location"] = contact["location"] or b
    # Fallback location: a header line that looks like an address.
    if not contact["location"]:
        for text, *_ in lines:
            if "," in text and re.search(r"\d", text) and "@" not in text \
                    and len(text) < 80:
                contact["location"] = text.strip()
                break
    return contact


def _parse_education(blocks):
    """One entry per bold-lead line: school | location, then degree+date lines."""
    entries, cur = [], None
    for text, centered, prefix, rest in blocks:
        if prefix and _compact_len(text) < 140 and not _is_bullet_text(text):
            if cur:
                entries.append(cur)
            school, loc = _split_columns(text) if not rest \
                else (prefix, rest)
            # prefix/rest split already separates bold school from location
            if rest:
                school, loc = prefix, rest
            cur = {"school": school.strip(),
                   "degree": "",
                   "graduation": _month_to_num(loc) or _month_to_num(text)}
            loc_clean = _clean_location(re.sub(_DATE_PAT.pattern, "", loc,
                                               flags=re.IGNORECASE))
            if loc_clean:
                cur["location"] = loc_clean
        elif cur is not None:
            left, right = _split_columns(text)
            grad = _month_to_num(right) or _month_to_num(text)
            if grad and not cur.get("graduation"):
                cur["graduation"] = grad
            degree = left if right and grad else text
            cur["degree"] = f"{cur['degree']} {degree}".strip(" ;") \
                if cur["degree"] else degree
        # lines before the first school line are ignored (stray text)
    if cur:
        entries.append(cur)
    for e in entries:
        e.setdefault("graduation", "")
    return entries


def _is_bullet_text(text: str) -> bool:
    return bool(_BULLET_PAT.match(text))


def _parse_experience(blocks, para_styles):
    """Company line (bold lead) -> title line (bold lead) -> bullets."""
    jobs, cur, expect = [], None, "company"
    for (text, centered, prefix, rest), is_bullet in zip(blocks, para_styles):
        if is_bullet:
            if cur is not None:
                bullet = _BULLET_PAT.sub("", text, count=1).strip()
                if bullet:
                    cur["bullets"].append(
                        {"text": bullet, "tags": guess_tags(bullet)})
            continue
        if prefix and _compact_len(text) < 160 and expect in ("company", "title", "title-or-company"):
            # A bold lead after bullets starts a new job; a bold lead right
            # after the company line is the title/dates line.
            if cur is not None and expect == "company":
                jobs.append(cur)
                cur = None
            if cur is None:
                company = prefix
                loc = _clean_location(rest)
                cur = {"company": company, "title": "", "location": loc,
                       "start_date": "", "end_date": "", "dates_raw": "",
                       "bullets": []}
                expect = "title"
            elif expect == "title":
                cur["title"] = prefix
                dates = rest or ""
                if not dates:  # title and dates in one line, split columns
                    _, dates = _split_columns(text)
                    if dates == text:
                        dates = ""
                cur["dates_raw"] = dates
                months = _DATE_PAT.findall(dates)
                if months:
                    first = f"{months[0][2]}-{_MONTH_NUM[months[0][0].lower()]:02d}"
                    cur["start_date"] = first
                    if len(months) > 1:
                        cur["end_date"] = \
                            f"{months[1][2]}-{_MONTH_NUM[months[1][0].lower()]:02d}"
                    elif re.search(r"present|current|now", dates, re.I):
                        cur["end_date"] = "Present"
                expect = "title-or-company"
            continue
        # Plain continuation line: degree-style detail or second title line.
        if cur is not None and text:
            if expect == "title" and not cur["title"]:
                left, right = _split_columns(text)
                cur["title"] = left
                cur["dates_raw"] = right
                if _month_to_num(right):
                    cur["start_date"] = _month_to_num(right)
                expect = "title-or-company"
            # otherwise: stray line inside a job block -- keep the text so
            # nothing is silently lost.
            elif expect == "title-or-company":
                cur.setdefault("notes", []).append(text)
    if cur is not None:
        jobs.append(cur)
    for j in jobs:
        j.pop("dates_raw", None)
        notes = j.pop("notes", [])
        if notes and not j["bullets"]:
            for n in notes:  # prose-only job block -> treat lines as bullets
                j["bullets"].append({"text": n, "tags": guess_tags(n)})
    return jobs


def _parse_skills(blocks):
    groups, soft, langs, interests = [], [], [], []
    cur_items = None
    label_key = None
    for text, centered, prefix, rest in blocks:
        label = (prefix or text).strip().rstrip(":")
        norm = _norm_heading(label)
        if prefix and text.rstrip().endswith(":") and len(text) < 40:
            key = _SKILL_LABELS.get(norm)
            if key == "skills":
                cur_items = []
                groups.append({"group": label, "items": cur_items})
                label_key = "skills"
            elif key in ("soft_skills", "languages", "interests"):
                label_key = key
                cur_items = {"soft_skills": soft, "languages": langs,
                             "interests": interests}[key]
            else:  # unknown label -> its own skill group, nothing lost
                cur_items = []
                groups.append({"group": label, "items": cur_items})
                label_key = "skills"
            continue
        target = cur_items if cur_items is not None else None
        if target is None:
            cur_items = []
            groups.append({"group": "General", "items": cur_items})
            target = cur_items
        item = _BULLET_PAT.sub("", text, count=1).strip().rstrip(".")
        if item:
            # Comma lists ("English – fluent" stays whole; "Python, Java"
            # splits) -- split only on commas separating short tokens.
            bits = [b.strip() for b in item.split(",")]
            if len(bits) > 1 and all(len(b) < 40 for b in bits):
                target.extend(b for b in bits if b)
            else:
                target.append(item)
    return groups, soft, langs, interests


# ------------------------------------------------------------ main entry

def _assemble_bank(header, sections):
    """Build the bank dict from header infos + {key: [(info, is_bullet)]}.

    Shared by the docx and pdf entry points so both produce the same
    schema from the same section parsers."""
    bank = {
        "contact": _parse_contact(header),
        "summary": {"variants": []},
        "skills": [],
        "experience": [],
        "projects": [],
        "education": [],
        "certifications": [],
    }
    extra = {}
    for key, tagged in sections.items():
        flat_infos = [info for info, _ in tagged]
        flat_flags = [flag for _, flag in tagged]
        if key == "summary":
            bank["summary"]["variants"] = [
                " ".join(t for t, *_ in flat_infos)]
        elif key == "education":
            bank["education"] = _parse_education(flat_infos)
        elif key == "experience":
            bank["experience"] = _parse_experience(flat_infos, flat_flags)
        elif key == "skills":
            groups, soft, langs, interests = _parse_skills(flat_infos)
            bank["skills"] = groups
            if soft:
                bank["soft_skills"] = soft
            if langs:
                bank["languages"] = langs
            if interests:
                bank["interests"] = interests
        elif key == "projects":
            bank["projects"] = [{"title": t, "description": ""}
                                for t, *_ in flat_infos]
        elif key == "certifications":
            bank["certifications"] = [t for t, *_ in flat_infos]
        else:
            extra[key] = [t for t, *_ in flat_infos]
    if extra:
        bank["extra_sections"] = extra
    _hoist_education_links(bank)
    return bank


def extract_bank(path: str) -> dict:
    doc = Document(path)
    header, sections, cur_key, cur_block = [], {}, None, None
    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if not text or len(text) < 2 or _BULLET_ONLY_PAT.fullmatch(text):
            continue  # stray artifacts (e.g. a lone "." first line)
        info = _para_info(p)
        _, centered, _, _ = info
        runs = [r for r in p.runs if (r.text or "").strip()]
        all_bold = bool(runs) and all(bool(r.bold) for r in runs)
        key = _is_section_heading(text, centered, all_bold,
                                  p.style.name or "")
        if key is not None:  # a new section starts -- switch to it
            cur_key = key
            cur_block = []
            sections.setdefault(key, []).append(cur_block)
            continue
        if cur_key is None:
            header.append(info)
        else:
            cur_block.append((info, _is_bullet(p, text)))

    flat = {k: [(info, flag) for block in blocks for info, flag in block]
            for k, blocks in sections.items()}
    return _assemble_bank(header, flat)


_PDF_PAGE_PAT = re.compile(r"^(page\s+\d+(\s+of\s+\d+)?|\d+\s*/\s*\d+)$",
                           re.IGNORECASE)
_PDF_COL_PAT = re.compile(r"\t+|\s{2,}|\s+\|\s+")


def _pdf_info(line: str, is_lead: bool):
    """Synthesize a docx-style (text, centered, prefix, rest) tuple.

    Plain text has no bold runs, so for lead lines (company / title /
    school / skill-label) the text before a tab/wide-space/pipe split
    plays the bold-lead role; continuation lines get an empty prefix so
    the column parsers treat them as prose (degrees, notes, items)
    instead of starting new entries."""
    text = line.strip()
    if not is_lead:
        return text, False, "", text
    parts = [x.strip(" ,|") for x in _PDF_COL_PAT.split(text)]
    parts = [x for x in parts if x]
    if len(parts) >= 2:
        return text, False, parts[0], " ".join(parts[1:])
    return text, False, text, ""


def _pdf_is_lead(line: str, boundary: bool, section: str | None) -> bool:
    """Decide whether a pdf text line opens a new entry.

    Leads have a column split, look like a `Label:` line, carry dates in
    the experience section (title/dates lines -- elsewhere a date means
    a degree/certification detail line), or are short lines right after
    a heading/bullet run (company/school lines -- tabs rarely survive
    the pdf text layer)."""
    if _PDF_COL_PAT.search(line):
        return True
    if line.rstrip().endswith(":") and len(line) < 40:
        return True
    if _DATE_PAT.search(line):
        return section == "experience"
    return boundary and len(line) < 70


def _pdf_heading(line: str) -> str | None:
    """Section key when a pdf text line looks like a section heading."""
    if len(line) > 40 or len(line.split()) > 4:
        return None
    return SECTIONS.get(_norm_heading(line))


def extract_bank_from_pdf(path: str) -> dict:
    """Same bank schema as extract_bank, from a .pdf resume's text layer."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ValueError("pdf support needs the 'pypdf' package "
                         "(pip install -r requirements.txt)") from e
    reader = PdfReader(path)
    raw: list[str] = []
    try:
        pages = list(reader.pages)
    except Exception as e:
        raise ValueError(f"could not read pdf pages ({e})") from e
    for page in pages:
        try:
            raw.extend((page.extract_text() or "").splitlines())
        except Exception:
            continue
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw]
    lines = [ln for ln in lines if ln and not _PDF_PAGE_PAT.match(ln)]
    if not lines:
        raise ValueError("no extractable text found -- is it a scanned/"
                         "image PDF? Export a text (not scanned) PDF instead")
    if len(" ".join(lines)) < 200:
        raise ValueError("almost no extractable text found -- is it a scanned/"
                         "image PDF? Export a text (not scanned) PDF instead")
    header: list = []
    sections: dict = {}
    cur_key = None
    boundary = True  # next short line after a heading/bullets opens an entry
    for ln in lines:
        key = _pdf_heading(ln)
        if key is not None:
            cur_key = key
            sections.setdefault(key, [])
            boundary = True
            continue
        is_bullet = bool(_BULLET_PAT.match(ln))
        if cur_key is None:
            header.append(_pdf_info(ln, True))
            continue
        if not is_bullet and not _pdf_is_lead(ln, boundary, cur_key):
            # Continuation prose: degree detail, job notes, skill items.
            sections[cur_key].append((_pdf_info(ln, False), False))
            boundary = False
            continue
        sections[cur_key].append((_pdf_info(ln, True), is_bullet))
        # A new job/school starts after a heading or a bullet run -- but
        # prose following a title line belongs to that job, not a new one.
        boundary = is_bullet
    return _assemble_bank(header, sections)


def _hoist_education_links(bank: dict):
    """Move bare URLs out of education degree text (e.g. a "Portfolio:
    <url>" line under a school) into contact links, where the tailor
    prompt and renderer expect them. Keeps degree strings clean."""
    contact = bank.get("contact", {})
    for entry in bank.get("education", []):
        degree = entry.get("degree", "")
        urls = [u for u in _URL_PAT.findall(degree)
                if "://" in u or u.startswith("www.")]
        if not urls:
            continue
        for url in urls:
            if not any(l.get("url") == url for l in contact.get("links", [])):
                label = "Portfolio"
                low_url = url.lower()
                if "linkedin" in low_url:
                    label = "Linkedin"
                elif "github.io" not in low_url and "github" in low_url:
                    label = "Github"
                contact.setdefault("links", []).append(
                    {"label": label, "url": url})
            degree = degree.replace(url, "")
        degree = re.sub(r"\b(portfolio|website|site)\s*:\s*", "",
                        degree, flags=re.IGNORECASE)
        entry["degree"] = re.sub(r"\s{2,}", " ", degree).strip(" ;")


def main(path: str, out_path: str = "resume_bank.extracted.yaml"):
    if path.lower().endswith(".pdf"):
        bank = extract_bank_from_pdf(path)
    else:
        bank = extract_bank(path)
    with open(out_path, "w") as f:
        yaml.dump(bank, f, sort_keys=False, allow_unicode=True, width=100)
    c = bank["contact"]
    print(f"Wrote {out_path}:")
    print(f"  contact: {c.get('name')!r} <{c.get('email')!r}> "
          f"{c.get('phone')!r} [{len(c.get('links', []))} links]")
    print(f"  summary variants: {len(bank['summary']['variants'])}")
    print(f"  education entries: {len(bank['education'])}")
    for e in bank["education"]:
        print(f"    - {e.get('school')!r} | {e.get('degree', '')[:60]!r} "
              f"({e.get('graduation', '')})")
    print(f"  experience jobs: {len(bank['experience'])}")
    for j in bank["experience"]:
        print(f"    - {j.get('title')!r} @ {j.get('company')!r} "
              f"[{j.get('start_date', '')}..{j.get('end_date', '')}] "
              f"{len(j.get('bullets', []))} bullets")
    print(f"  skill groups: {len(bank['skills'])} "
          f"({sum(len(g['items']) for g in bank['skills'])} items) | "
          f"soft={len(bank.get('soft_skills', []))} "
          f"langs={len(bank.get('languages', []))} "
          f"interests={len(bank.get('interests', []))}")
    if bank.get("extra_sections"):
        print(f"  extra (needs hand-check): {list(bank['extra_sections'])}")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Usage: python extract_resume.py resume.docx|resume.pdf [out.yaml]")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else
         "resume_bank.extracted.yaml")
