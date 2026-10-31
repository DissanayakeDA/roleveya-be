from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_cognito, get_optional_user
from app.core.config import get_settings
from app.core.cookies import REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from app.db import get_db
from app.models import User
from app.schemas.auth import (
    ConfirmSignUpRequest,
    EmailOnlyRequest,
    LoginRequest,
    ResetPasswordRequest,
    SessionOut,
    SignUpRequest,
    SignUpResult,
    UserOut,
)
from app.schemas.common import MessageOut
from app.services.cognito import AuthServiceError, CognitoService
from app.services.users import upsert_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=SignUpResult, status_code=status.HTTP_201_CREATED)
def sign_up(body: SignUpRequest, cognito: CognitoService = Depends(get_cognito)):
    confirmed = cognito.sign_up(
        email=body.email, password=body.password, phone_number=body.phone_number, full_name=body.full_name
    )
    if confirmed:
        return SignUpResult(requires_confirmation=False, message="Account created. You can sign in now.")
    return SignUpResult(requires_confirmation=True, message="We've emailed you a verification code.")


@router.post("/confirm", response_model=MessageOut)
def confirm_sign_up(body: ConfirmSignUpRequest, cognito: CognitoService = Depends(get_cognito)):
    cognito.confirm_sign_up(email=body.email, code=body.code)
    return MessageOut(message="Email verified. You can sign in now.")


@router.post("/resend-code", response_model=MessageOut)
def resend_code(body: EmailOnlyRequest, cognito: CognitoService = Depends(get_cognito)):
    cognito.resend_confirmation(email=body.email)
    return MessageOut(message="If an unverified account exists for that email, a new code is on its way.")


@router.post("/login", response_model=SessionOut)
def login(
    body: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    cognito: CognitoService = Depends(get_cognito),
):
    tokens = cognito.login(email=body.email, password=body.password)
    user = upsert_user(db, cognito.get_user_attributes(tokens.access_token))
    set_auth_cookies(response, tokens, get_settings())
    return SessionOut(authenticated=True, user=UserOut.model_validate(user))


@router.get("/session", response_model=SessionOut)
def session(user: User | None = Depends(get_optional_user)):
    # Always 200: "not signed in" is an expected answer, not an error the browser should log.
    if user is None:
        return SessionOut(authenticated=False)
    return SessionOut(authenticated=True, user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, cognito: CognitoService = Depends(get_cognito)):
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if refresh_token:
        try:
            cognito.revoke(refresh_token)
        except AuthServiceError:
            pass  # the cookies are cleared regardless
    clear_auth_cookies(response, get_settings())


@router.post("/forgot-password", response_model=MessageOut)
def forgot_password(body: EmailOnlyRequest, cognito: CognitoService = Depends(get_cognito)):
    cognito.forgot_password(email=body.email)
    return MessageOut(message="If an account exists for that email, we've sent a reset code.")


@router.post("/reset-password", response_model=MessageOut)
def reset_password(body: ResetPasswordRequest, cognito: CognitoService = Depends(get_cognito)):
    cognito.confirm_forgot_password(email=body.email, code=body.code, new_password=body.new_password)
    return MessageOut(message="Password updated. You can sign in with your new password.")
