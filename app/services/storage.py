"""Cloudinary-backed private file storage.

Files are uploaded with type="authenticated", so they are not reachable by public URL. The API fetches
them with a signed URL and streams the bytes to the browser, which never sees the Cloudinary URL.
"""

import io
import logging
import uuid
from dataclasses import dataclass

import cloudinary
import cloudinary.uploader
import cloudinary.utils
import httpx

from app.core.config import Settings
from app.services.files import FileKind

logger = logging.getLogger(__name__)


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class StoredFile:
    public_id: str
    resource_type: str
    format: str | None
    size_bytes: int


class CloudinaryStorage:
    def __init__(self, settings: Settings):
        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )
        self._root = settings.cloudinary_folder.strip("/")

    def upload(self, data: bytes, *, folder: str, kind: FileKind) -> StoredFile:
        # PDFs go up as "raw" so Cloudinary serves the original bytes untouched.
        resource_type = "image" if kind.is_image else "raw"
        name = uuid.uuid4().hex + ("" if kind.is_image else ".pdf")
        try:
            result = cloudinary.uploader.upload(
                io.BytesIO(data),
                public_id=f"{self._root}/{folder}/{name}",
                resource_type=resource_type,
                type="authenticated",
                overwrite=False,
            )
        except Exception as exc:  # the SDK raises a mix of its own and requests/urllib3 errors
            logger.warning("Cloudinary upload failed: %s", type(exc).__name__)
            raise StorageError() from exc
        return StoredFile(
            public_id=result["public_id"],
            resource_type=result.get("resource_type", resource_type),
            format=result.get("format") or (kind.value if kind.is_image else None),
            size_bytes=int(result.get("bytes") or len(data)),
        )

    def fetch(self, *, public_id: str, resource_type: str, file_format: str | None) -> bytes:
        options = {"resource_type": resource_type, "type": "authenticated", "sign_url": True, "secure": True}
        if resource_type == "image" and file_format:
            options["format"] = file_format
        url, _ = cloudinary.utils.cloudinary_url(public_id, **options)
        try:
            response = httpx.get(url, timeout=20.0, follow_redirects=True)
        except httpx.HTTPError as exc:
            logger.warning("Cloudinary fetch failed: %s", type(exc).__name__)
            raise StorageError() from exc
        if response.status_code != 200:
            # Cloudinary explains refusals in this header (e.g. PDF delivery turned off for the account).
            reason = response.headers.get("x-cld-error", "no reason given")
            logger.warning("Cloudinary fetch returned %s: %s", response.status_code, reason)
            raise StorageError()
        return response.content

    def delete(self, *, public_id: str, resource_type: str) -> None:
        try:
            cloudinary.uploader.destroy(public_id, resource_type=resource_type, type="authenticated", invalidate=True)
        except Exception as exc:
            # Best effort: an orphaned private asset is harmless and shouldn't block the user.
            logger.warning("Cloudinary delete failed for an asset: %s", type(exc).__name__)
