from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.db import get_db
from app.models import ApplicationEvent, ApplicationStatus, Job, User
from app.schemas.dashboard import DashboardSummary, DeadlineItem, WeeklyCount

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

S = ApplicationStatus
ACTIVE = (S.APPLIED, S.SCREENING, S.INTERVIEWING, S.OFFER)
RESPONDED = (S.SCREENING, S.INTERVIEWING, S.OFFER, S.ACCEPTED, S.REJECTED)
INTERVIEWED = (S.INTERVIEWING, S.OFFER, S.ACCEPTED)
WEEKS = 8


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


@router.get("/summary", response_model=DashboardSummary)
def summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    counts = dict(
        db.execute(select(Job.status, func.count()).where(Job.user_id == user.id).group_by(Job.status)).all()
    )
    by_status = {s.value: counts.get(s, 0) for s in ApplicationStatus}
    total = sum(by_status.values())
    applied_total = total - by_status[S.SAVED]

    today = date.today()
    first_week = today - timedelta(days=today.weekday(), weeks=WEEKS - 1)
    applied_dates = db.scalars(
        select(Job.applied_on).where(Job.user_id == user.id, Job.applied_on >= min(first_week, today - timedelta(days=30)))
    ).all()
    weekly = {first_week + timedelta(weeks=i): 0 for i in range(WEEKS)}
    for applied in applied_dates:
        week_start = applied - timedelta(days=applied.weekday())
        if week_start in weekly:
            weekly[week_start] += 1

    deadlines = db.scalars(
        select(Job)
        .where(Job.user_id == user.id, Job.status == S.SAVED, Job.deadline >= today)
        .order_by(Job.deadline.asc())
        .limit(5)
    ).all()

    recent = db.scalars(
        select(ApplicationEvent)
        .options(joinedload(ApplicationEvent.job))
        .where(ApplicationEvent.user_id == user.id)
        .order_by(ApplicationEvent.occurred_at.desc(), ApplicationEvent.created_at.desc())
        .limit(8)
    ).all()

    return DashboardSummary(
        total_jobs=total,
        by_status=by_status,
        active_applications=sum(by_status[s] for s in ACTIVE),
        applied_last_30_days=sum(1 for d in applied_dates if d >= today - timedelta(days=30)),
        response_rate=_rate(sum(by_status[s] for s in RESPONDED), applied_total),
        interview_rate=_rate(sum(by_status[s] for s in INTERVIEWED), applied_total),
        weekly_applications=[WeeklyCount(week_start=k, count=v) for k, v in weekly.items()],
        upcoming_deadlines=[DeadlineItem.model_validate(job, from_attributes=True) for job in deadlines],
        recent_activity=recent,
    )
