import json
from datetime import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import JobStatusHistory
from app.schemas import CollectResult, PollingSettingIn, PollingSettingOut
from app.services.auth import read_session_token, require_admin
from app.services.collector import persist_records
from app.services.polling import load_polling_interval

router = APIRouter(tags=["dashboard"])
logger = logging.getLogger(__name__)


def _extract_next_run(raw_payload_text: str, source: str) -> datetime | None:
    if source != "veeam" or not raw_payload_text:
        return None
    try:
        parsed = json.loads(raw_payload_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    payload = parsed.get("payload")
    data = payload if isinstance(payload, dict) else parsed
    if not isinstance(data, dict):
        return None

    for key in ("nextRun", "nextRunTime", "next_run", "next_run_time"):
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        text = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            continue
    return None


def _has_session(request: Request) -> bool:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name, "")
    if not token:
        return False
    return bool(read_session_token(settings, token))


def _pull_collection_enabled(request: Request) -> bool:
    settings = request.app.state.settings
    return bool(settings.pull_collection_enabled)


@router.get("/")
def dashboard_page(request: Request):
    if not _has_session(request):
        return RedirectResponse(url="/login", status_code=303)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"generated_at": datetime.utcnow().isoformat()},
    )


@router.get("/api/jobs/latest")
def get_latest_jobs(
    request: Request,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
    source: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
):
    query = db.query(JobStatusHistory)
    if source:
        query = query.filter(JobStatusHistory.source == source)
    if status:
        query = query.filter(JobStatusHistory.status == status)
    if search:
        like_value = f"%{search}%"
        query = query.filter(JobStatusHistory.job_name.like(like_value))

    latest_ids_subquery = (
        query.with_entities(func.max(JobStatusHistory.id).label("latest_id"))
        .group_by(JobStatusHistory.source, JobStatusHistory.job_name)
        .subquery()
    )

    latest = (
        db.query(JobStatusHistory)
        .join(latest_ids_subquery, JobStatusHistory.id == latest_ids_subquery.c.latest_id)
        .order_by(JobStatusHistory.source, func.lower(JobStatusHistory.job_name))
        .all()
    )
    payload = [
        {
            "id": row.id,
            "source": row.source,
            "job_name": row.job_name,
            "status": row.status,
            "message": row.message,
            "next_run": _extract_next_run(row.raw_payload, row.source),
            "ended_at": row.ended_at,
            "collected_at": row.collected_at,
        }
        for row in latest
    ]
    return {"items": payload, "count": len(payload)}


@router.get("/api/jobs/history")
def get_history(
    request: Request,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
    source: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
):
    query = db.query(JobStatusHistory).order_by(desc(JobStatusHistory.collected_at), desc(JobStatusHistory.id))
    if source:
        query = query.filter(JobStatusHistory.source == source)
    if status:
        query = query.filter(JobStatusHistory.status == status)
    if search:
        like_value = f"%{search}%"
        query = query.filter(JobStatusHistory.job_name.like(like_value))

    total = query.order_by(None).count()
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    rows = query.offset(offset).limit(page_size).all()
    return {
        "items": [
            {
                "id": row.id,
                "source": row.source,
                "job_name": row.job_name,
                "status": row.status,
                "message": row.message,
                "next_run": _extract_next_run(row.raw_payload, row.source),
                "ended_at": row.ended_at,
                "collected_at": row.collected_at,
            }
            for row in rows
        ],
        "count": len(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


@router.post("/api/jobs/collect", response_model=CollectResult)
async def manual_collect(
    request: Request,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not _pull_collection_enabled(request):
        raise HTTPException(status_code=400, detail="Pull collection is disabled. Use ingest API from remote nodes.")

    collector = request.app.state.collector
    alert_manager = request.app.state.alert_manager

    items, details = await collector.collect()
    persist_records(db, items)
    response_details = list(details)
    try:
        sent_alerts = await alert_manager.send_alerts(items)
        response_details.append({"source": "alert", "ok": True, "count": sent_alerts, "error": ""})
    except Exception as exc:
        logger.exception("Alert dispatch failed after manual collect.")
        response_details.append({"source": "alert", "ok": False, "count": 0, "error": str(exc)})

    failed_records = sum(1 for item in items if item.status in {"failed", "warning"})
    return CollectResult(total_records=len(items), failed_records=failed_records, details=response_details)


@router.get("/api/system/capabilities")
def system_capabilities(
    request: Request,
    _user: str = Depends(require_admin),
):
    settings = request.app.state.settings
    return {
        "pull_collection_enabled": bool(settings.pull_collection_enabled),
        "ingest_enabled": bool(settings.ingest_enabled),
        "polling_interval_seconds": int(settings.polling_interval_seconds),
        "active_server_window_minutes": int(settings.active_server_window_minutes),
        "sources": {
            "veeam_enabled": bool(settings.veeam_enabled),
            "rclone_enabled": bool(settings.rclone_enabled),
            "directadmin_enabled": bool(settings.directadmin_enabled),
        },
    }


@router.get("/api/settings/polling", response_model=PollingSettingOut)
def get_polling(
    request: Request,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    value = load_polling_interval(db, settings)
    return PollingSettingOut(polling_interval_seconds=value)


@router.put("/api/settings/polling", response_model=PollingSettingOut)
def update_polling(
    payload: PollingSettingIn,
    request: Request,
    _user: str = Depends(require_admin),
):
    if not _pull_collection_enabled(request):
        raise HTTPException(status_code=400, detail="Polling settings are disabled in ingest-only mode.")

    seconds = payload.polling_interval_seconds
    if seconds < 15 or seconds > 3600:
        raise HTTPException(status_code=400, detail="polling_interval_seconds must be between 15 and 3600")

    polling_manager = request.app.state.polling_manager
    polling_manager.update_interval(seconds)
    return PollingSettingOut(polling_interval_seconds=seconds)
