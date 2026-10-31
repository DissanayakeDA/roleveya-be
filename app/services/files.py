import os
import re
from enum import StrEnum
from urllib.parse import quote

from fastapi import HTTPException, UploadFile, status


class FileKind(StrEnum):
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpg"
    WEBP = "webp"

    @property
    def media_type(self) -> str:
        return {
            FileKind.PDF: "application/pdf",
            FileKind.PNG: "image/png",
            FileKind.JPEG: "image/jpeg",
            FileKind.WEBP: "image/webp",
        }[self]

    @property
    def is_image(self) -> bool:
        return self is not FileKind.PDF

    @classmethod
    def from_format(cls, file_format: str | None) -> "FileKind":
        normalised = (file_format or "").lower()
        return cls.JPEG if normalised == "jpeg" else next((k for k in cls if k.value == normalised), cls.PNG)


def sniff(data: bytes) -> FileKind | None:
    """Detects the file type from its content; the client-supplied content type is never trusted."""
    if b"%PDF-" in data[:1024]:
        return FileKind.PDF
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileKind.PNG
    if data[:3] == b"\xff\xd8\xff":
        return FileKind.JPEG
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return FileKind.WEBP
    return None


def read_upload(file: UploadFile, *, max_mb: int, allowed: set[FileKind]) -> tuple[bytes, FileKind]:
    max_bytes = max_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    if len(data) > max_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Files must be {max_mb} MB or smaller.")
    kind = sniff(data)
    if kind is None or kind not in allowed:
        names = ", ".join(sorted(k.value.upper() for k in allowed))
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported file type. Allowed: {names}.")
    return data, kind


def clean_filename(name: str | None, fallback: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)
    return (base or fallback)[:255]


def content_disposition(filename: str, inline: bool = True) -> str:
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", filename) or "file"
    disposition = "inline" if inline else "attachment"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
