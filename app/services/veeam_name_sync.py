from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppConfig, JobStatusHistory
from app.services.veeam_client import resolve_veeam_targets


@dataclass
class VeeamNameSyncResult:
    tracked_targets: int = 0
    renamed_rows: int = 0
    changed_targets: int = 0


def _target_key(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/").lower()
    return f"veeam_target_name::{normalized}"


def _load_or_create_config(db: Session, key: str, value: str) -> AppConfig:
    item = db.query(AppConfig).filter(AppConfig.config_key == key).first()
    if item:
        return item
    item = AppConfig(config_key=key, config_value=value)
    db.add(item)
    return item


def _rename_veeam_prefix(db: Session, old_name: str, new_name: str) -> int:
    if old_name == new_name:
        return 0
    old_prefix = f"[{old_name}] "
    rows = (
        db.query(JobStatusHistory)
        .filter(JobStatusHistory.source == "veeam")
        .filter(JobStatusHistory.job_name.like(f"{old_prefix}%"))
        .all()
    )
    if not rows:
        return 0
    for row in rows:
        row.job_name = f"[{new_name}] {row.job_name[len(old_prefix):]}"
    return len(rows)


def sync_veeam_target_names(db: Session, settings: Settings) -> VeeamNameSyncResult:
    result = VeeamNameSyncResult()
    if not settings.veeam_enabled:
        return result

    targets = resolve_veeam_targets(settings)
    for target in targets:
        name = str(target.get("name") or "").strip()
        base_url = str(target.get("base_url") or "").strip()
        if not name or not base_url:
            continue
        result.tracked_targets += 1

        key = _target_key(base_url)
        config_item = _load_or_create_config(db, key, name)
        previous_name = (config_item.config_value or "").strip()
        if not previous_name:
            config_item.config_value = name
            continue
        if previous_name == name:
            continue

        changed = _rename_veeam_prefix(db, previous_name, name)
        config_item.config_value = name
        result.renamed_rows += changed
        result.changed_targets += 1

    db.commit()
    return result
