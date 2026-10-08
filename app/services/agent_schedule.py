from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AgentNodeRecord
from app.services import pull_agents
from app.services.agent_trigger import AgentTriggerService
from app.services.alerts import AlertManager
from app.services.types import NormalizedJobStatus

logger = logging.getLogger(__name__)


def parse_trigger_times(value: str) -> list[tuple[int, int]]:
    """Parse "07:00,19:30" into [(7, 0), (19, 30)]."""
    times: list[tuple[int, int]] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        hour_text, sep, minute_text = part.partition(":")
        if not sep or not hour_text.isdigit() or not minute_text.isdigit():
            raise ValueError(f"Invalid time '{part}' in AGENT_AUTO_TRIGGER_TIMES (expected HH:MM)")
        hour, minute = int(hour_text), int(minute_text)
        if hour > 23 or minute > 59:
            raise ValueError(f"Invalid time '{part}' in AGENT_AUTO_TRIGGER_TIMES (expected HH:MM)")
        times.append((hour, minute))
    return times


class AgentAutoTrigger:
    """Triggers every agent on a daily schedule and alerts when a pull agent stops polling.

    Legacy (push) nodes are called directly and push their results to /api/ingest; pull nodes get a
    pending run that they pick up on their next poll.
    """

    def __init__(
        self,
        trigger_service: AgentTriggerService,
        alert_manager: AlertManager,
        settings: Settings,
        db_factory: Callable[[], Session],
    ):
        self.trigger_service = trigger_service
        self.alert_manager = alert_manager
        self.settings = settings
        self.db_factory = db_factory
        self._started_at = time.monotonic()
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def _queue_pull_runs(self) -> int:
        db = self.db_factory()
        try:
            nodes = db.query(AgentNodeRecord).filter(AgentNodeRecord.enabled.is_(True)).all()
            for node in nodes:
                pull_agents.request_run(db, node)
            return len(nodes)
        finally:
            db.close()

    async def run(self):
        try:
            queued = self._queue_pull_runs()
            if queued:
                logger.info("Auto agent trigger queued a run for %s pull agent(s)", queued)
        except Exception:
            # Must not cost the legacy nodes their daily trigger.
            logger.exception("Auto agent trigger could not queue pull agent runs.")
        if not self.settings.agent_trigger_enabled:
            return

        results = await self.trigger_service.trigger_all()
        failed = [item for item in results if not item.get("ok")]
        logger.info("Auto agent trigger finished: %s ok, %s failed", len(results) - len(failed), len(failed))
        if not failed:
            return

        now = datetime.now(timezone.utc)
        items: list[NormalizedJobStatus] = []
        for item in failed:
            logger.warning(
                "Auto agent trigger failed for %s: status_code=%s exit_code=%s response=%s",
                item.get("name"),
                item.get("status_code"),
                item.get("exit_code"),
                item.get("response"),
            )
            items.append(
                NormalizedJobStatus(
                    source="agent",
                    job_name=f"[{item.get('name')}] auto-trigger",
                    status="failed",
                    message=f"status_code={item.get('status_code')} exit_code={item.get('exit_code')} response={item.get('response')}",
                    ended_at=now,
                )
            )
        try:
            await self.alert_manager.send_alerts(items)
        except Exception:
            logger.exception("Alert dispatch failed after auto agent trigger.")

    async def check_offline(self):
        # After a hub restart every node looks stale until its next poll; give them one full window.
        if time.monotonic() - self._started_at < self.settings.agent_offline_after_minutes * 60:
            return
        db = self.db_factory()
        try:
            offline = pull_agents.mark_newly_offline(db, self.settings)
            items = [
                NormalizedJobStatus(
                    source="agent",
                    job_name=f"[{node.name}] heartbeat",
                    status="failed",
                    message=(
                        f"agent has not polled the hub for over {self.settings.agent_offline_after_minutes} min "
                        f"(last seen {pull_agents.as_utc(node.last_seen_at)}, host {node.hostname or '-'}, ip {node.remote_ip or '-'})"
                    ),
                    ended_at=datetime.now(timezone.utc),
                )
                for node in offline
            ]
        finally:
            db.close()
        if not items:
            return
        logger.warning("Pull agents offline: %s", ", ".join(item.job_name for item in items))
        try:
            await self.alert_manager.send_alerts(items)
        except Exception:
            logger.exception("Alert dispatch failed for offline pull agents.")

    def start(self) -> bool:
        """Always watches pull agents; returns whether the daily trigger got scheduled."""
        self._started_at = time.monotonic()
        self.scheduler.add_job(
            self.check_offline,
            IntervalTrigger(minutes=1),
            id="pull_agent_offline_check",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()

        if not self.settings.agent_auto_trigger_times.strip():
            return False
        try:
            times = parse_trigger_times(self.settings.agent_auto_trigger_times)
        except ValueError:
            logger.exception("Auto agent trigger is NOT scheduled.")
            return False

        for hour, minute in times:
            self.scheduler.add_job(
                self.run,
                CronTrigger(hour=hour, minute=minute, timezone=self.settings.timezone),
                id=f"agent_auto_trigger_{hour:02d}{minute:02d}",
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=300,
            )
        logger.info("Auto agent trigger scheduled at %s (%s)", self.settings.agent_auto_trigger_times, self.settings.timezone)
        return True

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
