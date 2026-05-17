from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppConfig
from app.services.alerts import AlertManager
from app.services.collector import BackupCollector, persist_records

POLLING_KEY = "polling_interval_seconds"
logger = logging.getLogger(__name__)


def load_polling_interval(db: Session, settings: Settings) -> int:
    item = db.query(AppConfig).filter(AppConfig.config_key == POLLING_KEY).first()
    if not item:
        return settings.polling_interval_seconds
    try:
        return max(15, min(3600, int(item.config_value)))
    except (TypeError, ValueError):
        return settings.polling_interval_seconds


def save_polling_interval(db: Session, seconds: int):
    item = db.query(AppConfig).filter(AppConfig.config_key == POLLING_KEY).first()
    if not item:
        item = AppConfig(config_key=POLLING_KEY, config_value=str(seconds))
        db.add(item)
    else:
        item.config_value = str(seconds)
    db.commit()


class PollingManager:
    def __init__(self, collector: BackupCollector, alert_manager: AlertManager, db_factory, settings: Settings):
        self.collector = collector
        self.alert_manager = alert_manager
        self.db_factory = db_factory
        self.settings = settings
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)
        self.job_id = "collect_backup_status"

    async def run_collection(self):
        db = self.db_factory()
        try:
            items, _details = await self.collector.collect()
            persist_records(db, items)
            try:
                await self.alert_manager.send_alerts(items)
            except Exception:
                logger.exception("Alert dispatch failed in scheduled collection.")
        finally:
            db.close()

    def start(self):
        db = self.db_factory()
        try:
            interval = load_polling_interval(db, self.settings)
        finally:
            db.close()

        self.scheduler.add_job(self.run_collection, "interval", seconds=interval, id=self.job_id, replace_existing=True)
        self.scheduler.start()

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def update_interval(self, seconds: int):
        self.scheduler.reschedule_job(self.job_id, trigger="interval", seconds=seconds)
        db = self.db_factory()
        try:
            save_polling_interval(db, seconds)
        finally:
            db.close()
