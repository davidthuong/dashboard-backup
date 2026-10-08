"""Pull agents: nodes enroll with a one-time token, then poll the hub over HTTPS.

The hub never connects to a node. A trigger only marks a run as pending; the node picks it up on
its next poll, runs the jobs from its own local config and reports the results. The hub cannot
tell a node which files to read or what to execute.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import socket
import ssl
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AgentEnrollment, AgentNodeRecord
from app.services.collector import persist_records
from app.services.types import NormalizedJobStatus

AGENT_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "agent"
AGENT_FILES = {
    "agent.ps1": "windows_pull_agent.ps1",
    "install.ps1": "windows_pull_agent_install.ps1",
}

# A claimed run that is never reported (agent killed, reboot) is handed out again after this.
RUN_CLAIM_TIMEOUT = timedelta(minutes=15)
ALLOWED_STATUSES = {"success", "failed", "warning", "running", "unknown"}
# Sources a node may report under; "veeam" and "agent" belong to the hub itself.
ALLOWED_REPORT_SOURCES = {"rclone", "directadmin"}
MAX_JOBS_PER_NODE = 20

_NAME_INVALID = re.compile(r"[^a-z0-9._-]+")
_VERSION_RE = re.compile(r'^\$AgentVersion\s*=\s*"([^"]+)"', re.MULTILINE)
_cert_cache: dict[str, tuple[float, str]] = {}


class EnrollError(Exception):
    status_code = 401


class NodeNameInUse(EnrollError):
    status_code = 409


@dataclass
class AgentInfo:
    hostname: str = ""
    os_info: str = ""
    agent_version: str = ""
    jobs: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ReportItem:
    job_name: str
    status: str
    source: str = "rclone"
    message: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    raw_payload: Any = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes even for timezone=True columns; everything is stored in UTC.
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = as_utc(value)
    return value.isoformat() if value else None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_node_name(value: str) -> str:
    return _NAME_INVALID.sub("-", (value or "").strip().lower()).strip("-._")[:64]


def read_agent_file(public_name: str) -> str | None:
    file_name = AGENT_FILES.get(public_name)
    if not file_name:
        return None
    path = AGENT_SCRIPT_DIR / file_name
    return path.read_text(encoding="utf-8") if path.is_file() else None


def latest_agent_version() -> str:
    text = read_agent_file("agent.ps1") or ""
    match = _VERSION_RE.search(text)
    return match.group(1) if match else ""


# --- hub address / certificate -------------------------------------------------------------


def _fetch_cert_sha1(addr: str, server_name: str) -> str:
    # Connect to addr (e.g. nginx:443 inside docker compose) but ask for the hub's own name via SNI,
    # so nginx answers with the certificate that nodes get, not its default server's.
    host, _, port = addr.strip().rpartition(":")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host.strip("[]"), int(port)), timeout=5) as sock:
        with context.wrap_socket(sock, server_hostname=server_name) as tls:
            der = tls.getpeercert(binary_form=True)
    return hashlib.sha1(der).hexdigest().upper()


def hub_cert_sha1(settings: Settings, hub_url: str) -> str:
    """SHA1 of the certificate nodes will see; Windows exposes it as X509Certificate.GetCertHashString()."""
    override = settings.agent_hub_cert_sha1.replace(":", "").replace(" ", "").strip().upper()
    if override:
        return override
    parts = urlsplit(hub_url)
    if parts.scheme != "https" or not parts.hostname:
        return ""

    candidates = [settings.agent_tls_probe_addr.strip(), f"{parts.hostname}:{parts.port or 443}"]
    for addr in [item for item in candidates if item]:
        cache_key = f"{addr}|{parts.hostname}"
        cached = _cert_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
        try:
            sha1 = _fetch_cert_sha1(addr, parts.hostname)
        except (OSError, ValueError):
            continue
        _cert_cache[cache_key] = (time.monotonic(), sha1)
        return sha1
    return ""


def build_install_command(hub_url: str, cert_sha1: str, enroll_token: str) -> str:
    """One line to paste into an elevated PowerShell (4.0+) on the node."""
    parts = [
        f"$h='{hub_url}'",
        f"$fp='{cert_sha1}'",
        "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12",
    ]
    if cert_sha1:
        # Trust exactly this certificate while downloading the installer; the installer checks it again,
        # pins it in the agent's config.json (self-signed hub) and clears this callback.
        parts.append(
            "[Net.ServicePointManager]::ServerCertificateValidationCallback = "
            "{ param($s, $c, $ch, $e) $c.GetCertHashString() -eq $fp }"
        )
    parts.append("$code = (New-Object Net.WebClient).DownloadString(\"$h/agent/install.ps1\")")
    parts.append(f"& ([scriptblock]::Create($code)) -HubUrl $h -CertSha1 $fp -EnrollToken '{enroll_token}'")
    return "; ".join(parts)


# --- enrollment ----------------------------------------------------------------------------


def create_enrollment(db: Session, settings: Settings, node_name: str = "") -> tuple[str, AgentEnrollment]:
    name = normalize_node_name(node_name)
    if node_name.strip() and not name:
        raise ValueError("Invalid node name")

    now = utcnow()
    # Housekeeping: drop tokens that can no longer be used.
    for old in db.query(AgentEnrollment).all():
        if old.used_at is not None or as_utc(old.expires_at) < now:
            db.delete(old)

    token = secrets.token_urlsafe(24)
    row = AgentEnrollment(
        token_hash=hash_token(token),
        node_name=name,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.agent_enroll_ttl_minutes),
    )
    db.add(row)
    db.commit()
    return token, row


def _clean_jobs(jobs: list[dict[str, str]]) -> str:
    cleaned = []
    for job in jobs[:MAX_JOBS_PER_NODE]:
        job_name = str(job.get("job_name") or "").strip()[:120]
        if job_name:
            cleaned.append({"job_name": job_name, "log_path": str(job.get("log_path") or "").strip()[:400]})
    return json.dumps(cleaned, ensure_ascii=False)


def _apply_info(node: AgentNodeRecord, info: AgentInfo, remote_ip: str) -> None:
    node.hostname = info.hostname.strip()[:255]
    node.os_info = info.os_info.strip()[:255]
    node.agent_version = info.agent_version.strip()[:40]
    node.jobs_json = _clean_jobs(info.jobs)
    node.remote_ip = remote_ip[:64]


def enroll_node(
    db: Session,
    enroll_token: str,
    info: AgentInfo,
    requested_name: str = "",
    remote_ip: str = "",
) -> tuple[AgentNodeRecord, str]:
    now = utcnow()
    token_hash = hash_token(enroll_token)
    enrollment = db.query(AgentEnrollment).filter(AgentEnrollment.token_hash == token_hash).one_or_none()
    if enrollment is None or enrollment.used_at is not None or as_utc(enrollment.expires_at) < now:
        raise EnrollError("Enrollment token is invalid, already used or expired. Create a new install command on the hub.")

    # Name chosen on the hub wins, then the installer's -Name, then the hostname.
    name = enrollment.node_name or normalize_node_name(requested_name) or normalize_node_name(info.hostname)
    if not name:
        raise EnrollError("Cannot determine a node name; pass -Name to the installer.")

    node = db.query(AgentNodeRecord).filter(AgentNodeRecord.name == name).one_or_none()
    if node is not None and enrollment.node_name != name:
        # Only a command created for this exact name may take over an existing node; otherwise any
        # unnamed command (or a duplicate hostname) could silently cut the real agent off.
        raise NodeNameInUse(
            f"Node name '{name}' is already in use. To reinstall it, create an install command with the name '{name}' on the hub."
        )

    # Consume the token atomically: a concurrent enroll with the same token gets rowcount 0.
    claimed = (
        db.query(AgentEnrollment)
        .filter(AgentEnrollment.token_hash == token_hash, AgentEnrollment.used_at.is_(None))
        .update({AgentEnrollment.used_at: now}, synchronize_session=False)
    )
    if claimed != 1:
        db.rollback()
        raise EnrollError("Enrollment token is invalid, already used or expired. Create a new install command on the hub.")

    agent_token = secrets.token_urlsafe(32)
    if node is None:
        node = AgentNodeRecord(name=name, created_at=now, enabled=True)
        db.add(node)
    # Re-enrolling an existing name (reinstall, migration from a legacy receiver) replaces its token
    # and keeps its job history, which is keyed by "[name] job".
    node.token_hash = hash_token(agent_token)
    _apply_info(node, info, remote_ip)
    node.last_seen_at = now
    node.offline_alerted = False
    db.commit()
    return node, agent_token


# --- agent runtime -------------------------------------------------------------------------


def authenticate(db: Session, authorization: str) -> AgentNodeRecord | None:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return db.query(AgentNodeRecord).filter(AgentNodeRecord.token_hash == hash_token(token.strip())).one_or_none()


def record_poll(db: Session, node: AgentNodeRecord, info: AgentInfo, remote_ip: str) -> dict[str, Any] | None:
    """Heartbeat; returns the pending run to execute, if any."""
    now = utcnow()
    _apply_info(node, info, remote_ip)
    node.last_seen_at = now
    node.offline_alerted = False

    run = None
    if node.run_id:
        claimed_at = as_utc(node.run_claimed_at)
        if claimed_at is None or now - claimed_at > RUN_CLAIM_TIMEOUT:
            node.run_claimed_at = now
            run = {"id": node.run_id, "requested_at": _iso(node.run_requested_at)}
    db.commit()
    return run


def request_run(db: Session, node: AgentNodeRecord) -> str:
    """A run still waiting for the agent is reused; one already handed out gets a fresh run after it."""
    if not node.run_id or node.run_claimed_at is not None:
        node.run_id = uuid.uuid4().hex
        node.run_requested_at = utcnow()
        node.run_claimed_at = None
        db.commit()
    return node.run_id


def is_online(node: AgentNodeRecord, settings: Settings) -> bool:
    last_seen = as_utc(node.last_seen_at)
    return last_seen is not None and utcnow() - last_seen <= timedelta(minutes=settings.agent_offline_after_minutes)


def queue_run(db: Session, node: AgentNodeRecord, settings: Settings) -> dict[str, Any]:
    """Trigger a pull node; shaped like AgentTriggerService results so callers can mix both kinds."""
    run_id = request_run(db, node)
    return {
        "name": node.name,
        "url": "pull-agent",
        "ok": True,
        "status_code": 202,
        "exit_code": None,
        "response": {"queued": True, "run_id": run_id, "online": is_online(node, settings)},
    }


def record_report(
    db: Session,
    node: AgentNodeRecord,
    run_id: str | None,
    items: list[ReportItem],
) -> list[NormalizedJobStatus]:
    now = utcnow()
    normalized = []
    for item in items:
        status = item.status.strip().lower()
        source = item.source.strip().lower()
        normalized.append(
            NormalizedJobStatus(
                source=source if source in ALLOWED_REPORT_SOURCES else "rclone",
                job_name=f"[{node.name}] {item.job_name.strip()[:120] or 'unnamed-job'}",
                status=status if status in ALLOWED_STATUSES else "unknown",
                message=item.message[:2000],
                started_at=item.started_at,
                ended_at=item.ended_at,
                raw_payload=item.raw_payload,
            )
        )
    persist_records(db, normalized)

    if run_id and run_id == node.run_id:
        node.run_id = None
        node.run_requested_at = None
        node.run_claimed_at = None
    node.last_run_at = now
    node.last_run_summary = ", ".join(
        f"{item.job_name.strip()}={record.status}" for item, record in zip(items, normalized)
    )[:1000]
    node.last_seen_at = now
    node.offline_alerted = False
    db.commit()
    return normalized


def mark_newly_offline(db: Session, settings: Settings) -> list[AgentNodeRecord]:
    """Enabled nodes silent for AGENT_OFFLINE_AFTER_MINUTES, each returned once until it polls again."""
    cutoff = utcnow() - timedelta(minutes=settings.agent_offline_after_minutes)
    newly_offline = []
    for node in db.query(AgentNodeRecord).filter(AgentNodeRecord.enabled.is_(True)).all():
        last_seen = as_utc(node.last_seen_at)
        if not node.offline_alerted and (last_seen is None or last_seen < cutoff):
            node.offline_alerted = True
            newly_offline.append(node)
    if newly_offline:
        db.commit()
    return newly_offline


def node_to_dict(node: AgentNodeRecord, settings: Settings, latest_version: str) -> dict[str, Any]:
    try:
        jobs = json.loads(node.jobs_json or "[]")
    except json.JSONDecodeError:
        jobs = []
    return {
        "id": node.id,
        "name": node.name,
        "enabled": node.enabled,
        "online": is_online(node, settings),
        "hostname": node.hostname,
        "os_info": node.os_info,
        "remote_ip": node.remote_ip,
        "agent_version": node.agent_version,
        "outdated": bool(latest_version and node.agent_version and node.agent_version != latest_version),
        "jobs": jobs,
        "created_at": _iso(node.created_at),
        "last_seen_at": _iso(node.last_seen_at),
        "run_pending": bool(node.run_id),
        "run_requested_at": _iso(node.run_requested_at),
        "run_claimed_at": _iso(node.run_claimed_at),
        "last_run_at": _iso(node.last_run_at),
        "last_run_summary": node.last_run_summary,
    }
