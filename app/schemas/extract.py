from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.models import EmploymentType, WorkMode


class ExtractTextIn(BaseModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=30_000)]


class ExtractedJobOut(BaseModel):
    """What was found in a post. Fields that weren't found are null; the form leaves them for the user."""

    found_text: bool
    company: str | None = None
    title: str | None = None
    location: str | None = None
    work_mode: WorkMode | None = None
    employment_type: EmploymentType | None = None
    job_url: str | None = None
    source: str | None = None
    description: str | None = None
