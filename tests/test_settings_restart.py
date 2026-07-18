"""Tests for the in-app server restart (POST /settings/restart)."""

from types import SimpleNamespace

import albumine.api.settings_panel as settings_panel
from albumine.main import create_app


class _FakeSupervisorRPC:
    """Stands in for supervisord's XML-RPC interface."""

    def __init__(self, *, reachable=True):
        self.reachable = reachable
        self.restarted = False

    def _proxy(self, url):
        rpc = self

        class _Supervisor:
            def getState(self):  # noqa: N802 - supervisord API name
                if not rpc.reachable:
                    raise ConnectionRefusedError("connection refused")
                return {"statename": "RUNNING"}

            def restart(self):
                if not rpc.reachable:
                    raise ConnectionRefusedError("connection refused")
                rpc.restarted = True
                return True

        return SimpleNamespace(supervisor=_Supervisor())


def test_restart_triggers_supervisor(app_settings, monkeypatch):
    from fastapi.testclient import TestClient

    rpc = _FakeSupervisorRPC()
    monkeypatch.setattr(settings_panel, "_supervisor", rpc._proxy)
    monkeypatch.setattr(settings_panel, "_RESTART_DELAY_SECONDS", 0)

    with TestClient(create_app(app_settings)) as client:
        response = client.post("/settings/restart")

    assert response.status_code == 200
    assert "flash-ok" in response.text
    assert "Server startet neu" in response.text
    assert "/healthz" in response.text  # the auto-reload script is included
    # TestClient runs background tasks before returning, so by now the
    # actual restart call must have happened.
    assert rpc.restarted is True


def test_restart_reports_unreachable_supervisor(app_settings, monkeypatch):
    from fastapi.testclient import TestClient

    rpc = _FakeSupervisorRPC(reachable=False)
    monkeypatch.setattr(settings_panel, "_supervisor", rpc._proxy)

    with TestClient(create_app(app_settings)) as client:
        response = client.post("/settings/restart")

    assert response.status_code == 200
    assert "flash-error" in response.text
    assert "Neustart fehlgeschlagen" in response.text
    assert rpc.restarted is False


def test_restart_disabled_without_supervisor_url(app_settings):
    from fastapi.testclient import TestClient

    app_settings.supervisor_url = None
    with TestClient(create_app(app_settings)) as client:
        response = client.post("/settings/restart")
        page = client.get("/settings")

    assert "flash-error" in response.text
    assert "nicht verfügbar" in response.text
    # The button is hidden when no supervisor is configured.
    assert "/settings/restart" not in page.text


def test_settings_page_shows_restart_button(app_settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(app_settings)) as client:
        page = client.get("/settings")

    assert 'hx-post="/settings/restart"' in page.text
    assert "Server neu starten" in page.text
