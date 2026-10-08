from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import JobStatusHistory
from app.services.directadmin_parser import DirectAdminParser
from app.services.rclone_parser import RcloneParser
from app.services.types import NormalizedJobStatus
from app.services.veeam_client import VeeamClient


class BackupCollector:
    def __init__(self, veeam_client: VeeamClient, rclone_parser: RcloneParser, directadmin_parser: DirectAdminParser):
        self.veeam_client = veeam_client
        self.rclone_parser = rclone_parser
        self.directadmin_parser = directadmin_parser

    async def collect(self) -> tuple[list[NormalizedJobStatus], list[dict[str, Any]]]:
        normalized: list[NormalizedJobStatus] = []
        details: list[dict[str, Any]] = []

        try:
            veeam_records = await self.veeam_client.collect()
            normalized.extend(veeam_records)
            details.append({"source": "veeam", "ok": True, "count": len(veeam_records), "error": ""})
        except Exception as exc:
            details.append({"source": "veeam", "ok": False, "count": 0, "error": str(exc)})
            normalized.append(
                NormalizedJobStatus(
                    source="veeam",
                    job_name="veeam_collection_error",
                    status="failed",
                    message=str(exc),
                    raw_payload={},
                )
            )

        try:
            rclone_records = self.rclone_parser.collect()
            normalized.extend(rclone_records)
            details.append({"source": "rclone", "ok": True, "count": len(rclone_records), "error": ""})
        except Exception as exc:
            details.append({"source": "rclone", "ok": False, "count": 0, "error": str(exc)})
            normalized.append(
                NormalizedJobStatus(
                    source="rclone",
                    job_name="rclone_collection_error",
                    status="failed",
                    message=str(exc),
                    raw_payload={},
                )
            )

        try:
            da_records = self.directadmin_parser.collect()
            normalized.extend(da_records)
            details.append({"source": "directadmin", "ok": True, "count": len(da_records), "error": ""})
        except Exception as exc:
            details.append({"source": "directadmin", "ok": False, "count": 0, "error": str(exc)})
            normalized.append(
                NormalizedJobStatus(
                    source="directadmin",
                    job_name="directadmin_collection_error",
                    status="failed",
                    message=str(exc),
                    raw_payload={},
                )
            )

        return normalized, details


def to_utc(value: datetime | None) -> datetime | None:
    """SQLite keeps only the wall-clock part, so every stored time is UTC.

    A naive time (parsed from a log line) is taken as TIMEZONE local time.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        try:
            value = value.replace(tzinfo=ZoneInfo(get_settings().timezone))
        except (ZoneInfoNotFoundError, ValueError):
            value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def persist_records(db: Session, items: list[NormalizedJobStatus]) -> int:
    now = datetime.now(timezone.utc)
    created = 0
    for item in items:
        record = JobStatusHistory(
            source=item.source,
            job_name=item.job_name,
            status=item.status,
            message=item.message or "",
            started_at=to_utc(item.started_at),
            ended_at=to_utc(item.ended_at),
            collected_at=now,
            raw_payload=json.dumps(item.raw_payload, ensure_ascii=False, default=str)
            if item.raw_payload is not None
            else "",
        )
        db.add(record)
        created += 1
    db.commit()
    return created
