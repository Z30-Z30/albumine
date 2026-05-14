"""Tests for the database layer."""

from sqlmodel import Session, select

from albumine.db import ScanRecord, ScanStatus, create_db_engine, init_db


def test_scan_record_json_list_accessors():
    record = ScanRecord(pair_id="abc", detection_method="image_pair", front_path="/in/a.jpg")
    record.source_files = ["/in/a.jpg", "/in/b.jpg"]
    record.people = ["Anna", "Hans"]

    assert record.source_files_json == '["/in/a.jpg", "/in/b.jpg"]'
    assert record.source_files == ["/in/a.jpg", "/in/b.jpg"]
    assert record.people == ["Anna", "Hans"]


def test_init_db_creates_table_and_roundtrips(app_settings):
    app_settings.config_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(app_settings.database_url)
    init_db(engine)

    with Session(engine) as session:
        record = ScanRecord(
            pair_id="pair-1",
            detection_method="pdf_duplex",
            front_path="/in/scan.pdf",
            status=ScanStatus.DONE,
        )
        record.people = ["Oma"]
        session.add(record)
        session.commit()

    with Session(engine) as session:
        loaded = session.get(ScanRecord, "pair-1")
        assert loaded is not None
        assert loaded.status is ScanStatus.DONE
        assert loaded.people == ["Oma"]

        done = session.exec(
            select(ScanRecord).where(ScanRecord.status == ScanStatus.DONE)
        ).all()
        assert len(done) == 1
