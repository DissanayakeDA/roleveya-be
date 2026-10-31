from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from app.db import get_db
from app.models import User
from app.services.ai import GroqClient
from app.services.cognito import AuthServiceError, CognitoService
from app.services.latex import LatexCompiler, WordConverter
from app.services.ocr import LocalOcr
from app.services.storage import CloudinaryStorage
from app.services.tokens import (
    CognitoTokenVerifier,
    TokenError,
    TokenExpiredError,
    TokenVerifierUnavailable,
    unverified_username,
)
from app.services.users import upsert_user


@lru_cache
def get_cognito() -> CognitoService:
    return CognitoService(get_settings())


@lru_cache
def get_token_verifier() -> CognitoTokenVerifier:
    return CognitoTokenVerifier(get_settings())


@lru_cache
def get_storage() -> CloudinaryStorage:
    return CloudinaryStorage(get_settings())


@lru_cache
def get_ocr() -> LocalOcr:
    return LocalOcr()


@lru_cache
def get_latex() -> LatexCompiler:
    return LatexCompiler(get_settings())


@lru_cache
def get_word() -> WordConverter:
    return WordConverter()


@lru_cache
def get_ai() -> GroqClient:
    return GroqClient(get_settings())


def _verify_or_none(verifier: CognitoTokenVerifier, token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return verifier.verify_access_token(token)
    except (TokenExpiredError, TokenError):
        return None
    except TokenVerifierUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.")


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
    cognito: CognitoService = Depends(get_cognito),
    verifier: CognitoTokenVerifier = Depends(get_token_verifier),
) -> User | None:
    """Resolves the signed-in user from cookies, refreshing an expired access token server-side.

    Refreshing here (instead of the browser retrying after a 401) keeps token handling out of the
    frontend entirely. Rotated tokens are written back as cookies by ApiGuardMiddleware.
    """
    access_token = request.cookies.get(ACCESS_COOKIE)
    claims = _verify_or_none(verifier, access_token)

    if claims is None:
        refresh_token = request.cookies.get(REFRESH_COOKIE)
        if not refresh_token:
            if access_token:
                request.state.clear_auth_cookies = True
            return None
        try:
            tokens = cognito.refresh(refresh_token=refresh_token, username=unverified_username(access_token))
        except AuthServiceError as exc:
            if exc.status_code >= 500:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.")
            request.state.clear_auth_cookies = True
            return None
        claims = _verify_or_none(verifier, tokens.access_token)
        if claims is None:
            request.state.clear_auth_cookies = True
            return None
        request.state.new_tokens = tokens
        access_token = tokens.access_token

    user = db.scalar(select(User).where(User.cognito_sub == claims["sub"]))
    if user is None:
        try:
            attributes = cognito.get_user_attributes(access_token)
        except AuthServiceError:
            request.state.clear_auth_cookies = True
            return None
        if attributes.get("sub") != claims["sub"]:
            request.state.clear_auth_cookies = True
            return None
        user = upsert_user(db, attributes)
    return user


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in to continue.")
    return user
