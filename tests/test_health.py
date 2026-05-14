"""Smoke tests for the Phase 1 app skeleton."""

from fastapi.testclient import TestClient

from albumine.main import create_app


def test_healthz_returns_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_index_renders_landing_page() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "AlbuMine" in response.text
