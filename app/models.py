import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, TypeDecorator, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """Always hands back timezone-aware UTC datetimes, even from drivers that return naive values."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class ApplicationStatus(enum.StrEnum):
    SAVED = "saved"
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    GHOSTED = "ghosted"


class WorkMode(enum.StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class EmploymentType(enum.StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    FREELANCE = "freelance"
    TEMPORARY = "temporary"


class Priority(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EventType(enum.StrEnum):
    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    NOTE = "note"


def _enum(enum_cls: type[enum.Enum]) -> Enum:
    # Stored as VARCHAR (not a native PG enum) so new values never need a type migration.
    return Enum(
        enum_cls,
        native_enum=False,
        length=20,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cognito_sub: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    phone_number: Mapped[str | None] = mapped_column(String(32))
    full_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class Preference(Base):
    __tablename__ = "preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    desired_titles: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    work_modes: Mapped[list[str]] = mapped_column(JSON, default=list)
    employment_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    industries: Mapped[list[str]] = mapped_column(JSON, default=list)
    key_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    experience_level: Mapped[str | None] = mapped_column(String(30))
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    open_to_relocation: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class Cv(Base):
    __tablename__ = "cvs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_public_id: Mapped[str] = mapped_column(String(255))
    storage_resource_type: Mapped[str] = mapped_column(String(20))
    storage_format: Mapped[str | None] = mapped_column(String(10))
    size_bytes: Mapped[int] = mapped_column(Integer)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_user_status", "user_id", "status"),
        Index("ix_jobs_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    work_mode: Mapped[WorkMode | None] = mapped_column(_enum(WorkMode))
    employment_type: Mapped[EmploymentType | None] = mapped_column(_enum(EmploymentType))
    job_url: Mapped[str | None] = mapped_column(String(2048))
    source: Mapped[str | None] = mapped_column(String(100))
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(320))

    status: Mapped[ApplicationStatus] = mapped_column(_enum(ApplicationStatus), default=ApplicationStatus.SAVED)
    priority: Mapped[Priority] = mapped_column(_enum(Priority), default=Priority.MEDIUM)
    deadline: Mapped[date | None] = mapped_column(Date)
    applied_on: Mapped[date | None] = mapped_column(Date)

    cv_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cvs.id", ondelete="SET NULL"))
    cv: Mapped[Cv | None] = relationship(lazy="joined")

    poster_public_id: Mapped[str | None] = mapped_column(String(255))
    poster_resource_type: Mapped[str | None] = mapped_column(String(20))
    poster_format: Mapped[str | None] = mapped_column(String(10))
    poster_filename: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    events: Mapped[list["ApplicationEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def has_poster(self) -> bool:
        return self.poster_public_id is not None

    @property
    def poster_kind(self) -> str | None:
        if not self.has_poster:
            return None
        return "pdf" if self.poster_resource_type == "raw" else "image"


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (Index("ix_events_user_occurred", "user_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event_type: Mapped[EventType] = mapped_column(_enum(EventType))
    from_status: Mapped[ApplicationStatus | None] = mapped_column(_enum(ApplicationStatus))
    to_status: Mapped[ApplicationStatus | None] = mapped_column(_enum(ApplicationStatus))
    note: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="events")
