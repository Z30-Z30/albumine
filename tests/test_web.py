"""Tests for the web UI (FastAPI + HTMX routes).

The app is built with test settings (temp dirs) and exercised via TestClient.
Redis is unreachable in tests, so the app runs in its degraded mode — the
gallery and corrections work, queue-backed actions report Redis offline.
"""

import albumine.pipeline as pipeline_module
from albumine.db import ScanRecord, ScanStatus
from albumine.main import create_app


def _seed_record(
    app, app_settings, make_jpeg, *, pair_id="pair-1", status=ScanStatus.NEEDS_REVIEW
):
    """Insert a ScanRecord with real front/back image files on disk."""
    output = make_jpeg(app_settings.output_dir / f"{pair_id}.jpg")
    back = make_jpeg(app_settings.input_dir / f"{pair_id}_b.jpg")
    with app.state.session_factory() as session:
        record = ScanRecord(
            pair_id=pair_id,
            detection_method="image_pair",
            front_path=str(output),
            back_path=str(back),
            output_path=str(output),
            status=status,
            raw_text="Mai 1973, Zürich",
            date_iso="1973-05",
            date_original_text="Mai 1973",
            location="Zürich",
            event="Hochzeit",
        )
        record.people = ["Anna", "Hans"]
        record.source_files = [str(back)]
        session.add(record)
        session.commit()
    return pair_id


def test_gallery_empty(app_settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(app_settings)) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Noch keine Scans" in response.text


def test_gallery_lists_seeded_record(app_settings, make_jpeg):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        _seed_record(app, app_settings, make_jpeg)
        response = client.get("/")
    assert response.status_code == 200
    assert "pair-1" in response.text
    assert "Hochzeit" in response.text


def test_pair_detail_renders_correction_form(app_settings, make_jpeg):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        _seed_record(app, app_settings, make_jpeg)
        response = client.get("/pair/pair-1")
    assert response.status_code == 200
    assert "Extrahierte Daten" in response.text
    assert 'name="raw_text"' in response.text
    assert "Mai 1973" in response.text


def test_pair_detail_unknown_returns_404(app_settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(app_settings)) as client:
        response = client.get("/pair/does-not-exist")
    assert response.status_code == 404


def test_front_and_back_images_are_served(app_settings, make_jpeg):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        _seed_record(app, app_settings, make_jpeg)
        front = client.get("/pair/pair-1/image/front")
        back = client.get("/pair/pair-1/image/back")
    assert front.status_code == 200
    assert front.headers["content-type"] == "image/jpeg"
    assert back.status_code == 200
    assert back.headers["content-type"] == "image/jpeg"


def test_correction_updates_record(app_settings, make_jpeg, monkeypatch):
    from fastapi.testclient import TestClient

    # The app's pipeline writes metadata via ExifTool — stub it out so the test
    # does not depend on the exiftool binary.
    monkeypatch.setattr(pipeline_module, "write_metadata", lambda *a, **k: None)

    app = create_app(app_settings)
    with TestClient(app) as client:
        _seed_record(app, app_settings, make_jpeg, status=ScanStatus.NEEDS_REVIEW)
        response = client.post(
            "/pair/pair-1/correct",
            data={
                "raw_text": "Korrigierter Text",
                "date_text": "Juli 1980",
                "location": "Bern",
                "people": "Oma, Opa",
                "event": "Geburtstag",
                "notes": "",
            },
        )
        assert response.status_code == 200
        assert "gespeichert" in response.text

        with app.state.session_factory() as session:
            record = session.get(ScanRecord, "pair-1")
    assert record.status is ScanStatus.DONE
    assert record.location == "Bern"
    assert record.people == ["Oma", "Opa"]
    assert record.date_iso == "1980-07"


def test_status_dashboard_renders(app_settings, make_jpeg):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        _seed_record(app, app_settings, make_jpeg, status=ScanStatus.FAILED)
        with app.state.session_factory() as session:
            record = session.get(ScanRecord, "pair-1")
            record.error = "etwas ist schiefgelaufen"
            session.add(record)
            session.commit()
        response = client.get("/status")
    assert response.status_code == 200
    assert "Status" in response.text
    assert "Redis ist offline" in response.text  # no Redis in tests


def test_ai_health_fragment(app_settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(app_settings)) as client:
        response = client.get("/status/ai-health")
    assert response.status_code == 200
    assert "ai-health" in response.text


def test_reprocess_without_redis_reports_offline(app_settings, make_jpeg):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        _seed_record(app, app_settings, make_jpeg)
        response = client.post("/pair/pair-1/reprocess")
    assert response.status_code == 200
    assert "Redis ist offline" in response.text


def test_rescan_without_redis_reports_offline(app_settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(app_settings)) as client:
        response = client.post("/rescan")
    assert response.status_code == 200
    assert "Redis ist offline" in response.text


class _FakeArqRedis:
    """Stands in for ArqRedis: dedups on job id like the real enqueue_job."""

    def __init__(self):
        self.jobs: list[str] = []

    async def enqueue_job(self, function, *args, _job_id=None, **kwargs):
        if _job_id in self.jobs:
            return None  # arq: a job with this id is already queued/running
        self.jobs.append(_job_id)
        return object()


def test_rescan_enqueues_scan_job(app_settings):
    from fastapi.testclient import TestClient

    from albumine.api.deps import get_redis

    app = create_app(app_settings)
    redis = _FakeArqRedis()
    app.dependency_overrides[get_redis] = lambda: redis
    with TestClient(app) as client:
        response = client.post("/rescan")
    assert response.status_code == 200
    assert "flash-ok" in response.text
    assert "Input-Ordner wird neu eingelesen" in response.text
    assert redis.jobs == ["scan-input"]


def test_reprocess_enqueues_process_job(app_settings, make_jpeg):
    from fastapi.testclient import TestClient

    from albumine.api.deps import get_redis

    app = create_app(app_settings)
    redis = _FakeArqRedis()
    app.dependency_overrides[get_redis] = lambda: redis
    with TestClient(app) as client:
        _seed_record(app, app_settings, make_jpeg)
        response = client.post("/pair/pair-1/reprocess")
    assert response.status_code == 200
    assert "flash-ok" in response.text
    assert "Re-Processing wurde eingereiht" in response.text
    assert redis.jobs == ["pair:pair-1"]


def test_reprocess_while_job_pending_reports_already_running(app_settings, make_jpeg):
    from fastapi.testclient import TestClient

    from albumine.api.deps import get_redis

    app = create_app(app_settings)
    redis = _FakeArqRedis()
    app.dependency_overrides[get_redis] = lambda: redis
    with TestClient(app) as client:
        _seed_record(app, app_settings, make_jpeg)
        first = client.post("/pair/pair-1/reprocess")
        second = client.post("/pair/pair-1/reprocess")
    assert "flash-ok" in first.text
    assert second.status_code == 200
    assert "flash-warn" in second.text
    assert "läuft noch oder wurde gerade erst abgeschlossen" in second.text
    # The duplicate click must not have enqueued a second job.
    assert redis.jobs == ["pair:pair-1"]


def test_rescan_while_scan_pending_reports_already_running(app_settings):
    from fastapi.testclient import TestClient

    from albumine.api.deps import get_redis

    app = create_app(app_settings)
    redis = _FakeArqRedis()
    app.dependency_overrides[get_redis] = lambda: redis
    with TestClient(app) as client:
        first = client.post("/rescan")
        second = client.post("/rescan")
    assert "flash-ok" in first.text
    assert second.status_code == 200
    assert "flash-warn" in second.text
    assert "Ein Scan läuft bereits" in second.text
    # The duplicate click must not have enqueued a second job.
    assert redis.jobs == ["scan-input"]


def test_404_renders_styled_error_page(app_settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(app_settings)) as client:
        response = client.get("/pair/does-not-exist")
    assert response.status_code == 404
    assert "Fehler 404" in response.text
    assert "Zurück zur Galerie" in response.text


# --- settings panel + i18n --------------------------------------------------


def _settings_form(**overrides):
    """Build a complete settings form payload from the defaults, then override."""
    from albumine.config import Settings
    from albumine.db.settings_store import EDITABLE_SETTINGS

    defaults = Settings()
    form: dict[str, str] = {}
    for spec in EDITABLE_SETTINGS:
        if spec.kind == "secret":
            continue  # secrets: empty/absent means "keep current value"
        value = getattr(defaults, spec.key)
        if spec.kind == "bool":
            if value:  # checkbox semantics: only present when checked
                form[spec.key] = "on"
        else:
            form[spec.key] = "" if value is None else str(value)
    for key, value in overrides.items():
        if value is None:
            form.pop(key, None)
        else:
            form[key] = value
    return form


def test_settings_page_renders(app_settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(app_settings)) as client:
        response = client.get("/settings")
    assert response.status_code == 200
    assert "Einstellungen" in response.text
    assert 'name="ui_language"' in response.text
    assert 'name="jpeg_quality"' in response.text


def test_settings_language_switch_changes_ui(app_settings):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        # Default is German.
        assert "Galerie" in client.get("/").text
        # Switch the UI language to English and persist it.
        saved = client.post("/settings", data=_settings_form(ui_language="en"))
        assert saved.status_code == 200
        # The whole UI now renders in English.
        gallery = client.get("/")
    assert "Gallery" in gallery.text
    assert "Galerie" not in gallery.text


def test_settings_invalid_value_is_rejected(app_settings):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        response = client.post("/settings", data=_settings_form(jpeg_quality="9999"))
        assert response.status_code == 200
        assert "flash-error" in response.text
        # The bad value must not have been persisted.
        with app.state.session_factory() as session:
            from albumine.db import AppSetting

            assert session.get(AppSetting, "jpeg_quality") is None


def test_settings_persist_behaviour_override(app_settings):
    from fastapi.testclient import TestClient

    app = create_app(app_settings)
    with TestClient(app) as client:
        client.post("/settings", data=_settings_form(jpeg_quality="65"))
        with app.state.session_factory() as session:
            from albumine.db import AppSetting

            assert session.get(AppSetting, "jpeg_quality").value == "65"
