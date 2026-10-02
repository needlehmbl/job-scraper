"""
Local resume library for the dashboard tailoring flow.

`resumes/` (gitignored) holds your uploaded .docx/.pdf files plus an extracted
bank sidecar per resume (`<name>.bank.yaml`, produced by extract_resume)
and a tiny `library.json` index tracking the default resume. Everything
stays on disk locally -- nothing is ever uploaded anywhere except the
trimmed, contact-free bank sent to the LLM when YOU press Tailor.
"""
import json
import os
import re
import time

import yaml

import extract_resume

LIB_DIR = "resumes"
INDEX_FILE = os.path.join(LIB_DIR, "library.json")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # resumes are small; refuse anything huge
ALLOWED_EXTS = (".docx", ".pdf")


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_.")[:80]


def _load_index() -> dict:
    try:
        with open(INDEX_FILE) as f:
            data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("resumes", [])
                data.setdefault("default", "")
                return data
    except (OSError, ValueError):
        pass
    return {"resumes": [], "default": ""}


def _save_index(data: dict):
    os.makedirs(LIB_DIR, exist_ok=True)
    with open(INDEX_FILE, "w") as f:
        json.dump(data, f, indent=2)


def list_library() -> dict:
    """Return {resumes: [{name, docx, bank, created, is_default}]}."""
    data = _load_index()
    out = []
    for entry in data["resumes"]:
        bank_path = os.path.join(LIB_DIR, entry.get("bank", ""))
        summary = ""
        try:
            with open(bank_path) as f:
                bank = yaml.safe_load(f) or {}
            n_bullets = sum(len(j.get("bullets", []))
                            for j in bank.get("experience", []))
            summary = (f"{len(bank.get('experience', []))} jobs, "
                       f"{n_bullets} bullets, "
                       f"{sum(len(g.get('items', [])) for g in bank.get('skills', []))} skills")
        except (OSError, ValueError):
            summary = "bank unreadable -- re-upload to re-extract"
        out.append({**entry, "bank_summary": summary,
                    "is_default": entry.get("name") == data["default"]})
    return {"resumes": out, "default": data["default"]}


def load_bank(name: str = "") -> tuple[dict, str]:
    """Load the bank YAML for `name` (or the default). Returns (bank, error)."""
    data = _load_index()
    if not data["resumes"]:
        return {}, "no resumes uploaded yet -- add one in the Resumes section"
    target = name or data["default"] or data["resumes"][0]["name"]
    entry = next((e for e in data["resumes"] if e["name"] == target), None)
    if entry is None:
        return {}, f"resume '{target}' not found"
    try:
        with open(os.path.join(LIB_DIR, entry["bank"])) as f:
            return yaml.safe_load(f) or {}, ""
    except OSError as e:
        return {}, f"could not read bank file: {e}"


def save_upload(original_filename: str, data: bytes) -> tuple[dict, str]:
    """Validate + store an uploaded .docx/.pdf and extract its bank sidecar."""
    ext = os.path.splitext(original_filename.lower())[1]
    if ext not in ALLOWED_EXTS:
        return {}, "only .docx and .pdf files are accepted"
    if len(data) > MAX_UPLOAD_BYTES:
        return {}, f"file too large ({len(data) // 1024}KB > 10MB)"
    if len(data) < 100:
        return {}, "file is empty"
    # Must be a real docx (zip) or pdf before we trust the extension.
    if ext == ".docx" and not data.startswith(b"PK\x03\x04"):
        return {}, "not a valid .docx file"
    if ext == ".pdf" and not data.startswith(b"%PDF"):
        return {}, "not a valid .pdf file"

    os.makedirs(LIB_DIR, exist_ok=True)
    stem = _safe_name(os.path.splitext(os.path.basename(original_filename))[0])
    if not stem:
        return {}, "unusable filename"
    # De-dupe: resume.docx, resume_2.docx, ... (either extension claims a stem)
    candidate, i = stem, 1
    while any(os.path.exists(os.path.join(LIB_DIR, candidate + e))
              for e in ALLOWED_EXTS):
        i += 1
        candidate = f"{stem}_{i}"
    stored_name, bank_name = candidate + ext, candidate + ".bank.yaml"
    with open(os.path.join(LIB_DIR, stored_name), "wb") as f:
        f.write(data)
    try:
        if ext == ".pdf":
            bank = extract_resume.extract_bank_from_pdf(
                os.path.join(LIB_DIR, stored_name))
        else:
            bank = extract_resume.extract_bank(os.path.join(LIB_DIR, stored_name))
    except Exception as e:
        os.remove(os.path.join(LIB_DIR, stored_name))
        return {}, f"could not parse {ext} ({e}) -- is it a text resume?"
    with open(os.path.join(LIB_DIR, bank_name), "w") as f:
        yaml.dump(bank, f, sort_keys=False, allow_unicode=True, width=100)

    index = _load_index()
    entry = {"name": candidate, "file": stored_name, "bank": bank_name,
             "created": time.strftime("%Y-%m-%d %H:%M")}
    index["resumes"] = [e for e in index["resumes"] if e["name"] != candidate]
    index["resumes"].append(entry)
    if not index["default"]:
        index["default"] = candidate
    _save_index(index)
    n_bullets = sum(len(j.get("bullets", [])) for j in bank.get("experience", []))
    return {**entry, "jobs": len(bank.get("experience", [])),
            "bullets": n_bullets,
            "is_default": index["default"] == candidate}, ""


def set_default(name: str) -> str:
    index = _load_index()
    if not any(e["name"] == name for e in index["resumes"]):
        return f"resume '{name}' not found"
    index["default"] = name
    _save_index(index)
    return ""


def delete(name: str) -> str:
    index = _load_index()
    entry = next((e for e in index["resumes"] if e["name"] == name), None)
    if entry is None:
        return f"resume '{name}' not found"
    for key in ("file", "docx", "bank"):
        try:
            os.remove(os.path.join(LIB_DIR, entry[key]))
        except (OSError, KeyError, TypeError):
            pass
    index["resumes"] = [e for e in index["resumes"] if e["name"] != name]
    if index["default"] == name:
        index["default"] = index["resumes"][0]["name"] if index["resumes"] else ""
    _save_index(index)
    return ""
