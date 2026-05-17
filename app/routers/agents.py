from fastapi import APIRouter, Depends, HTTPException, Request

from app.services.auth import require_admin

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents(
    request: Request,
    _user: str = Depends(require_admin),
):
    settings = request.app.state.settings
    if not settings.agent_trigger_enabled:
        return {"enabled": False, "items": []}

    service = request.app.state.agent_trigger_service
    nodes = service.list_nodes()
    return {
        "enabled": True,
        "items": [{"name": n.name, "url": n.url, "action": n.action, "verify_ssl": n.verify_ssl} for n in nodes],
        "count": len(nodes),
    }


@router.post("/trigger-all")
async def trigger_all_agents(
    request: Request,
    _user: str = Depends(require_admin),
):
    settings = request.app.state.settings
    if not settings.agent_trigger_enabled:
        raise HTTPException(status_code=400, detail="Agent trigger is disabled")

    service = request.app.state.agent_trigger_service
    results = await service.trigger_all()
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
):
    settings = request.app.state.settings
    if not settings.agent_trigger_enabled:
        raise HTTPException(status_code=400, detail="Agent trigger is disabled")

    service = request.app.state.agent_trigger_service
    try:
        result = await service.trigger_by_name(name)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"ok": bool(result.get("ok")), "result": result}

