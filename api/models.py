"""Pydantic schemas for the job-scraper API."""
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

STATUSES = {"NEW", "REVIEWED", "APPLIED", "SKIP", "REJECTED",
            "MISMATCH", "EXP_GAP", "EXPIRED", "DUPLICATE",
            "INTERVIEW_INITIAL", "INTERVIEW_TECHNICAL", "INTERVIEW_FINAL",
            "INTERVIEW_OUT", "OFFER", "OFFER_ACCEPTED", "OFFER_DECLINED"}

# Funnel stages shown in the dashboard's Interviews tab (in process).
INTERVIEW_STATUSES = {"INTERVIEW_INITIAL", "INTERVIEW_TECHNICAL",
                      "INTERVIEW_FINAL", "INTERVIEW_OUT"}
# Outcome stages shown in the dashboard's Offers tab.
OFFER_STATUSES = {"OFFER", "OFFER_ACCEPTED", "OFFER_DECLINED"}
# Pipeline rows "move out" of the main Jobs tab into those tabs.
PIPELINE_STATUSES = INTERVIEW_STATUSES | OFFER_STATUSES

_STATUS_PATTERN = (r"^(NEW|REVIEWED|APPLIED|SKIP|REJECTED|MISMATCH|EXP_GAP|EXPIRED|DUPLICATE"
                   r"|INTERVIEW_INITIAL|INTERVIEW_TECHNICAL|INTERVIEW_FINAL|INTERVIEW_OUT"
                   r"|OFFER|OFFER_ACCEPTED|OFFER_DECLINED)$")


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
    offer_salary: str = ""
    offer_benefits: str = ""
    offer_pros: str = ""
    offer_cons: str = ""


class JobStatusUpdate(BaseModel):
    status: str = Field(pattern=_STATUS_PATTERN)


class FollowUpUpdate(BaseModel):
    follow_up_at: date | None = None


class OfferUpdate(BaseModel):
    offer_salary: str | None = None
    offer_benefits: str | None = None
    offer_pros: str | None = None
    offer_cons: str | None = None


class HistoryEntry(BaseModel):
    old_status: str | None = None
    new_status: str
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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)