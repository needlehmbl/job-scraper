"""Re-export of the Phase 1 Postgres helpers for the API layer."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import connect, upsert_job, record_run, find_existing  # noqa: F401,E402
from db import DATABASE_URL, ensure_tracking_schema, record_status_change  # noqa: F401,E402
from db import (  # noqa: F401,E402
    ensure_filtered_schema,
    save_filtered_jobs,
    list_filtered_jobs,
    restore_filtered_job,
    delete_filtered_job,
)