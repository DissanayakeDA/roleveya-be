import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth, cvs, dashboard, extract, history, jobs, preferences, studio, tailoring
from app.core.config import get_settings
from app.core.middleware import CSRF_HEADER, ApiGuardMiddleware
from app.db import Base, engine
from app.services.cognito import AuthServiceError

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("roleveya")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Roleveya API",
    version="1.0.0",
    lifespan=lifespan,
    # API docs are a map of every endpoint, so they're only served outside production.
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)

app.add_middleware(ApiGuardMiddleware)
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER],
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    # Rebuilt by hand so submitted values (passwords included) are never echoed back.
    errors = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path"))
        errors.append({"field": field, "message": str(error.get("msg", "Invalid value")).removeprefix("Value error, ")})
    detail = errors[0]["message"] if errors else "Invalid request."
    if errors and errors[0]["field"]:
        detail = f"{errors[0]['field']}: {detail}"
    return JSONResponse({"detail": detail, "errors": errors}, status_code=422)


@app.exception_handler(AuthServiceError)
async def auth_error(_: Request, exc: AuthServiceError):
    return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": "Something went wrong. Please try again."}, status_code=500)


@app.get("/api/health", include_in_schema=False)
def health():
    return {"status": "ok"}


# extract before jobs, so /jobs/extract/... is never read as a job id.
for module in (auth, extract, jobs, tailoring, cvs, studio, preferences, dashboard, history):
    app.include_router(module.router, prefix="/api")
