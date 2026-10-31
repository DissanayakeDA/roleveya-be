import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, model_validator

from app.schemas.common import OrmModel, optional_str, required_str
from app.schemas.jobs import JobRef
from app.services.cv_ai import CvEdit, SkillGap

# Characters, not bytes; MAX_LATEX_KB is checked in bytes by the endpoints.
Latex = Annotated[str, StringConstraints(min_length=1, max_length=400_000)]
TemplateId = Literal["classic", "modern"]


class TemplateOut(BaseModel):
    id: str
    name: str
    description: str


class StudioStatus(BaseModel):
    """What the server can do, so the UI can explain a missing feature instead of failing a request."""

    ai: bool
    compiler: bool
    word: bool
    templates: list[TemplateOut]


class DocumentCreate(BaseModel):
    name: required_str(120)
    template: TemplateId | None = None
    latex: Latex | None = None

    @model_validator(mode="after")
    def _one_source(self):
        if not self.latex and not self.template:
            raise ValueError("Choose a template or paste your LaTeX.")
        return self


class DocumentUpdate(BaseModel):
    name: required_str(120) | None = None
    latex: Latex | None = None


class DocumentSummary(OrmModel):
    id: uuid.UUID
    name: str
    template: str | None
    job_id: uuid.UUID | None
    based_on_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class DocumentOut(DocumentSummary):
    latex: str


class CompileIn(BaseModel):
    latex: Latex


class IssueOut(BaseModel):
    line: int | None
    message: str


class CompileOut(BaseModel):
    """A failed compile is a normal result while editing, so it's a 200 with ok=false and the issues."""

    ok: bool
    pdf_base64: str | None = None
    issues: list[IssueOut] = []


class WordIn(BaseModel):
    latex: Latex
    filename: optional_str(120) = None


class VersionCreate(BaseModel):
    name: required_str(120)
    latex: Latex
    # Tailoring only: record this version as the CV sent for the job.
    use_for_job: bool = True


class SnapshotCreate(BaseModel):
    name: required_str(120)


class VersionSummary(OrmModel):
    id: uuid.UUID
    name: str
    cv_id: uuid.UUID
    job: JobRef | None
    document_id: uuid.UUID | None
    created_at: datetime


class VersionOut(VersionSummary):
    latex: str


class DraftCreate(BaseModel):
    master_id: uuid.UUID


class AnalysisIn(BaseModel):
    latex: Latex
    # Ask the AI again even if this CV and post were already compared.
    refresh: bool = False


class AnalysisOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    model: str
    # False once the CV or the job post has changed since this analysis was made.
    current: bool
    summary: str
    matched_skills: list[str]
    missing_skills: list[SkillGap]
    edits: list[CvEdit]
    keywords: list[str]


class TailoringOut(BaseModel):
    draft: DocumentOut | None
    analysis: AnalysisOut | None
    versions: list[VersionSummary]


ConvertName = optional_str(120)
