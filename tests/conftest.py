import os

# Configure before the app is imported: settings and the engine are built at import time.
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": "sqlite://",
        "COGNITO_USER_POOL_ID": "us-east-1_test",
        "COGNITO_CLIENT_ID": "test-client",
        "COGNITO_CLIENT_SECRET": "",
        "CLOUDINARY_CLOUD_NAME": "test",
        "CLOUDINARY_API_KEY": "key",
        "CLOUDINARY_API_SECRET": "secret",
        "COOKIE_SECURE": "false",
        "CORS_ORIGINS": "",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_cognito, get_ocr, get_storage, get_token_verifier  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.cognito import AuthServiceError, AuthTokens  # noqa: E402
from app.services.job_extract import TextLine  # noqa: E402
from app.services.storage import StoredFile  # noqa: E402
from app.services.tokens import TokenError, TokenExpiredError  # noqa: E402

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeCognito:
    """Tokens look like "access-<sub>"; "expired-<sub>" simulates an expired access token."""

    def __init__(self):
        self.refresh_calls = 0

    def sign_up(self, **_):
        return False

    def confirm_sign_up(self, *, email, code):
        if code != "123456":
            raise AuthServiceError(400, "invalid_code", "That verification code is incorrect.")

    def login(self, *, email, password):
        if password != "Correct-horse1":
            raise AuthServiceError(401, "invalid_credentials", "Incorrect email or password.")
        return AuthTokens("access-sub-1", "refresh-sub-1", 3600)

    def refresh(self, *, refresh_token, username):
        self.refresh_calls += 1
        if refresh_token != "refresh-sub-1":
            raise AuthServiceError(401, "session_expired", "expired")
        return AuthTokens("access-sub-1", None, 3600)

    def get_user_attributes(self, access_token):
        sub = access_token.removeprefix("access-")
        return {"sub": sub, "email": f"{sub}@example.com", "phone_number": "+94771234567", "name": "Ada"}

    def revoke(self, refresh_token):
        pass

    def resend_confirmation(self, **_):
        pass

    def forgot_password(self, **_):
        pass

    def confirm_forgot_password(self, **_):
        pass


class FakeVerifier:
    def verify_access_token(self, token):
        if token.startswith("expired-"):
            raise TokenExpiredError()
        if not token.startswith("access-"):
            raise TokenError()
        sub = token.removeprefix("access-")
        return {"sub": sub, "username": sub, "token_use": "access"}


class FakeStorage:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload(self, data, *, folder, kind):
        public_id = f"roleveya/{folder}/{len(self.files)}" + ("" if kind.is_image else ".pdf")
        self.files[public_id] = data
        return StoredFile(public_id, "image" if kind.is_image else "raw", kind.value if kind.is_image else None, len(data))

    def fetch(self, *, public_id, resource_type, file_format):
        return self.files[public_id]

    def delete(self, *, public_id, resource_type):
        self.deleted.append(public_id)


class FakeOcr:
    """Returns whatever lines the test sets, as if read from the uploaded screenshot."""

    def __init__(self):
        self.lines: list[TextLine] = []

    def read_lines(self, data):
        return self.lines


@pytest.fixture
def fakes():
    cognito, storage, ocr = FakeCognito(), FakeStorage(), FakeOcr()
    app.dependency_overrides[get_cognito] = lambda: cognito
    app.dependency_overrides[get_token_verifier] = lambda: FakeVerifier()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_ocr] = lambda: ocr
    yield {"cognito": cognito, "storage": storage, "ocr": ocr}
    app.dependency_overrides.clear()


@pytest.fixture
def client(fakes):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app, headers={"X-Requested-With": "roleveya"}) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client):
    response = client.post("/api/auth/login", json={"email": "ada@example.com", "password": "Correct-horse1"})
    assert response.status_code == 200
    return client
