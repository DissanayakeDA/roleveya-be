import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.common import OrmModel, optional_str, required_str


class CvOut(OrmModel):
    id: uuid.UUID
    label: str
    original_filename: str
    size_bytes: int
    is_default: bool
    created_at: datetime


class CvUpdate(BaseModel):
    label: required_str(120) | None = None
    # Only "make default" is supported; another CV becoming default is how one stops being default.
    is_default: Literal[True] | None = None


CvLabel = optional_str(120)
