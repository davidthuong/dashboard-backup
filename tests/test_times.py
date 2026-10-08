from datetime import datetime

from app.models import JobStatusHistory
from app.services.collector import persist_records
from app.services.types import NormalizedJobStatus


def _stored_ended_at(db, ended_at):
    db.query(JobStatusHistory).delete()
    persist_records(db, [NormalizedJobStatus(source="rclone", job_name="j", status="success", ended_at=ended_at)])
    return db.query(JobStatusHistory).one().ended_at.replace(tzinfo=None)


def test_times_are_stored_as_utc(client, db):
    # agent / legacy push: "BACKUP HOAN TAT" at 20:04:06 Vietnam time, sent as UTC
    assert _stored_ended_at(db, datetime.fromisoformat("2026-10-08T13:04:06+00:00")) == datetime(2026, 10, 8, 13, 4, 6)
    # Veeam style offset
    assert _stored_ended_at(db, datetime.fromisoformat("2026-10-08T20:04:06+07:00")) == datetime(2026, 10, 8, 13, 4, 6)
    # naive time parsed from a log line on the hub: TIMEZONE (Asia/Bangkok) local time
    assert _stored_ended_at(db, datetime(2026, 10, 8, 20, 4, 6)) == datetime(2026, 10, 8, 13, 4, 6)


def test_ingest_api_returns_utc(admin):
    response = admin.post(
        "/api/ingest/status",
        headers={"X-Ingest-Token": "test-ingest-token"},
        json={"items": [{"node": "hub1", "source": "rclone", "job_name": "icewarp-nightly", "status": "success",
                         "ended_at": "2026-10-08T13:04:06.0000000Z"}]},
    )
    assert response.status_code == 200, response.text
    row = admin.get("/api/jobs/latest").json()["items"][0]
    # no offset in the JSON: the dashboard reads bare times as UTC
    assert row["ended_at"].startswith("2026-10-08T13:04:06")
