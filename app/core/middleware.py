from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.cookies import clear_auth_cookies, set_auth_cookies

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "roleveya"


class ApiGuardMiddleware(BaseHTTPMiddleware):
    """CSRF check, auth cookie rotation and security headers for every API response.

    CSRF: auth cookies are SameSite=Strict, and state-changing requests must also carry a custom header,
    which a cross-site form or script cannot add without a CORS preflight the API would reject.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in UNSAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return self._harden(JSONResponse({"detail": "Request blocked."}, status_code=403))

        response = await call_next(request)

        settings = get_settings()
        new_tokens = getattr(request.state, "new_tokens", None)
        if new_tokens is not None:
            set_auth_cookies(response, new_tokens, settings)
        elif getattr(request.state, "clear_auth_cookies", False):
            clear_auth_cookies(response, settings)
        return self._harden(response)

    @staticmethod
    def _harden(response: Response) -> Response:
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
