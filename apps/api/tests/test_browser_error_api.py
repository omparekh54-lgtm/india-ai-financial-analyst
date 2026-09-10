from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth import AuthenticatedUser, require_authenticated_user
from app.main import app


def test_browser_error_accepts_anonymous_principal() -> None:
    anonymous = AuthenticatedUser(id=uuid4(), kind="anonymous")
    app.dependency_overrides[require_authenticated_user] = lambda: anonymous
    try:
        response = TestClient(app).post(
            "/v1/system/browser-errors",
            json={"kind": "window_error", "message": "test", "page_path": "/"},
        )
        assert response.status_code == 202
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


def test_browser_errors_are_rate_limited_per_user() -> None:
    user = AuthenticatedUser(id=uuid4())
    app.dependency_overrides[require_authenticated_user] = lambda: user
    try:
        client = TestClient(app)
        for _ in range(5):
            assert client.post(
                "/v1/system/browser-errors",
                json={"kind": "window_error", "message": "test", "page_path": "/"},
            ).status_code == 202
        assert client.post(
            "/v1/system/browser-errors",
            json={"kind": "window_error", "message": "test", "page_path": "/"},
        ).status_code == 429
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)
