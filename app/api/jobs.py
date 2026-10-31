import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_storage
from app.core.config import get_settings
from app.db import get_db
from app.models import ApplicationEvent, ApplicationStatus, Cv, EventType, Job, Priority, User, WorkMode, utcnow
from app.schemas.jobs import EventOut, JobCreate, JobOut, JobPage, JobSort, JobUpdate, NoteCreate, StatusChange
from app.services.files import FileKind, clean_filename, content_disposition, read_upload
from app.services.storage import CloudinaryStorage, StorageError

router = APIRouter(prefix="/jobs", tags=["jobs"])

POSTER_KINDS = {FileKind.PDF, FileKind.PNG, FileKind.JPEG, FileKind.WEBP}


def get_owned_job(db: Session, user: User, job_id: uuid.UUID) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    return job


def _ensure_cv_owned(db: Session, user: User, cv_id: uuid.UUID | None) -> None:
    if cv_id is not None and db.scalar(select(Cv.id).where(Cv.id == cv_id, Cv.user_id == user.id)) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The selected CV no longer exists.")


def _mark_applied_if_needed(job: Job) -> None:
    if job.status != ApplicationStatus.SAVED and job.applied_on is None:
        job.applied_on = date.today()


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_SORTS = {
    JobSort.UPDATED: (Job.updated_at.desc(),),
    JobSort.CREATED: (Job.created_at.desc(),),
    JobSort.DEADLINE: (Job.deadline.asc().nulls_last(), Job.updated_at.desc()),
    JobSort.APPLIED: (Job.applied_on.desc().nulls_last(), Job.updated_at.desc()),
    JobSort.COMPANY: (Job.company.asc(), Job.title.asc()),
}


@router.get("", response_model=JobPage)
def list_jobs(
    status_filter: list[ApplicationStatus] | None = Query(None, alias="status"),
    q: str | None = Query(None, max_length=100),
    work_mode: WorkMode | None = None,
    priority: Priority | None = None,
    sort: JobSort = JobSort.UPDATED,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Job).where(Job.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Job.status.in_(status_filter))
    if q and q.strip():
        pattern = _like(q.strip())
        stmt = stmt.where(
            or_(
                Job.title.ilike(pattern, escape="\\"),
                Job.company.ilike(pattern, escape="\\"),
                Job.location.ilike(pattern, escape="\\"),
            )
        )
    if work_mode:
        stmt = stmt.where(Job.work_mode == work_mode)
    if priority:
        stmt = stmt.where(Job.priority == priority)

    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = db.scalars(stmt.order_by(*_SORTS[sort]).offset((page - 1) * page_size).limit(page_size)).all()
    return JobPage(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_job(body: JobCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_cv_owned(db, user, body.cv_id)
    job = Job(user_id=user.id, **body.model_dump())
    _mark_applied_if_needed(job)
    job.events.append(ApplicationEvent(user_id=user.id, event_type=EventType.CREATED, to_status=job.status))
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned_job(db, user, job_id)


@router.patch("/{job_id}", response_model=JobOut)
def update_job(
    job_id: uuid.UUID, body: JobUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    job = get_owned_job(db, user, job_id)
    changes = body.model_dump(exclude_unset=True)
    if "cv_id" in changes:
        _ensure_cv_owned(db, user, changes["cv_id"])
    salary_min = changes.get("salary_min", job.salary_min)
    salary_max = changes.get("salary_max", job.salary_max)
    if salary_min is not None and salary_max is not None and salary_min > salary_max:
        raise HTTPException(422, "Minimum salary can't be higher than maximum salary.")
    for field, value in changes.items():
        setattr(job, field, value)
    db.commit()
    db.refresh(job)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
):
    job = get_owned_job(db, user, job_id)
    poster = (job.poster_public_id, job.poster_resource_type)
    db.delete(job)
    db.commit()
    if poster[0]:
        storage.delete(public_id=poster[0], resource_type=poster[1])


@router.patch("/{job_id}/status", response_model=JobOut)
def change_status(
    job_id: uuid.UUID, body: StatusChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    job = get_owned_job(db, user, job_id)
    if body.status == job.status and not body.note:
        return job
    job.events.append(
        ApplicationEvent(
            user_id=user.id,
            event_type=EventType.STATUS_CHANGED if body.status != job.status else EventType.NOTE,
            from_status=job.status if body.status != job.status else None,
            to_status=body.status if body.status != job.status else None,
            note=body.note,
            occurred_at=body.occurred_at or utcnow(),
        )
    )
    job.status = body.status
    _mark_applied_if_needed(job)
    job.updated_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


# -- application history ---------------------------------------------------------------------


@router.get("/{job_id}/events", response_model=list[EventOut])
def list_job_events(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = get_owned_job(db, user, job_id)
    return db.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.job_id == job.id)
        .order_by(ApplicationEvent.occurred_at.desc(), ApplicationEvent.created_at.desc())
    ).all()


@router.post("/{job_id}/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def add_note(
    job_id: uuid.UUID, body: NoteCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    job = get_owned_job(db, user, job_id)
    event = ApplicationEvent(
        job_id=job.id,
        user_id=user.id,
        event_type=EventType.NOTE,
        note=body.note,
        occurred_at=body.occurred_at or utcnow(),
    )
    db.add(event)
    job.updated_at = utcnow()
    db.commit()
    db.refresh(event)
    return event


@router.delete("/{job_id}/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    job_id: uuid.UUID, event_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    job = get_owned_job(db, user, job_id)
    event = db.scalar(select(ApplicationEvent).where(ApplicationEvent.id == event_id, ApplicationEvent.job_id == job.id))
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry not found.")
    if event.event_type != EventType.NOTE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only notes can be removed from the history.")
    db.delete(event)
    db.commit()


# -- job poster ------------------------------------------------------------------------------


@router.post("/{job_id}/poster", response_model=JobOut)
def upload_poster(
    job_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
):
    job = get_owned_job(db, user, job_id)
    data, kind = read_upload(file, max_mb=get_settings().max_poster_mb, allowed=POSTER_KINDS)
    try:
        stored = storage.upload(data, folder=f"posters/{user.id}", kind=kind)
    except StorageError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Upload failed. Please try again.")

    previous = (job.poster_public_id, job.poster_resource_type)
    job.poster_public_id = stored.public_id
    job.poster_resource_type = stored.resource_type
    job.poster_format = stored.format
    job.poster_filename = clean_filename(file.filename, f"poster.{kind.value}")
    db.commit()
    db.refresh(job)
    if previous[0]:
        storage.delete(public_id=previous[0], resource_type=previous[1])
    return job


@router.get("/{job_id}/poster")
def get_poster(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
):
    job = get_owned_job(db, user, job_id)
    if not job.has_poster:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This job has no poster.")
    try:
        data = storage.fetch(
            public_id=job.poster_public_id, resource_type=job.poster_resource_type, file_format=job.poster_format
        )
    except StorageError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The poster is temporarily unavailable.")
    kind = FileKind.PDF if job.poster_kind == "pdf" else FileKind.from_format(job.poster_format)
    return Response(
        content=data,
        media_type=kind.media_type,
        headers={"Content-Disposition": content_disposition(job.poster_filename or f"poster.{kind.value}")},
    )


@router.delete("/{job_id}/poster", response_model=JobOut)
def delete_poster(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
):
    job = get_owned_job(db, user, job_id)
    if job.has_poster:
        storage.delete(public_id=job.poster_public_id, resource_type=job.poster_resource_type)
        job.poster_public_id = job.poster_resource_type = job.poster_format = job.poster_filename = None
        db.commit()
        db.refresh(job)
    return job
