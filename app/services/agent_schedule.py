from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings
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
    """Calls trigger-all on a daily schedule; nodes push their results to /api/ingest themselves."""

    def __init__(self, trigger_service: AgentTriggerService, alert_manager: AlertManager, settings: Settings):
        self.trigger_service = trigger_service
        self.alert_manager = alert_manager
        self.settings = settings
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def run(self):
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

    def start(self) -> bool:
        if not self.settings.agent_trigger_enabled or not self.settings.agent_auto_trigger_times.strip():
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
        self.scheduler.start()
        logger.info("Auto agent trigger scheduled at %s (%s)", self.settings.agent_auto_trigger_times, self.settings.timezone)
        return True

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
