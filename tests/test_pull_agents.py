import asyncio
import re
from datetime import timedelta

from app.config import get_settings
from app.models import AgentEnrollment, AgentNodeRecord, JobStatusHistory
from app.services import pull_agents
from app.services.agent_schedule import AgentAutoTrigger

JOB = {"job_name": "icewarp-nightly", "log_path": "D:\\scripts\\backup-icewarp.log"}


def _install_token(admin, name=""):
    response = admin.post("/api/agents/enrollments", json={"name": name})
    assert response.status_code == 200, response.text
    data = response.json()
    match = re.search(r"-EnrollToken '([^']+)'", data["command"])
    assert match, data["command"]
    return match.group(1), data


def _enroll(client, token, hostname="PROMAIN-MAIL", name="", jobs=None):
    return client.post(
        "/api/agent/v1/enroll",
        json={
            "enroll_token": token,
            "name": name,
            "hostname": hostname,
            "os_info": "Windows Server 2012 R2 / PowerShell 4.0",
            "agent_version": pull_agents.latest_agent_version(),
            "jobs": [JOB] if jobs is None else jobs,
        },
    )


def _auth(agent_token):
    return {"Authorization": f"Bearer {agent_token}"}


def _poll(client, agent_token):
    return client.post("/api/agent/v1/poll", json={"hostname": "PROMAIN-MAIL", "jobs": [JOB]}, headers=_auth(agent_token))


def _enrolled_agent(admin, name="promain-mail"):
    token, _ = _install_token(admin, name)
    response = _enroll(admin, token)
    assert response.status_code == 200, response.text
    return response.json()["agent_token"]


def test_agent_scripts_are_served_without_login(client):
    for public_name in ("install.ps1", "agent.ps1"):
        response = client.get(f"/agent/{public_name}")
        assert response.status_code == 200
        assert "PowerShell" in response.text
    assert client.get("/agent/windows_secure_receiver.ps1").status_code == 404
    assert client.get("/agent/..%2Fapp%2Fconfig.py").status_code == 404
    assert pull_agents.latest_agent_version()


def test_enrollment_requires_admin(client):
    assert client.post("/api/agents/enrollments", json={}).status_code == 401
    assert client.get("/api/agents").status_code == 401


def test_full_cycle_enroll_trigger_poll_report(admin):
    token, created = _install_token(admin, "promain-mail")
    assert created["node_name"] == "promain-mail"
    assert created["hub_url"] == "http://testserver"
    assert "ServerCertificateValidationCallback" not in created["command"]  # nothing to pin over http

    enrolled = _enroll(admin, token)
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["name"] == "promain-mail"
    agent_token = enrolled.json()["agent_token"]

    # one-time token
    assert _enroll(admin, token).status_code == 401

    assert _poll(admin, agent_token).json()["run"] is None

    triggered = admin.post("/api/agents/trigger-all").json()
    assert triggered["ok"] and triggered["count"] == 1
    assert triggered["results"][0]["response"]["queued"] is True

    run = _poll(admin, agent_token).json()["run"]
    assert run and run["id"]
    # claimed: not handed out twice while the agent works on it
    assert _poll(admin, agent_token).json()["run"] is None

    report = admin.post(
        "/api/agent/v1/report",
        headers=_auth(agent_token),
        json={
            "run_id": run["id"],
            # PowerShell sends a one-element array as a bare object
            "items": {
                "source": "rclone",
                "job_name": "icewarp-nightly",
                "status": "success",
                "message": "backup completed successfully",
                "ended_at": "2026-10-08T00:30:00Z",
                "raw_payload": {"log_path": JOB["log_path"]},
            },
        },
    )
    assert report.status_code == 200, report.text
    assert report.json()["created"] == 1

    latest = admin.get("/api/jobs/latest").json()["items"]
    assert [(row["job_name"], row["status"]) for row in latest] == [("[promain-mail] icewarp-nightly", "success")]

    nodes = admin.get("/api/agents").json()["nodes"]
    assert len(nodes) == 1
    node = nodes[0]
    assert node["online"] and not node["run_pending"] and not node["outdated"]
    assert node["hostname"] == "PROMAIN-MAIL"
    assert node["jobs"] == [JOB]
    assert node["last_run_summary"] == "icewarp-nightly=success"


def test_trigger_single_pull_node_by_name(admin):
    agent_token = _enrolled_agent(admin)
    response = admin.post("/api/agents/trigger/promain-mail")
    assert response.status_code == 200
    assert response.json()["result"]["response"]["queued"] is True
    assert _poll(admin, agent_token).json()["run"]
    assert admin.post("/api/agents/trigger/nope").status_code == 404


def test_trigger_all_without_any_agent_is_rejected(admin):
    assert admin.post("/api/agents/trigger-all").status_code == 400


def test_name_comes_from_hub_then_installer_then_hostname(admin):
    token, _ = _install_token(admin)
    assert _enroll(admin, token, hostname="IGNORED", name="Mail 01").json()["name"] == "mail-01"
    token, _ = _install_token(admin)
    assert _enroll(admin, token, hostname="PROMAIN-MAIL").json()["name"] == "promain-mail"
    token, _ = _install_token(admin, "hub-chosen")
    assert _enroll(admin, token, name="installer-name").json()["name"] == "hub-chosen"


def test_unnamed_command_cannot_take_over_existing_node(admin):
    agent_token = _enrolled_agent(admin, "hub1")
    token, _ = _install_token(admin)
    assert _enroll(admin, token, name="hub1").status_code == 409
    assert _enroll(admin, token, hostname="HUB1").status_code == 409
    assert _poll(admin, agent_token).status_code == 200
    # the token was not consumed by the refused attempts
    assert _enroll(admin, token, hostname="hub5").json()["name"] == "hub5"


def test_trigger_during_a_claimed_run_queues_another_run(admin):
    agent_token = _enrolled_agent(admin)
    admin.post("/api/agents/trigger/promain-mail")
    first = _poll(admin, agent_token).json()["run"]
    admin.post("/api/agents/trigger/promain-mail")
    second = _poll(admin, agent_token).json()["run"]
    assert second and second["id"] != first["id"]
    # the late report of the first run must not clear the second one
    admin.post("/api/agent/v1/report", headers=_auth(agent_token), json={"run_id": first["id"], "items": []})
    assert admin.get("/api/agents").json()["nodes"][0]["run_pending"] is True


def test_report_normalizes_source_and_time(admin, db):
    agent_token = _enrolled_agent(admin)
    item = {"source": "veeam", "job_name": "x", "status": "weird", "ended_at": "2026-10-08T07:30:00+07:00"}
    admin.post("/api/agent/v1/report", headers=_auth(agent_token), json={"items": [item]})
    row = db.query(JobStatusHistory).one()
    assert (row.source, row.status, row.job_name) == ("rclone", "unknown", "[promain-mail] x")
    assert pull_agents.as_utc(row.ended_at).isoformat() == "2026-10-08T00:30:00+00:00"


def test_reenabled_node_is_not_reported_offline_right_away(admin, db):
    _enrolled_agent(admin)
    settings = get_settings()
    node = db.query(AgentNodeRecord).one()
    node.enabled = False
    node.last_seen_at = pull_agents.utcnow() - timedelta(days=1)
    db.commit()
    assert admin.get("/api/agents").json()["triggerable"] == 0
    admin.patch(f"/api/agents/nodes/{node.id}", json={"enabled": True})
    db.expire_all()
    assert pull_agents.mark_newly_offline(db, settings) == []


def test_pull_queue_failure_does_not_skip_legacy_trigger(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_trigger_enabled", True)
    called = []

    class FakeLegacy:
        async def trigger_all(self):
            called.append(True)
            return []

    def broken_db():
        raise RuntimeError("database is locked")

    auto = AgentAutoTrigger(trigger_service=FakeLegacy(), alert_manager=None, settings=settings, db_factory=broken_db)
    asyncio.run(auto.run())
    assert called == [True]


def test_invalid_node_name_is_rejected(admin):
    assert admin.post("/api/agents/enrollments", json={"name": "!!!"}).status_code == 400


def test_reenroll_replaces_token_and_keeps_node(admin, db):
    old_token = _enrolled_agent(admin)
    new_token = _enrolled_agent(admin)
    assert _poll(admin, old_token).status_code == 401
    assert _poll(admin, new_token).status_code == 200
    assert db.query(AgentNodeRecord).count() == 1


def test_expired_enrollment_token_is_rejected(admin, db):
    token, _ = _install_token(admin, "late")
    row = db.query(AgentEnrollment).one()
    row.expires_at = pull_agents.utcnow() - timedelta(minutes=1)
    db.commit()
    assert _enroll(admin, token).status_code == 401


def test_agent_auth(admin, db):
    assert _poll(admin, "not-a-token").status_code == 401
    assert admin.post("/api/agent/v1/poll", json={}).status_code == 401

    agent_token = _enrolled_agent(admin)
    node_id = db.query(AgentNodeRecord).one().id
    assert admin.patch(f"/api/agents/nodes/{node_id}", json={"enabled": False}).status_code == 200
    assert _poll(admin, agent_token).status_code == 403
    assert admin.post("/api/agents/trigger/promain-mail").status_code == 400

    assert admin.delete(f"/api/agents/nodes/{node_id}").status_code == 200
    assert _poll(admin, agent_token).status_code == 401


def test_unreported_run_is_handed_out_again_after_claim_timeout(admin, db):
    agent_token = _enrolled_agent(admin)
    admin.post("/api/agents/trigger/promain-mail")
    first = _poll(admin, agent_token).json()["run"]

    node = db.query(AgentNodeRecord).one()
    node.run_claimed_at = pull_agents.utcnow() - pull_agents.RUN_CLAIM_TIMEOUT - timedelta(seconds=1)
    db.commit()
    assert _poll(admin, agent_token).json()["run"]["id"] == first["id"]


def test_offline_nodes_are_reported_once(admin, db):
    _enrolled_agent(admin)
    settings = get_settings()
    assert pull_agents.mark_newly_offline(db, settings) == []

    node = db.query(AgentNodeRecord).one()
    node.last_seen_at = pull_agents.utcnow() - timedelta(minutes=settings.agent_offline_after_minutes + 1)
    db.commit()
    assert [n.name for n in pull_agents.mark_newly_offline(db, settings)] == ["promain-mail"]
    assert pull_agents.mark_newly_offline(db, settings) == []
    assert admin.get("/api/agents").json()["nodes"][0]["online"] is False


def test_hub_cert_and_install_command(monkeypatch):
    settings = get_settings()
    assert pull_agents.hub_cert_sha1(settings, "http://10.0.0.1:8000") == ""
    monkeypatch.setattr(settings, "agent_hub_cert_sha1", "ab:cd:ef")
    assert pull_agents.hub_cert_sha1(settings, "https://103.238.214.35") == "ABCDEF"

    command = pull_agents.build_install_command("https://103.238.214.35", "ABCDEF", "tok")
    assert "$fp='ABCDEF'" in command
    assert "GetCertHashString() -eq $fp" in command
    assert command.endswith("-HubUrl $h -CertSha1 $fp -EnrollToken 'tok'")
