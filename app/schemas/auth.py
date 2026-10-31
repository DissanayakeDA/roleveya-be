import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, StringConstraints, field_validator

from app.schemas.common import OrmModel, required_str

Password = Annotated[str, StringConstraints(min_length=8, max_length=256)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4,8}$")]
# E.164, which is the format Cognito requires for phone_number.
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+[1-9]\d{6,14}$")]


class _EmailModel(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class SignUpRequest(_EmailModel):
    full_name: required_str(200)
    phone_number: Phone
    password: Password

    @field_validator("phone_number", mode="before")
    @classmethod
    def _strip_phone_formatting(cls, value: str) -> str:
        return "".join(ch for ch in value if ch.isdigit() or ch == "+") if isinstance(value, str) else value


class SignUpResult(BaseModel):
    requires_confirmation: bool
    message: str


class ConfirmSignUpRequest(_EmailModel):
    code: Code


class EmailOnlyRequest(_EmailModel):
    pass


class LoginRequest(_EmailModel):
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class ResetPasswordRequest(_EmailModel):
    code: Code
    new_password: Password


class UserOut(OrmModel):
    id: uuid.UUID
    email: str
    phone_number: str | None
    full_name: str | None
    created_at: datetime


class SessionOut(BaseModel):
    authenticated: bool
    user: UserOut | None = None
