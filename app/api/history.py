from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.db import get_db
from app.models import ApplicationEvent, EventType, User
from app.schemas.jobs import EventPage

router = APIRouter(prefix="/history", tags=["history"])


@router.get("", response_model=EventPage)
def application_history(
    event_type: EventType | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every recorded change across all of the user's applications, newest first."""
    stmt = select(ApplicationEvent).where(ApplicationEvent.user_id == user.id)
    if event_type:
        stmt = stmt.where(ApplicationEvent.event_type == event_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.options(joinedload(ApplicationEvent.job))
        .order_by(ApplicationEvent.occurred_at.desc(), ApplicationEvent.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return EventPage(items=items, total=total, page=page, page_size=page_size)
