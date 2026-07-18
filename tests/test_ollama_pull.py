"""Tests for the Ollama model-pull status bar (settings panel)."""

import albumine.api.settings_panel as settings_panel
from albumine.api.settings_panel import OllamaPullState
from albumine.main import create_app


def test_pull_requires_model_name(app_settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(app_settings)) as client:
        response = client.post("/settings/ollama/pull", data={"ollama_vision_model": " "})
    assert "flash-error" in response.text
    assert "Modellnamen" in response.text


def test_pull_starts_and_reports_success(app_settings, monkeypatch):
    from fastapi.testclient import TestClient

    seen = {}

    async def _fake_pull(host, state):
        seen["host"] = host
        seen["model"] = state.model
        state.status = "success"
        state.done = True

    monkeypatch.setattr(settings_panel, "_run_ollama_pull", _fake_pull)

    with TestClient(create_app(app_settings)) as client:
        client.post(
            "/settings/ollama/pull", data={"ollama_vision_model": "llava:13b"}
        )
        status = client.get("/settings/ollama/pull-status")

    assert seen["model"] == "llava:13b"
    assert seen["host"] == app_settings.ollama_host
    assert "flash-ok" in status.text
    assert "llava:13b" in status.text


def test_pull_status_shows_progress_and_polls(app_settings):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        state = OllamaPullState(model="llava", status="downloading")
        state.layers = {"sha256:aa": (50, 100), "sha256:bb": (25, 100)}
        app.state.ollama_pull = state
        response = client.get("/settings/ollama/pull-status")

    assert "37%" in response.text  # 75 of 200 bytes
    assert "downloading" in response.text
    # Still running -> the fragment re-polls itself.
    assert 'hx-get="/settings/ollama/pull-status"' in response.text


def test_pull_while_running_warns_and_keeps_first_pull(app_settings, monkeypatch):
    from fastapi.testclient import TestClient

    async def _fake_pull(host, state):  # pragma: no cover - must not be called
        raise AssertionError("second pull must not start")

    monkeypatch.setattr(settings_panel, "_run_ollama_pull", _fake_pull)

    app = create_app(app_settings)
    with TestClient(app) as client:
        running = OllamaPullState(model="llava", status="downloading")
        app.state.ollama_pull = running
        response = client.post(
            "/settings/ollama/pull", data={"ollama_vision_model": "qwen2.5vl"}
        )

    assert "flash-warn" in response.text
    assert app.state.ollama_pull is running  # first pull untouched


def test_pull_error_is_shown(app_settings):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        state = OllamaPullState(model="nope", error="pull model manifest: file does not exist")
        app.state.ollama_pull = state
        response = client.get("/settings/ollama/pull-status")

    assert "flash-error" in response.text
    assert "file does not exist" in response.text
    # Terminal state -> no more polling.
    assert "hx-get" not in response.text


def test_settings_page_contains_pull_button(app_settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(app_settings)) as client:
        page = client.get("/settings")
    assert 'hx-post="/settings/ollama/pull"' in page.text
    assert "Modell herunterladen" in page.text
