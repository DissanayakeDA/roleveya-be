from datetime import date

from fastapi.testclient import TestClient

from app.core.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from app.main import app
from tests.conftest import PDF_BYTES, PNG_BYTES


def _job(client, **overrides):
    payload = {"title": "Backend Engineer", "company": "Acme", "location": "Colombo", **overrides}
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# -- auth ----------------------------------------------------------------------------------------


def test_session_is_200_when_signed_out(client):
    response = client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "user": None}


def test_login_sets_httponly_cookies_and_never_returns_tokens(client):
    response = client.post("/api/auth/login", json={"email": "Ada@Example.com", "password": "Correct-horse1"})
    assert response.status_code == 200
    assert "access-sub-1" not in response.text
    set_cookie = response.headers.get_list("set-cookie")
    assert any(c.startswith(f"{ACCESS_COOKIE}=") and "HttpOnly" in c and "SameSite=strict" in c for c in set_cookie)
    assert any(c.startswith(f"{REFRESH_COOKIE}=") for c in set_cookie)

    session = client.get("/api/auth/session").json()
    assert session["authenticated"] is True
    assert session["user"]["email"] == "sub-1@example.com"


def test_wrong_password(client):
    response = client.post("/api/auth/login", json={"email": "ada@example.com", "password": "nope"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_expired_access_token_is_refreshed_server_side(auth_client, fakes):
    auth_client.cookies.set(ACCESS_COOKIE, "expired-sub-1", path="/api")
    response = auth_client.get("/api/jobs")
    assert response.status_code == 200
    assert fakes["cognito"].refresh_calls == 1
    assert any(c.startswith(f"{ACCESS_COOKIE}=access-sub-1") for c in response.headers.get_list("set-cookie"))


def test_dead_refresh_token_clears_cookies(auth_client):
    auth_client.cookies.set(ACCESS_COOKIE, "expired-sub-1", path="/api")
    auth_client.cookies.set(REFRESH_COOKIE, "revoked", path="/api")
    response = auth_client.get("/api/auth/session")
    assert response.json()["authenticated"] is False
    assert any(c.startswith(f'{ACCESS_COOKIE}=""') for c in response.headers.get_list("set-cookie"))


def test_protected_routes_require_auth(client):
    assert client.get("/api/jobs").status_code == 401


def test_logout_clears_session(auth_client):
    assert auth_client.post("/api/auth/logout").status_code == 204
    assert auth_client.get("/api/auth/session").json()["authenticated"] is False


def test_signup_validates_phone(client):
    response = client.post(
        "/api/auth/signup",
        json={"email": "a@b.co", "full_name": "A", "phone_number": "0771234567", "password": "Correct-horse1"},
    )
    assert response.status_code == 422
    ok = client.post(
        "/api/auth/signup",
        json={"email": "a@b.co", "full_name": "A", "phone_number": "+94 77 123 4567", "password": "Correct-horse1"},
    )
    assert ok.status_code == 201
    assert ok.json()["requires_confirmation"] is True


def test_validation_errors_do_not_echo_input(client):
    response = client.post(
        "/api/auth/signup",
        json={"email": "not-an-email", "full_name": "A", "phone_number": "+94771234567", "password": "S3cret-value!"},
    )
    assert response.status_code == 422
    assert "S3cret-value!" not in response.text


# -- security ------------------------------------------------------------------------------------


def test_state_changing_requests_need_csrf_header(auth_client):
    bare = TestClient(app)
    bare.cookies = auth_client.cookies
    assert bare.post("/api/jobs", json={"title": "x", "company": "y"}).status_code == 403


def test_users_cannot_see_each_others_jobs(auth_client):
    job = _job(auth_client)
    auth_client.cookies.set(ACCESS_COOKIE, "access-someone-else", path="/api")
    assert auth_client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert auth_client.get("/api/jobs").json()["total"] == 0


# -- jobs & history ------------------------------------------------------------------------------


def test_job_crud_and_filters(auth_client):
    job = _job(auth_client, salary_min=100, salary_max=200, salary_currency="lkr", job_url="https://acme.io/jobs/1")
    assert job["status"] == "saved"
    assert job["salary_currency"] == "LKR"
    assert job["created_at"].endswith(("Z", "+00:00")), "timestamps must carry their UTC offset"
    _job(auth_client, title="Data Analyst", company="Globex", status="applied")

    listing = auth_client.get("/api/jobs", params={"q": "glob"}).json()
    assert listing["total"] == 1 and listing["items"][0]["company"] == "Globex"
    assert auth_client.get("/api/jobs", params=[("status", "saved"), ("status", "applied")]).json()["total"] == 2

    updated = auth_client.patch(f"/api/jobs/{job['id']}", json={"notes": "Referral from Sam", "location": ""}).json()
    assert updated["notes"] == "Referral from Sam" and updated["location"] is None

    assert auth_client.patch(f"/api/jobs/{job['id']}", json={"salary_min": 500}).status_code == 422
    assert auth_client.patch(f"/api/jobs/{job['id']}", json={"company": None}).status_code == 422

    assert auth_client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert auth_client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_status_changes_are_recorded(auth_client):
    job = _job(auth_client)
    moved = auth_client.patch(f"/api/jobs/{job['id']}/status", json={"status": "applied", "note": "Via careers page"})
    assert moved.status_code == 200
    assert moved.json()["applied_on"] == date.today().isoformat()
    auth_client.patch(f"/api/jobs/{job['id']}/status", json={"status": "interviewing"})
    auth_client.post(f"/api/jobs/{job['id']}/events", json={"note": "Panel on Friday"})

    events = auth_client.get(f"/api/jobs/{job['id']}/events").json()
    assert [e["event_type"] for e in events] == ["note", "status_changed", "status_changed", "created"]
    assert events[1]["from_status"] == "applied" and events[1]["to_status"] == "interviewing"

    history = auth_client.get("/api/history").json()
    assert history["total"] == 4 and history["items"][0]["job"]["company"] == "Acme"

    status_event = events[1]
    assert auth_client.delete(f"/api/jobs/{job['id']}/events/{status_event['id']}").status_code == 400
    assert auth_client.delete(f"/api/jobs/{job['id']}/events/{events[0]['id']}").status_code == 204


def test_dashboard_summary(auth_client):
    _job(auth_client, deadline="2999-01-01")
    applied = _job(auth_client, title="SRE", status="applied")
    auth_client.patch(f"/api/jobs/{applied['id']}/status", json={"status": "interviewing"})
    summary = auth_client.get("/api/dashboard/summary").json()
    assert summary["total_jobs"] == 2
    assert summary["by_status"]["interviewing"] == 1
    assert summary["active_applications"] == 1
    assert summary["interview_rate"] == 1.0
    assert summary["applied_last_30_days"] == 1
    assert sum(w["count"] for w in summary["weekly_applications"]) == 1
    assert summary["upcoming_deadlines"][0]["deadline"] == "2999-01-01"
    assert len(summary["recent_activity"]) == 3


# -- preferences ---------------------------------------------------------------------------------


def test_preferences_roundtrip(auth_client):
    assert auth_client.get("/api/preferences").json()["desired_titles"] == []
    saved = auth_client.put(
        "/api/preferences",
        json={
            "desired_titles": ["Backend Engineer", "backend engineer", "Platform Engineer"],
            "work_modes": ["remote", "hybrid", "remote"],
            "employment_types": ["full_time"],
            "salary_min": 1000,
            "salary_max": 2000,
            "salary_currency": "usd",
            "experience_level": "senior",
        },
    )
    assert saved.status_code == 200, saved.text
    body = auth_client.get("/api/preferences").json()
    assert body["desired_titles"] == ["Backend Engineer", "Platform Engineer"]
    assert body["work_modes"] == ["remote", "hybrid"]
    assert body["salary_currency"] == "USD"


# -- files ---------------------------------------------------------------------------------------


def test_cv_upload_default_and_delete(auth_client, fakes):
    first = auth_client.post("/api/cvs", files={"file": ("Ada CV.pdf", PDF_BYTES, "application/pdf")})
    assert first.status_code == 201, first.text
    assert first.json()["is_default"] is True and first.json()["label"] == "Ada CV"
    second = auth_client.post(
        "/api/cvs", files={"file": ("cv2.pdf", PDF_BYTES, "application/pdf")}, data={"label": "Data roles"}
    ).json()
    assert second["is_default"] is False

    auth_client.patch(f"/api/cvs/{second['id']}", json={"is_default": True})
    cvs = auth_client.get("/api/cvs").json()
    assert [c["id"] for c in cvs if c["is_default"]] == [second["id"]]

    downloaded = auth_client.get(f"/api/cvs/{second['id']}/file")
    assert downloaded.headers["content-type"] == "application/pdf" and downloaded.content == PDF_BYTES

    job = _job(auth_client, cv_id=second["id"])
    assert job["cv"]["label"] == "Data roles"
    assert auth_client.delete(f"/api/cvs/{second['id']}").status_code == 204
    assert auth_client.get(f"/api/jobs/{job['id']}").json()["cv"] is None
    assert auth_client.get("/api/cvs").json()[0]["is_default"] is True
    assert fakes["storage"].deleted


def test_cv_rejects_non_pdf_even_with_pdf_content_type(auth_client):
    response = auth_client.post("/api/cvs", files={"file": ("cv.pdf", PNG_BYTES, "application/pdf")})
    assert response.status_code == 415


def test_poster_upload_view_and_replace(auth_client, fakes):
    job = _job(auth_client)
    uploaded = auth_client.post(f"/api/jobs/{job['id']}/poster", files={"file": ("poster.png", PNG_BYTES, "image/png")})
    assert uploaded.status_code == 200 and uploaded.json()["poster_kind"] == "image"
    poster = auth_client.get(f"/api/jobs/{job['id']}/poster")
    assert poster.headers["content-type"] == "image/png" and poster.content == PNG_BYTES

    replaced = auth_client.post(f"/api/jobs/{job['id']}/poster", files={"file": ("ad.pdf", PDF_BYTES, "application/pdf")})
    assert replaced.json()["poster_kind"] == "pdf"
    assert len(fakes["storage"].deleted) == 1

    cleared = auth_client.delete(f"/api/jobs/{job['id']}/poster").json()
    assert cleared["has_poster"] is False
