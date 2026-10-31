import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_storage
from app.core.config import get_settings
from app.db import get_db
from app.models import Cv, Job, User
from app.schemas.cvs import CvLabel, CvOut, CvUpdate
from app.services.files import FileKind, clean_filename, content_disposition, read_upload
from app.services.storage import CloudinaryStorage, StorageError

router = APIRouter(prefix="/cvs", tags=["cvs"])


def _get_owned_cv(db: Session, user: User, cv_id: uuid.UUID) -> Cv:
    cv = db.scalar(select(Cv).where(Cv.id == cv_id, Cv.user_id == user.id))
    if cv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CV not found.")
    return cv


def _make_default(db: Session, user: User, cv: Cv) -> None:
    db.execute(update(Cv).where(Cv.user_id == user.id, Cv.id != cv.id).values(is_default=False))
    cv.is_default = True


@router.get("", response_model=list[CvOut])
def list_cvs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Cv).where(Cv.user_id == user.id).order_by(Cv.is_default.desc(), Cv.created_at.desc())).all()


@router.post("", response_model=CvOut, status_code=status.HTTP_201_CREATED)
def upload_cv(
    file: UploadFile = File(...),
    label: CvLabel = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
):
    settings = get_settings()
    count = db.scalar(select(func.count()).select_from(Cv).where(Cv.user_id == user.id)) or 0
    if count >= settings.max_cvs_per_user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"You can keep up to {settings.max_cvs_per_user} CVs.")

    data, _ = read_upload(file, max_mb=settings.max_cv_mb, allowed={FileKind.PDF})
    filename = clean_filename(file.filename, "cv.pdf")
    try:
        stored = storage.upload(data, folder=f"cvs/{user.id}", kind=FileKind.PDF)
    except StorageError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Upload failed. Please try again.")

    cv = Cv(
        user_id=user.id,
        label=label or filename.rsplit(".", 1)[0][:120] or "My CV",
        original_filename=filename,
        storage_public_id=stored.public_id,
        storage_resource_type=stored.resource_type,
        storage_format=stored.format,
        size_bytes=stored.size_bytes,
        is_default=count == 0,
    )
    db.add(cv)
    db.commit()
    db.refresh(cv)
    return cv


@router.patch("/{cv_id}", response_model=CvOut)
def update_cv(
    cv_id: uuid.UUID, body: CvUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    cv = _get_owned_cv(db, user, cv_id)
    if body.label is not None:
        cv.label = body.label
    if body.is_default:
        _make_default(db, user, cv)
    db.commit()
    db.refresh(cv)
    return cv


@router.get("/{cv_id}/file")
def download_cv(
    cv_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
):
    cv = _get_owned_cv(db, user, cv_id)
    try:
        data = storage.fetch(
            public_id=cv.storage_public_id, resource_type=cv.storage_resource_type, file_format=cv.storage_format
        )
    except StorageError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "This CV is temporarily unavailable.")
    return Response(
        content=data,
        media_type=FileKind.PDF.media_type,
        headers={"Content-Disposition": content_disposition(cv.original_filename)},
    )


@router.delete("/{cv_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cv(
    cv_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: CloudinaryStorage = Depends(get_storage),
):
    cv = _get_owned_cv(db, user, cv_id)
    was_default = cv.is_default
    stored = (cv.storage_public_id, cv.storage_resource_type)

    db.execute(update(Job).where(Job.cv_id == cv.id).values(cv_id=None))
    db.delete(cv)
    db.flush()
    if was_default:
        successor = db.scalar(select(Cv).where(Cv.user_id == user.id).order_by(Cv.created_at.desc()).limit(1))
        if successor is not None:
            successor.is_default = True
    db.commit()
    storage.delete(public_id=stored[0], resource_type=stored[1])
