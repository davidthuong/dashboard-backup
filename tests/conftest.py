import os
import tempfile
from pathlib import Path

import pytest

# Settings and the DB engine are created at import time, so configure before importing the app.
# Explicit values also shadow anything in a developer's local .env (alerts must never fire from tests).
_TMP = Path(tempfile.mkdtemp(prefix="backup-dashboard-tests-"))
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
        "ALLOW_INSECURE_DEFAULTS": "true",
        "TIMEZONE": "Asia/Bangkok",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "test-password-123",
        "SESSION_SECRET": "test-session-secret-test-session-secret",
        "SESSION_COOKIE_SECURE": "false",
        "VEEAM_ENABLED": "false",
        "VEEAM_TARGETS_JSON": "",
        "RCLONE_ENABLED": "false",
        "DIRECTADMIN_ENABLED": "false",
        "ALERT_ENABLED": "false",
        "INGEST_API_TOKEN": "test-ingest-token",
        "AGENT_TRIGGER_ENABLED": "false",
        "AGENT_NODES_JSON": "",
        "AGENT_AUTO_TRIGGER_TIMES": "",
        "AGENT_HUB_URL": "",
        "AGENT_HUB_CERT_SHA1": "",
        "AGENT_TLS_PROBE_ADDR": "",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AgentEnrollment, AgentNodeRecord, JobStatusHistory  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        db = SessionLocal()
        try:
            for model in (AgentEnrollment, AgentNodeRecord, JobStatusHistory):
                db.query(model).delete()
            db.commit()
        finally:
            db.close()
        yield test_client


@pytest.fixture()
def admin(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "test-password-123"})
    assert response.status_code == 200
    return client


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
