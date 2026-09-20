"""
Manages applications.xlsx -- your "clean sheet" for reviewing scraped leads.
One row per job. Existing rows are preserved on re-runs and duplicate jobs
are skipped rather than re-added.
"""
import os

import pandas as pd

COLUMNS = [
    "status",        # NEW / REVIEWED / APPLIED / REJECTED / SKIP / MISMATCH / EXP_GAP / EXPIRED / DUPLICATE
    "title",
    "company",
    "location",
    "date_posted",
    "job_url",
    "search_term",
    "notes",
]


def load_or_init(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        df = pd.read_excel(path)
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = ""
        return df[COLUMNS]
    return pd.DataFrame(columns=COLUMNS)


def upsert(df: pd.DataFrame, row: dict) -> pd.DataFrame:
    key = row["job_url"]
    if key in df.get("job_url", pd.Series(dtype=str)).values:
        idx = df.index[df["job_url"] == key][0]
        for k, v in row.items():
            df.at[idx, k] = v
    else:
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    return df


def save(df: pd.DataFrame, path: str):
    df = df.copy()
    df["date_posted"] = pd.to_datetime(df["date_posted"], errors="coerce")
    df = (
        df.sort_values("date_posted", ascending=False, na_position="last")
        .reset_index(drop=True)
    )
    df.to_excel(path, index=False)
    print(f"[tracker] wrote {len(df)} rows to {path}")