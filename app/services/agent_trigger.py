from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings


@dataclass
class AgentNode:
    name: str
    url: str
    shared_secret: str
    verify_ssl: bool = True
    timeout_seconds: int = 15
    action: str = "rclone_log_push"
    payload: dict[str, Any] | None = None


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_exit_code(data: Any) -> int | None:
    # Receivers answer {"ok": true, "result": ...} even when the push script failed;
    # the script's exit code is inside result (a dict, or a list with stdout lines + dict on Windows).
    result = data.get("result") if isinstance(data, dict) else None
    candidates = result if isinstance(result, list) else [result]
    for item in candidates:
        if isinstance(item, dict) and "exit_code" in item:
            value = item.get("exit_code")
            return None if value is None else _safe_int(value, 1)
    return None


class AgentTriggerService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def list_nodes(self) -> list[AgentNode]:
        if not self.settings.agent_nodes_json.strip():
            return []
        try:
            parsed = json.loads(self.settings.agent_nodes_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid AGENT_NODES_JSON: {exc}") from exc

        if not isinstance(parsed, list):
            raise RuntimeError("AGENT_NODES_JSON must be a JSON array")

        nodes: list[AgentNode] = []
        for idx, item in enumerate(parsed, start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or f"node-{idx}").strip()
            url = str(item.get("url") or "").strip()
            secret = str(item.get("shared_secret") or "").strip()
            if not url or not secret:
                continue
            node = AgentNode(
                name=name,
                url=url,
                shared_secret=secret,
                verify_ssl=bool(item.get("verify_ssl", True)),
                timeout_seconds=max(5, min(60, _safe_int(item.get("timeout_seconds"), 15))),
                action=str(item.get("action") or "rclone_log_push").strip(),
                payload=item.get("payload") if isinstance(item.get("payload"), dict) else None,
            )
            nodes.append(node)
        return nodes

    def _sign(self, secret: str, timestamp: int, nonce: str, body: str) -> str:
        message = f"{timestamp}.{nonce}.{body}".encode("utf-8")
        return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    async def trigger_node(self, node: AgentNode) -> dict[str, Any]:
        timestamp = int(time.time())
        nonce = uuid.uuid4().hex
        payload = {
            "action": node.action,
            "request_id": uuid.uuid4().hex,
            "requested_by": "hub",
            "payload": node.payload or {},
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        signature = self._sign(node.shared_secret, timestamp, nonce, body)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Agent-Timestamp": str(timestamp),
            "X-Agent-Nonce": nonce,
            "X-Agent-Signature": signature,
        }

        try:
            async with httpx.AsyncClient(timeout=node.timeout_seconds, verify=node.verify_ssl) as client:
                response = await client.post(node.url, content=body.encode("utf-8"), headers=headers)
            data: dict[str, Any]
            try:
                data = response.json() if response.text else {}
            except ValueError:
                data = {"raw": response.text[:500]}
            exit_code = _extract_exit_code(data)
            body_ok = not (isinstance(data, dict) and data.get("ok") is False)
            return {
                "name": node.name,
                "url": node.url,
                "ok": response.status_code < 300 and body_ok and exit_code in (None, 0),
                "status_code": response.status_code,
                "exit_code": exit_code,
                "response": data,
            }
        except Exception as exc:
            return {
                "name": node.name,
                "url": node.url,
                "ok": False,
                "status_code": 0,
                "exit_code": None,
                "response": {"error": str(exc) or exc.__class__.__name__},
            }

    async def trigger_all(self) -> list[dict[str, Any]]:
        nodes = self.list_nodes()
        results: list[dict[str, Any]] = []
        for node in nodes:
            results.append(await self.trigger_node(node))
        return results

    async def trigger_by_name(self, name: str) -> dict[str, Any]:
        nodes = self.list_nodes()
        for node in nodes:
            if node.name == name:
                return await self.trigger_node(node)
        raise RuntimeError(f"Agent node not found: {name}")

