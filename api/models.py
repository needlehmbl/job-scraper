"""Pydantic schemas for the job-scraper API."""
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

STATUSES = {"NEW", "REVIEWED", "APPLIED", "SKIP", "REJECTED",
            "MISMATCH", "EXP_GAP", "EXPIRED", "DUPLICATE"}


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


class JobStatusUpdate(BaseModel):
    status: str = Field(pattern=r"^(NEW|REVIEWED|APPLIED|SKIP|REJECTED|MISMATCH|EXP_GAP|EXPIRED|DUPLICATE)$")


class FollowUpUpdate(BaseModel):
    follow_up_at: date | None = None


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