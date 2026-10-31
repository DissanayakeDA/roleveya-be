from starlette.responses import Response

from app.core.config import Settings
from app.services.cognito import AuthTokens

ACCESS_COOKIE = "rv_at"
REFRESH_COOKIE = "rv_rt"
COOKIE_PATH = "/api"


def _common(settings: Settings) -> dict:
    return {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "strict",
        "path": COOKIE_PATH,
        "domain": settings.cookie_domain,
    }


def set_auth_cookies(response: Response, tokens: AuthTokens, settings: Settings) -> None:
    # The access cookie outlives the JWT inside it on purpose: once the JWT expires the API refreshes it
    # server-side, and needs the expired token's username to do so when the app client has a secret.
    max_age = settings.refresh_token_days * 24 * 3600
    response.set_cookie(ACCESS_COOKIE, tokens.access_token, max_age=max_age, **_common(settings))
    if tokens.refresh_token:
        response.set_cookie(REFRESH_COOKIE, tokens.refresh_token, max_age=max_age, **_common(settings))


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(name, **_common(settings))
