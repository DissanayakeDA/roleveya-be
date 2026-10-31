"""CV studio: master CVs in LaTeX, compiling to PDF and Word, PDF-to-LaTeX, and saved (unchangeable) versions."""

import base64
import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_ai, get_current_user, get_latex, get_ocr, get_storage, get_word
from app.core.config import get_settings
from app.db import get_db
from app.models import Cv, CvDocument, CvVersion, Job, User
from app.schemas.studio import (
    CompileIn,
    CompileOut,
    ConvertName,
    DocumentCreate,
    DocumentOut,
    DocumentSummary,
    DocumentUpdate,
    IssueOut,
    SnapshotCreate,
    StudioStatus,
    TemplateId,
    TemplateOut,
    VersionOut,
    VersionSummary,
    WordIn,
)
from app.services.ai import AiBadResponseError, AiRateLimitedError, AiTooLargeError, AiUnavailableError, GroqClient
from app.services.cv_ai import NotACvError, structure_cv_text
from app.services.cv_templates import TEMPLATES, render_cv, starter_latex
from app.services.files import FileKind, clean_filename, content_disposition, read_upload
from app.services.latex import (
    LatexBusyError,
    LatexCompileError,
    LatexCompiler,
    LatexUnavailableError,
    WordConverter,
    WordUnavailableError,
)
from app.services.ocr import LocalOcr
from app.services.pdf_text import InvalidPdfError, pdf_text
from app.services.storage import CloudinaryStorage, StorageError

router = APIRouter(tags=["cv studio"])

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# -- shared with tailoring ---------------------------------------------------------------------


def check_size(latex: str) -> None:
    limit = get_settings().max_latex_kb
    if len(latex.encode("utf-8")) > limit * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"LaTeX source must be {limit} KB or smaller.")


def ai_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AiRateLimitedError):
        wait = f" Try again in {_duration(exc.retry_after)}." if exc.retry_after else " Try again in a minute."
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "The AI's free usage limit has been reached." + wait)
    if isinstance(exc, AiTooLargeError):
        return HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "That's too much text for the AI's free limit. Shorten the job description or the CV and try again.",
        )
    if isinstance(exc, AiBadResponseError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI's reply couldn't be used. Try again.")
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AI suggestions aren't available right now. Try again later.")


def compile_or_raise(compiler: LatexCompiler, latex: str) -> bytes:
    try:
        return compiler.compile(latex)
    except LatexCompileError as exc:
        first = exc.issues[0]
        where = f"Line {first.line}: " if first.line else ""
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The CV didn't compile. {where}{first.message}.")
    except (LatexUnavailableError, LatexBusyError) as exc:
        raise _compiler_error(exc)


def create_version(
    db: Session,
    user: User,
    storage: CloudinaryStorage,
    compiler: LatexCompiler,
    *,
    name: str,
    latex: str,
    job: Job | None,
    document: CvDocument | None,
) -> CvVersion:
    settings = get_settings()
    count = db.scalar(select(func.count()).select_from(CvVersion).where(CvVersion.user_id == user.id)) or 0
    if count >= settings.max_cv_versions_per_user:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"You can keep up to {settings.max_cv_versions_per_user} saved versions. Delete some on the CVs page.",
        )
    check_size(latex)
    pdf = compile_or_raise(compiler, latex)
    try:
        stored = storage.upload(pdf, folder=f"cvs/{user.id}", kind=FileKind.PDF)
    except StorageError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Saving the PDF failed. Please try again.")

    has_cvs = db.scalar(select(Cv.id).where(Cv.user_id == user.id).limit(1)) is not None
    cv = Cv(
        user_id=user.id,
        label=name,
        original_filename=clean_filename(f"{name}.pdf", "cv.pdf"),
        storage_public_id=stored.public_id,
        storage_resource_type=stored.resource_type,
        storage_format=stored.format,
        size_bytes=stored.size_bytes,
        is_default=not has_cvs,
    )
    version = CvVersion(
        user_id=user.id,
        cv=cv,
        job_id=job.id if job else None,
        document_id=document.id if document else None,
        name=name,
        latex=latex,
        latex_sha256=hashlib.sha256(latex.encode("utf-8")).hexdigest(),
    )
    db.add_all([cv, version])
    return version


def _compiler_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LatexBusyError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The PDF compiler is busy. Try again in a moment.")
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "PDF compiling isn't set up on the server yet (Tectonic isn't installed)."
    )


def _duration(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} seconds"
    minutes = round(seconds / 60)
    return f"{minutes} minutes" if minutes < 90 else f"{round(minutes / 60)} hours"


def _owned_document(db: Session, user: User, document_id: uuid.UUID) -> CvDocument:
    document = db.scalar(select(CvDocument).where(CvDocument.id == document_id, CvDocument.user_id == user.id))
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CV not found.")
    return document


def _owned_version(db: Session, user: User, version_id: uuid.UUID) -> CvVersion:
    version = db.scalar(select(CvVersion).where(CvVersion.id == version_id, CvVersion.user_id == user.id))
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found.")
    return version


def _check_master_limit(db: Session, user: User) -> None:
    limit = get_settings().max_cv_documents_per_user
    count = db.scalar(
        select(func.count()).select_from(CvDocument).where(CvDocument.user_id == user.id, CvDocument.job_id.is_(None))
    ) or 0
    if count >= limit:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"You can keep up to {limit} master CVs.")


def _docx_response(data: bytes, name: str) -> Response:
    filename = clean_filename(f"{name}.docx", "cv.docx")
    return Response(
        content=data,
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": content_disposition(filename, inline=False)},
    )


def _to_word(word: WordConverter, latex: str) -> bytes:
    try:
        return word.convert(latex)
    except LatexCompileError as exc:
        first = exc.issues[0]
        where = f"Line {first.line}: " if first.line else ""
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The Word file couldn't be made. {where}{first.message}.")
    except WordUnavailableError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Word export isn't set up on the server (pandoc is missing).")


# -- status ------------------------------------------------------------------------------------


@router.get("/studio/status", response_model=StudioStatus)
def studio_status(
    _: User = Depends(get_current_user),
    compiler: LatexCompiler = Depends(get_latex),
    word: WordConverter = Depends(get_word),
    ai: GroqClient = Depends(get_ai),
):
    return StudioStatus(
        ai=ai.available,
        compiler=compiler.available,
        word=word.available,
        templates=[TemplateOut(id=t.id, name=t.name, description=t.description) for t in TEMPLATES.values()],
    )


# -- master CVs --------------------------------------------------------------------------------


@router.get("/cv-documents", response_model=list[DocumentSummary])
def list_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(CvDocument)
        .where(CvDocument.user_id == user.id, CvDocument.job_id.is_(None))
        .order_by(CvDocument.updated_at.desc())
    ).all()


@router.post("/cv-documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def create_document(body: DocumentCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _check_master_limit(db, user)
    latex = body.latex if body.latex and body.latex.strip() else starter_latex(body.template or "classic")
    check_size(latex)
    document = CvDocument(
        user_id=user.id, name=body.name, latex=latex, template=None if body.latex else body.template
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@router.post("/cv-documents/convert", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def convert_pdf(
    file: UploadFile | None = File(None),
    cv_id: uuid.UUID | None = Form(None),
    template: TemplateId = Form("classic"),
    name: ConvertName = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
    ocr: LocalOcr = Depends(get_ocr),
    ai: GroqClient = Depends(get_ai),
):
    """Reads a CV PDF (an upload, or one of the user's CVs) and lays its content out in a template."""
    _check_master_limit(db, user)
    if not ai.available:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Converting a PDF needs the AI, which isn't set up yet.")
    if file is not None:
        data, _ = read_upload(file, max_mb=get_settings().max_cv_mb, allowed={FileKind.PDF})
        source_name = clean_filename(file.filename, "cv.pdf").rsplit(".", 1)[0]
    elif cv_id is not None:
        cv = db.scalar(select(Cv).where(Cv.id == cv_id, Cv.user_id == user.id))
        if cv is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "CV not found.")
        try:
            data = storage.fetch(
                public_id=cv.storage_public_id, resource_type=cv.storage_resource_type, file_format=cv.storage_format
            )
        except StorageError:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "That CV is temporarily unavailable.")
        source_name = cv.label
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a PDF to convert.")

    try:
        text = pdf_text(data, ocr)
    except InvalidPdfError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "That PDF couldn't be opened. If it has a password, remove it and try again."
        )
    if sum(char.isalpha() for char in text) < 50:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No text could be read from that PDF.")
    try:
        cv_data = structure_cv_text(ai, text)
    except NotACvError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That PDF doesn't look like a CV.")
    except (AiUnavailableError, AiRateLimitedError, AiTooLargeError, AiBadResponseError) as exc:
        raise ai_error(exc)

    document = CvDocument(
        user_id=user.id, name=name or f"{source_name} (LaTeX)"[:120], latex=render_cv(cv_data, template), template=template
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@router.get("/cv-documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _owned_document(db, user, document_id)


@router.patch("/cv-documents/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: uuid.UUID, body: DocumentUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    document = _owned_document(db, user, document_id)
    if body.name is not None:
        document.name = body.name
    if body.latex is not None:
        check_size(body.latex)
        document.latex = body.latex
    db.commit()
    db.refresh(document)
    return document


@router.delete("/cv-documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    document = _owned_document(db, user, document_id)
    # Saved versions keep their own copy of the LaTeX, so they outlive the document they came from.
    for version in db.scalars(select(CvVersion).where(CvVersion.document_id == document.id)):
        version.document_id = None
    for copy in db.scalars(select(CvDocument).where(CvDocument.based_on_id == document.id)):
        copy.based_on_id = None
    db.delete(document)
    db.commit()


@router.post("/cv-documents/{document_id}/versions", response_model=VersionSummary, status_code=status.HTTP_201_CREATED)
def snapshot_document(
    document_id: uuid.UUID,
    body: SnapshotCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
    compiler: LatexCompiler = Depends(get_latex),
):
    """Saves the master CV as it is now as a PDF CV, which can then be picked as the CV sent for a job."""
    document = _owned_document(db, user, document_id)
    version = create_version(
        db, user, storage, compiler, name=body.name, latex=document.latex, job=None, document=document
    )
    db.commit()
    db.refresh(version)
    return version


# -- compiling ---------------------------------------------------------------------------------


@router.post("/latex/compile", response_model=CompileOut)
def compile_latex(
    body: CompileIn, _: User = Depends(get_current_user), compiler: LatexCompiler = Depends(get_latex)
):
    check_size(body.latex)
    try:
        pdf = compiler.compile(body.latex)
    except LatexCompileError as exc:
        return CompileOut(ok=False, issues=[IssueOut(line=i.line, message=i.message) for i in exc.issues])
    except (LatexUnavailableError, LatexBusyError) as exc:
        raise _compiler_error(exc)
    return CompileOut(ok=True, pdf_base64=base64.b64encode(pdf).decode("ascii"))


@router.post("/latex/docx")
def latex_to_word(body: WordIn, _: User = Depends(get_current_user), word: WordConverter = Depends(get_word)):
    check_size(body.latex)
    return _docx_response(_to_word(word, body.latex), body.filename or "CV")


# -- saved versions ----------------------------------------------------------------------------


@router.get("/cv-versions", response_model=list[VersionSummary])
def list_versions(
    job_id: uuid.UUID | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    stmt = select(CvVersion).where(CvVersion.user_id == user.id)
    if job_id is not None:
        stmt = stmt.where(CvVersion.job_id == job_id)
    return db.scalars(stmt.order_by(CvVersion.created_at.desc())).all()


@router.get("/cv-versions/{version_id}", response_model=VersionOut)
def get_version(version_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _owned_version(db, user, version_id)


@router.get("/cv-versions/{version_id}/docx")
def version_to_word(
    version_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    word: WordConverter = Depends(get_word),
):
    version = _owned_version(db, user, version_id)
    return _docx_response(_to_word(word, version.latex), version.name)
