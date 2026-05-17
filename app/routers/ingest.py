import logging

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas import IngestStatusIn
from app.services.collector import persist_records
from app.services.types import NormalizedJobStatus

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
logger = logging.getLogger(__name__)


def _check_ingest_token(request: Request, ingest_token: str):
    settings = request.app.state.settings
    if not settings.ingest_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingest is disabled")
    if not settings.ingest_api_token:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="INGEST_API_TOKEN is not configured")
    if ingest_token != settings.ingest_api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingest token")


@router.post("/status")
async def ingest_status(
    payload: IngestStatusIn,
    request: Request,
    x_ingest_token: str = Header(default="", alias="X-Ingest-Token"),
):
    _check_ingest_token(request, x_ingest_token)

    normalized: list[NormalizedJobStatus] = []
    for item in payload.items:
        source = item.source.strip().lower() if item.source else "rclone"
        node = item.node.strip() if item.node else "unknown-node"
        job_name = item.job_name.strip() if item.job_name else "unnamed-job"
        status_text = item.status.strip().lower() if item.status else "unknown"
        normalized.append(
            NormalizedJobStatus(
                source=source,
                job_name=f"[{node}] {job_name}",
                status=status_text,
                message=item.message,
                started_at=item.started_at,
                ended_at=item.ended_at,
                raw_payload=item.raw_payload,
            )
        )

    db: Session = SessionLocal()
    try:
        created = persist_records(db, normalized)
    finally:
        db.close()

    alert_ok = True
    alert_error = ""
    try:
        await request.app.state.alert_manager.send_alerts(normalized)
    except Exception as exc:
        logger.exception("Alert dispatch failed after ingest write.")
        alert_ok = False
        alert_error = str(exc)

    response = {"ok": True, "created": created, "alert_ok": alert_ok}
    if alert_error:
        response["alert_error"] = alert_error
    return response
