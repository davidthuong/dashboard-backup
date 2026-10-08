"""Endpoints called by pull agents (no admin session; agents authenticate with their bearer token)."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AgentNodeRecord
from app.schemas import AgentEnrollIn, AgentInfoIn, AgentReportIn
from app.services import pull_agents
from app.services.pull_agents import AgentInfo, EnrollError, ReportItem

router = APIRouter(tags=["agent-api"])
logger = logging.getLogger(__name__)


def _remote_ip(request: Request) -> str:
    # nginx appends the peer address to X-Forwarded-For, so the last entry is the one nginx saw.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else ""


def _info(payload: AgentInfoIn) -> AgentInfo:
    return AgentInfo(
        hostname=payload.hostname,
        os_info=payload.os_info,
        agent_version=payload.agent_version,
        jobs=[job.model_dump() for job in payload.jobs],
    )


def _current_node(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> AgentNodeRecord:
    node = pull_agents.authenticate(db, authorization)
    if node is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown agent token; re-run the install command")
    if not node.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Node is disabled on the hub")
    return node


@router.get("/agent/{file_name}", response_class=PlainTextResponse)
def agent_file(file_name: str):
    text = pull_agents.read_agent_file(file_name)
    if text is None:
        raise HTTPException(status_code=404, detail="Not found")
    return PlainTextResponse(text, headers={"Cache-Control": "no-store"})


@router.get("/api/agent/v1/ping")
def ping():
    return {"ok": True, "agent_version": pull_agents.latest_agent_version()}


@router.post("/api/agent/v1/enroll")
def enroll(payload: AgentEnrollIn, request: Request, db: Session = Depends(get_db)):
    try:
        node, agent_token = pull_agents.enroll_node(
            db,
            payload.enroll_token,
            _info(payload),
            requested_name=payload.name,
            remote_ip=_remote_ip(request),
        )
    except EnrollError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    logger.info("Agent enrolled: %s (host=%s ip=%s)", node.name, node.hostname, node.remote_ip)
    return {"ok": True, "name": node.name, "agent_token": agent_token}


@router.post("/api/agent/v1/poll")
def poll(
    payload: AgentInfoIn,
    request: Request,
    node: AgentNodeRecord = Depends(_current_node),
    db: Session = Depends(get_db),
):
    run = pull_agents.record_poll(db, node, _info(payload), _remote_ip(request))
    return {"ok": True, "name": node.name, "run": run}


@router.post("/api/agent/v1/report")
async def report(
    payload: AgentReportIn,
    request: Request,
    node: AgentNodeRecord = Depends(_current_node),
    db: Session = Depends(get_db),
):
    items = [
        ReportItem(
            job_name=item.job_name,
            status=item.status,
            source=item.source,
            message=item.message,
            started_at=item.started_at,
            ended_at=item.ended_at,
            raw_payload=item.raw_payload,
        )
        for item in payload.items
    ]
    normalized = pull_agents.record_report(db, node, payload.run_id, items)

    alert_ok = True
    try:
        await request.app.state.alert_manager.send_alerts(normalized)
    except Exception:
        logger.exception("Alert dispatch failed after agent report from %s.", node.name)
        alert_ok = False
    return {"ok": True, "created": len(normalized), "alert_ok": alert_ok}
