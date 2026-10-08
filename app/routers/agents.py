from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AgentNodeRecord
from app.schemas import AgentEnrollmentCreateIn, AgentNodeUpdateIn
from app.services import pull_agents
from app.services.auth import require_admin

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _hub_url(request: Request) -> str:
    settings = request.app.state.settings
    if settings.agent_hub_url.strip():
        return settings.agent_hub_url.strip().rstrip("/")
    # Behind nginx the app only sees http://app:8000; use what the browser used.
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _legacy_nodes(request: Request):
    if not request.app.state.settings.agent_trigger_enabled:
        return []
    return request.app.state.agent_trigger_service.list_nodes()


def _pull_nodes(db: Session) -> list[AgentNodeRecord]:
    return db.query(AgentNodeRecord).order_by(AgentNodeRecord.name).all()


def _get_pull_node(db: Session, node_id: int) -> AgentNodeRecord:
    node = db.get(AgentNodeRecord, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Agent node not found")
    return node


@router.get("")
def list_agents(
    request: Request,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    latest = pull_agents.latest_agent_version()
    legacy = _legacy_nodes(request)
    nodes = [pull_agents.node_to_dict(node, settings, latest) for node in _pull_nodes(db)]
    return {
        "legacy_enabled": bool(settings.agent_trigger_enabled),
        "legacy": [{"name": n.name, "url": n.url, "action": n.action, "verify_ssl": n.verify_ssl} for n in legacy],
        "nodes": nodes,
        "count": len(legacy) + len(nodes),
        "triggerable": len(legacy) + sum(1 for node in nodes if node["enabled"]),
        "latest_agent_version": latest,
        "offline_after_minutes": settings.agent_offline_after_minutes,
        "auto_trigger_times": settings.agent_auto_trigger_times,
    }


@router.post("/enrollments")
def create_enrollment(
    payload: AgentEnrollmentCreateIn,
    request: Request,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    hub_url = _hub_url(request)
    cert_sha1 = pull_agents.hub_cert_sha1(settings, hub_url)
    if hub_url.startswith("https://") and not cert_sha1:
        raise HTTPException(
            status_code=500,
            detail="Cannot read the hub TLS certificate. Set AGENT_HUB_CERT_SHA1 (or AGENT_TLS_PROBE_ADDR) in .env.",
        )
    try:
        token, enrollment = pull_agents.create_enrollment(db, settings, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "command": pull_agents.build_install_command(hub_url, cert_sha1, token),
        "hub_url": hub_url,
        "cert_sha1": cert_sha1,
        "node_name": enrollment.node_name,
        "expires_at": pull_agents.as_utc(enrollment.expires_at).isoformat(),
    }


@router.post("/trigger-all")
async def trigger_all_agents(
    request: Request,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    nodes = [node for node in _pull_nodes(db) if node.enabled]
    if not settings.agent_trigger_enabled and not nodes:
        raise HTTPException(status_code=400, detail="No agents: enroll a node or set AGENT_TRIGGER_ENABLED=true")

    results = []
    if settings.agent_trigger_enabled:
        results.extend(await request.app.state.agent_trigger_service.trigger_all())
    results.extend(pull_agents.queue_run(db, node, settings) for node in nodes)
    ok_count = sum(1 for item in results if item.get("ok"))
    return {
        "ok": ok_count == len(results) if results else True,
        "count": len(results),
        "ok_count": ok_count,
        "failed_count": len(results) - ok_count,
        "results": results,
    }


@router.post("/trigger/{name}")
async def trigger_agent_by_name(
    name: str,
    request: Request,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    node = db.query(AgentNodeRecord).filter(AgentNodeRecord.name == name).one_or_none()
    if node is not None:
        if not node.enabled:
            raise HTTPException(status_code=400, detail="Agent node is disabled")
        result = pull_agents.queue_run(db, node, settings)
        return {"ok": True, "result": result}

    if not settings.agent_trigger_enabled:
        raise HTTPException(status_code=404, detail=f"Agent node not found: {name}")
    service = request.app.state.agent_trigger_service
    try:
        result = await service.trigger_by_name(name)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"ok": bool(result.get("ok")), "result": result}


@router.patch("/nodes/{node_id}")
def update_node(
    node_id: int,
    payload: AgentNodeUpdateIn,
    request: Request,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    node = _get_pull_node(db, node_id)
    if payload.enabled and not node.enabled:
        # A disabled agent gets 403 on poll, so last_seen is stale; restart the offline window now.
        node.last_seen_at = pull_agents.utcnow()
        node.offline_alerted = False
    node.enabled = payload.enabled
    if not node.enabled:
        node.run_id = None
        node.run_requested_at = None
        node.run_claimed_at = None
    db.commit()
    settings = request.app.state.settings
    return pull_agents.node_to_dict(node, settings, pull_agents.latest_agent_version())


@router.delete("/nodes/{node_id}")
def delete_node(
    node_id: int,
    _user: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    node = _get_pull_node(db, node_id)
    db.delete(node)
    db.commit()
    return {"ok": True}
