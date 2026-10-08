from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class JobStatusHistory(Base):
    __tablename__ = "job_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(30), index=True)  # veeam | rclone
    job_name: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_payload: Mapped[str] = mapped_column(Text, default="")


class AgentEnrollment(Base):
    """One-time token printed in an install command; consumed by the first /enroll call."""

    __tablename__ = "agent_enrollments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    node_name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentNodeRecord(Base):
    """A pull agent: polls the hub with its own bearer token, runs its locally configured jobs on request."""

    __tablename__ = "agent_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    hostname: Mapped[str] = mapped_column(String(255), default="")
    os_info: Mapped[str] = mapped_column(String(255), default="")
    agent_version: Mapped[str] = mapped_column(String(40), default="")
    remote_ip: Mapped[str] = mapped_column(String(64), default="")
    jobs_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    offline_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Pending run request: set by trigger, handed out by poll (claimed), cleared by report.
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_summary: Mapped[str] = mapped_column(Text, default="")


class AppConfig(Base):
    __tablename__ = "app_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    config_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    config_value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

