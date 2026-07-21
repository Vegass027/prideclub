from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_health() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_unknown_path_returns_404() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"


def test_api_v1_without_init_data_returns_401() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/v1/me")
        assert r.status_code == 401
        assert r.json()["code"] == "missing_init_data"


def test_internal_without_service_token_returns_401() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/internal/something")
        assert r.status_code == 401
        assert r.json()["code"] == "missing_service_token"