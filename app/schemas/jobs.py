import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, EmailStr, Field, StringConstraints, model_validator

from app.models import ApplicationStatus, EmploymentType, EventType, Priority, WorkMode
from app.schemas.common import Currency, OrmModel, blank_to_none, optional_str, required_str

JobUrl = Annotated[
    Annotated[str, StringConstraints(max_length=2048, pattern=r"^https?://\S+$")] | None,
    BeforeValidator(blank_to_none),
]
OptionalEmail = Annotated[EmailStr | None, BeforeValidator(blank_to_none)]
Salary = Annotated[int | None, Field(ge=0, le=100_000_000)]
# Optional: many posts are saved with just the company and description. Stored as "" when not given.
JobTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200), BeforeValidator(lambda v: "" if v is None else v)
]


class _JobFields(BaseModel):
    title: JobTitle = ""
    company: required_str(200)
    location: optional_str(200) = None
    work_mode: WorkMode | None = None
    employment_type: EmploymentType | None = None
    job_url: JobUrl = None
    source: optional_str(100) = None
    salary_min: Salary = None
    salary_max: Salary = None
    salary_currency: Currency = None
    description: optional_str(20_000) = None
    notes: optional_str(5_000) = None
    contact_name: optional_str(200) = None
    contact_email: OptionalEmail = None
    priority: Priority = Priority.MEDIUM
    deadline: date | None = None
    applied_on: date | None = None
    cv_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _salary_range(self):
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("Minimum salary can't be higher than maximum salary.")
        return self


class JobCreate(_JobFields):
    status: ApplicationStatus = ApplicationStatus.SAVED


class JobUpdate(BaseModel):
    """Partial update. Status is changed through the status endpoint so every change is recorded."""

    title: JobTitle = ""
    company: required_str(200) | None = None
    location: optional_str(200) = None
    work_mode: WorkMode | None = None
    employment_type: EmploymentType | None = None
    job_url: JobUrl = None
    source: optional_str(100) = None
    salary_min: Salary = None
    salary_max: Salary = None
    salary_currency: Currency = None
    description: optional_str(20_000) = None
    notes: optional_str(5_000) = None
    contact_name: optional_str(200) = None
    contact_email: OptionalEmail = None
    priority: Priority | None = None
    deadline: date | None = None
    applied_on: date | None = None
    cv_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _required_fields_not_cleared(self):
        for name in ("company", "priority"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name.capitalize()} can't be empty.")
        return self


class StatusChange(BaseModel):
    status: ApplicationStatus
    note: optional_str(2_000) = None
    occurred_at: datetime | None = None


class NoteCreate(BaseModel):
    note: required_str(2_000)
    occurred_at: datetime | None = None


class JobSort(StrEnum):
    UPDATED = "updated"
    CREATED = "created"
    DEADLINE = "deadline"
    APPLIED = "applied"
    COMPANY = "company"


class CvSummary(OrmModel):
    id: uuid.UUID
    label: str
    original_filename: str
    # Set when the CV is a tailored version saved from the CV studio.
    version_id: uuid.UUID | None = None


class JobOut(OrmModel):
    id: uuid.UUID
    title: str
    company: str
    location: str | None
    work_mode: WorkMode | None
    employment_type: EmploymentType | None
    job_url: str | None
    source: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    description: str | None
    notes: str | None
    contact_name: str | None
    contact_email: str | None
    status: ApplicationStatus
    priority: Priority
    deadline: date | None
    applied_on: date | None
    cv: CvSummary | None
    has_poster: bool
    poster_kind: str | None
    poster_filename: str | None
    created_at: datetime
    updated_at: datetime


class JobListItem(OrmModel):
    id: uuid.UUID
    title: str
    company: str
    location: str | None
    work_mode: WorkMode | None
    status: ApplicationStatus
    priority: Priority
    deadline: date | None
    applied_on: date | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    has_poster: bool
    updated_at: datetime


class JobPage(BaseModel):
    items: list[JobListItem]
    total: int
    page: int
    page_size: int


class JobRef(OrmModel):
    id: uuid.UUID
    title: str
    company: str


class EventOut(OrmModel):
    id: uuid.UUID
    job_id: uuid.UUID
    event_type: EventType
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus | None
    note: str | None
    occurred_at: datetime


class EventWithJob(EventOut):
    job: JobRef


class EventPage(BaseModel):
    items: list[EventWithJob]
    total: int
    page: int
    page_size: int
