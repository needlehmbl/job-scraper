"""Pydantic schemas for the job-scraper API."""
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

STATUSES = {"NEW", "REVIEWED", "APPLIED", "SKIP",
            "MISMATCH", "EXP_GAP", "EXPIRED", "DUPLICATE"}

# Hiring-funnel stages for APPLIED rows (tracked in the dashboard's
# Applications tab). Triage `status` and pipeline `stage` are separate
# axes; invariant: stage is set ⟺ status is APPLIED.
STAGES = {"APPLIED", "INITIAL", "TECHNICAL", "FINAL",
          "OFFER", "ACCEPTED", "DECLINED", "OUT"}
INTERVIEW_STAGES = {"INITIAL", "TECHNICAL", "FINAL", "OUT"}
OFFER_STAGES = {"OFFER", "ACCEPTED", "DECLINED"}

_STATUS_PATTERN = (r"^(NEW|REVIEWED|APPLIED|SKIP|MISMATCH|EXP_GAP"
                   r"|EXPIRED|DUPLICATE)$")
_STAGE_PATTERN = (r"^(APPLIED|INITIAL|TECHNICAL|FINAL"
                  r"|OFFER|ACCEPTED|DECLINED|OUT)$")


class Job(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    title: str
    company: str
    url: str
    location: str | None = None
    date_posted: date | None = None
    status: str
    scraped_at: datetime
    last_seen: datetime | None = None
    applied_at: datetime | None = None
    status_updated_at: datetime | None = None
    score: int = 0
    score_reason: str = ""
    follow_up_at: date | None = None
    stage: str | None = None
    offer_salary: str = ""
    offer_benefits: str = ""
    offer_pros: str = ""
    offer_cons: str = ""


class JobStatusUpdate(BaseModel):
    status: str = Field(pattern=_STATUS_PATTERN)


class FollowUpUpdate(BaseModel):
    follow_up_at: date | None = None


class StageUpdate(BaseModel):
    stage: str = Field(pattern=_STAGE_PATTERN)


class OfferUpdate(BaseModel):
    offer_salary: str | None = None
    offer_benefits: str | None = None
    offer_pros: str | None = None
    offer_cons: str | None = None


class JobDetailsUpdate(BaseModel):
    """Manual correction of a tracked row's identity fields.

    All optional; only non-None fields are changed. Blank-after-strip
    title/company are rejected (400) since empty identity breaks dedupe.
    """

    title: str | None = Field(default=None, max_length=500)
    company: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)


class HistoryEntry(BaseModel):
    kind: str = "status"  # status | stage
    old_status: str | None = None
    new_status: str | None = None
    old_stage: str | None = None
    new_stage: str | None = None
    changed_at: datetime | None = None


class FilteredJob(BaseModel):
    """A posting the feedback filter held out of `jobs`, awaiting review."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str = ""
    title: str = ""
    company: str = ""
    url: str = ""
    location: str | None = None
    date_posted: date | None = None
    description: str | None = None
    search_term: str | None = None
    filter_reason: str = ""
    filtered_at: datetime | None = None
    restored: bool = False
    # Only set by POST /filtered/{fid}/restore: the jobs row the posting was
    # moved into (NEW when the restore created it, otherwise the status that
    # row already had) so the dashboard can track and undo the move.
    job_id: int | None = None
    job_status: str | None = None


class FilteredReinsert(BaseModel):
    """Snapshot of a dismissed filtered posting, for undo of delete."""

    source: str = ""
    title: str = ""
    company: str = ""
    url: str = ""
    location: str | None = None
    date_posted: date | None = None
    description: str | None = None
    search_term: str | None = None
    filter_reason: str = ""


class ManualAddRequest(BaseModel):
    """Manual intake: one externally-found posting URL -> Applications.

    title/company/location are optional user-typed overrides (the modal):
    non-blank values win over auto-fetched ones (covers Indeed's bot-wall).
    """

    url: str = Field(min_length=8, max_length=2000)
    title: str | None = Field(default=None, max_length=500)
    company: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)