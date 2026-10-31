from datetime import date

from pydantic import BaseModel

from app.schemas.jobs import EventWithJob, JobRef


class WeeklyCount(BaseModel):
    week_start: date
    count: int


class DeadlineItem(JobRef):
    deadline: date


class DashboardSummary(BaseModel):
    total_jobs: int
    by_status: dict[str, int]
    active_applications: int
    applied_last_30_days: int
    response_rate: float | None
    interview_rate: float | None
    weekly_applications: list[WeeklyCount]
    upcoming_deadlines: list[DeadlineItem]
    recent_activity: list[EventWithJob]
