from dataclasses import asdict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.deps import get_current_user, get_ocr
from app.core.config import get_settings
from app.models import User
from app.schemas.extract import ExtractedJobOut, ExtractTextIn
from app.services.files import FileKind, read_upload
from app.services.job_extract import extract_job, lines_from_text
from app.services.ocr import InvalidImageError, LocalOcr, OcrUnavailableError

router = APIRouter(prefix="/jobs/extract", tags=["jobs"])

SCREENSHOT_KINDS = {FileKind.PNG, FileKind.JPEG, FileKind.WEBP}


@router.post("/text", response_model=ExtractedJobOut)
def extract_from_text(body: ExtractTextIn, _: User = Depends(get_current_user)):
    lines = lines_from_text(body.text)
    return ExtractedJobOut(found_text=bool(body.text.strip()), **asdict(extract_job(lines)))


@router.post("/screenshot", response_model=ExtractedJobOut)
def extract_from_screenshot(
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
    ocr: LocalOcr = Depends(get_ocr),
):
    data, _kind = read_upload(file, max_mb=get_settings().max_poster_mb, allowed=SCREENSHOT_KINDS)
    try:
        lines = ocr.read_lines(data)
    except InvalidImageError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That image couldn't be opened. Try a PNG or JPG screenshot.")
    except OcrUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Reading screenshots isn't working right now. Paste the text instead."
        )
    return ExtractedJobOut(found_text=bool(lines), **asdict(extract_job(lines)))
