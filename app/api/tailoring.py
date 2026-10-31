"""Tailoring a CV for one job: a working copy of a master CV, AI suggestions, and saving the result as a version."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_ai, get_current_user, get_latex, get_storage
from app.api.jobs import get_owned_job
from app.api.studio import ai_error, check_size, create_version
from app.db import get_db
from app.models import CvAnalysis, CvDocument, CvVersion, Job, User
from app.schemas.studio import AnalysisIn, AnalysisOut, DocumentOut, DraftCreate, TailoringOut, VersionCreate, VersionSummary
from app.services.ai import AiBadResponseError, AiRateLimitedError, AiTooLargeError, AiUnavailableError, GroqClient
from app.services.cv_ai import AnalysisResult, analyse_cv, analysis_input_hash
from app.services.latex import LatexCompiler
from app.services.storage import CloudinaryStorage

router = APIRouter(prefix="/jobs/{job_id}/tailoring", tags=["cv studio"])

KEEP_ANALYSES = 5
MIN_DESCRIPTION = 80


def _draft(db: Session, job: Job) -> CvDocument | None:
    return db.scalar(select(CvDocument).where(CvDocument.job_id == job.id))


def _input_hash(ai: GroqClient, job: Job, latex: str) -> str:
    return analysis_input_hash(ai.model, job.title, job.company, job.description or "", latex)


def _analysis_out(analysis: CvAnalysis, current_hash: str | None) -> AnalysisOut:
    result = AnalysisResult.model_validate(analysis.result)
    return AnalysisOut(
        id=analysis.id,
        created_at=analysis.created_at,
        model=analysis.model,
        current=analysis.input_sha256 == current_hash,
        **result.model_dump(),
    )


@router.get("", response_model=TailoringOut)
def get_tailoring(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ai: GroqClient = Depends(get_ai),
):
    job = get_owned_job(db, user, job_id)
    draft = _draft(db, job)
    latest = db.scalar(
        select(CvAnalysis).where(CvAnalysis.job_id == job.id).order_by(CvAnalysis.created_at.desc()).limit(1)
    )
    versions = db.scalars(
        select(CvVersion).where(CvVersion.job_id == job.id).order_by(CvVersion.created_at.desc())
    ).all()
    current_hash = _input_hash(ai, job, draft.latex) if draft else None
    return TailoringOut(
        draft=draft,
        analysis=_analysis_out(latest, current_hash) if latest else None,
        versions=versions,
    )


@router.post("/draft", response_model=DocumentOut)
def start_draft(
    job_id: uuid.UUID, body: DraftCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Starts (or restarts) the job's working copy from a master CV."""
    job = get_owned_job(db, user, job_id)
    master = db.scalar(
        select(CvDocument).where(
            CvDocument.id == body.master_id, CvDocument.user_id == user.id, CvDocument.job_id.is_(None)
        )
    )
    if master is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Master CV not found.")
    name = f"{master.name} for {job.company}"[:120]
    draft = _draft(db, job)
    if draft is None:
        draft = CvDocument(user_id=user.id, job_id=job.id, name=name, latex=master.latex)
        db.add(draft)
    draft.name, draft.latex, draft.template, draft.based_on_id = name, master.latex, master.template, master.id
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/analysis", response_model=AnalysisOut)
def analyse(
    job_id: uuid.UUID,
    body: AnalysisIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ai: GroqClient = Depends(get_ai),
):
    """Compares the CV being tailored with the job post. A CV and post already compared aren't sent again."""
    job = get_owned_job(db, user, job_id)
    check_size(body.latex)
    if len((job.description or "").strip()) < MIN_DESCRIPTION:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add the job description to this job first.")
    draft = _draft(db, job)
    if draft is not None and draft.latex != body.latex:
        draft.latex = body.latex

    input_hash = _input_hash(ai, job, body.latex)
    if not body.refresh:
        cached = db.scalar(
            select(CvAnalysis)
            .where(CvAnalysis.job_id == job.id, CvAnalysis.input_sha256 == input_hash)
            .order_by(CvAnalysis.created_at.desc())
            .limit(1)
        )
        if cached is not None:
            db.commit()
            return _analysis_out(cached, input_hash)

    try:
        result = analyse_cv(
            ai, title=job.title, company=job.company, description=job.description or "", latex=body.latex
        )
    except (AiUnavailableError, AiRateLimitedError, AiTooLargeError, AiBadResponseError) as exc:
        db.commit()
        raise ai_error(exc)

    analysis = CvAnalysis(
        user_id=user.id, job_id=job.id, input_sha256=input_hash, model=ai.model, result=result.model_dump()
    )
    db.add(analysis)
    db.flush()
    keep = select(CvAnalysis.id).where(CvAnalysis.job_id == job.id).order_by(CvAnalysis.created_at.desc()).limit(KEEP_ANALYSES)
    db.execute(delete(CvAnalysis).where(CvAnalysis.job_id == job.id, CvAnalysis.id.not_in(keep.scalar_subquery())))
    db.commit()
    db.refresh(analysis)
    return _analysis_out(analysis, input_hash)


@router.post("/versions", response_model=VersionSummary, status_code=status.HTTP_201_CREATED)
def save_version(
    job_id: uuid.UUID,
    body: VersionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
    compiler: LatexCompiler = Depends(get_latex),
):
    """Compiles the tailored CV and saves it, unchangeable, as a CV linked to this job."""
    job = get_owned_job(db, user, job_id)
    draft = _draft(db, job)
    version = create_version(db, user, storage, compiler, name=body.name, latex=body.latex, job=job, document=draft)
    if draft is not None:
        draft.latex = body.latex
    if body.use_for_job:
        db.flush()
        job.cv_id = version.cv_id
    db.commit()
    db.refresh(version)
    return version
