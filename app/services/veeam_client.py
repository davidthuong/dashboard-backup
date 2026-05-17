from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx

from app.config import Settings
from app.services.types import NormalizedJobStatus


def _first_non_empty(data: dict[str, Any], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _pick_datetime(item: dict[str, Any], keys: list[str]) -> datetime | None:
    for key in keys:
        parsed = _parse_datetime(item.get(key))
        if parsed:
            return parsed
    return None


def _map_status_value(value: str) -> str:
    text = value.strip().lower()
    if not text:
        return ""
    if text in {"success", "succeeded", "ok"}:
        return "success"
    if text in {"warning", "warnings"}:
        return "warning"
    if text in {"failed", "failure", "error"}:
        return "failed"
    if text in {"running", "inprogress", "in progress", "working", "processing"}:
        return "running"
    return text


def _resolve_veeam_status(item: dict[str, Any]) -> tuple[str, str]:
    # jobs/states often exposes scheduler state (Inactive/Active) and lastResult.
    # For dashboard purpose, lastResult is more useful as "latest status".
    last_result_raw = _first_non_empty(item, ["lastResult", "last_result", "result", "lastStatus", "lastState"], "")
    job_state_raw = _first_non_empty(item, ["status", "state", "activityStatus"], "")

    last_result = _map_status_value(last_result_raw)
    job_state = _map_status_value(job_state_raw)

    if job_state in {"running", "inprogress", "in progress", "working", "processing"}:
        return "running", f"job_state={job_state_raw}"
    if last_result:
        msg = f"last_result={last_result_raw}"
        if job_state_raw:
            msg = f"{msg}; job_state={job_state_raw}"
        return last_result, msg
    if job_state:
        return job_state, f"job_state={job_state_raw}"
    return "unknown", ""


def resolve_veeam_targets(settings: Settings) -> list[dict[str, Any]]:
    raw = settings.veeam_targets_json.strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid VEEAM_TARGETS_JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise RuntimeError("VEEAM_TARGETS_JSON must be a JSON array")

        targets: list[dict[str, Any]] = []
        for idx, item in enumerate(parsed, start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or f"veeam-{idx}").strip()
            base_url = str(item.get("base_url") or "").strip()
            username = str(item.get("username") or "").strip()
            password = str(item.get("password") or "").strip()
            if not base_url:
                continue

            targets.append(
                {
                    "name": name,
                    "base_url": base_url,
                    "username": username,
                    "password": password,
                    "verify_ssl": bool(item.get("verify_ssl", settings.veeam_verify_ssl)),
                    "token_url": str(item.get("token_url") or "").strip(),
                    "sessions_url": str(item.get("sessions_url") or "").strip(),
                    "timeout_seconds": int(item.get("timeout_seconds", settings.veeam_timeout_seconds)),
                    "api_version": str(item.get("api_version") or settings.veeam_api_version).strip(),
                }
            )
        return targets

    if not settings.veeam_base_url:
        return []
    return [
        {
            "name": "veeam-default",
            "base_url": settings.veeam_base_url,
            "username": settings.veeam_username,
            "password": settings.veeam_password,
            "verify_ssl": settings.veeam_verify_ssl,
            "token_url": settings.veeam_token_url,
            "sessions_url": settings.veeam_sessions_url,
            "timeout_seconds": settings.veeam_timeout_seconds,
            "api_version": settings.veeam_api_version,
        }
    ]


class VeeamClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def collect(self) -> list[NormalizedJobStatus]:
        if not self.settings.veeam_enabled:
            return []
        targets = resolve_veeam_targets(self.settings)
        if not targets:
            return [
                NormalizedJobStatus(
                    source="veeam",
                    job_name="veeam_config_missing",
                    status="unknown",
                    message="Set VEEAM_BASE_URL or VEEAM_TARGETS_JSON",
                    raw_payload={},
                )
            ]

        output: list[NormalizedJobStatus] = []
        for target in targets:
            target_name = str(target.get("name") or "veeam")
            try:
                token = await self._authenticate(target)
                items = await self._fetch_sessions(token, target)
                output.extend(items)
            except Exception as exc:
                output.append(
                    NormalizedJobStatus(
                        source="veeam",
                        job_name=f"[{target_name}] collection_error",
                        status="failed",
                        message=str(exc),
                        raw_payload={"target": target_name},
                    )
                )
        return output

    async def _authenticate(self, target: dict[str, Any]) -> str:
        base = str(target["base_url"]).rstrip("/")
        token_url = str(target.get("token_url") or f"{base}/api/oauth2/token")
        api_version = str(target.get("api_version") or "").strip()
        payload = {
            "grant_type": "password",
            "username": target.get("username", ""),
            "password": target.get("password", ""),
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if api_version:
            headers["x-api-version"] = api_version

        async with httpx.AsyncClient(verify=bool(target.get("verify_ssl")), timeout=int(target.get("timeout_seconds", 20))) as client:
            resp = await client.post(token_url, data=payload, headers=headers)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = (resp.text or "").strip()
                raise RuntimeError(
                    f"Veeam auth failed ({exc.response.status_code}) at {token_url}. "
                    f"Check username/password and VEEAM_API_VERSION. Response: {body[:600]}"
                ) from exc
            data = resp.json()
            token = data.get("access_token")
            if not token:
                raise RuntimeError("Veeam auth succeeded but access_token is missing")
            return token

    async def _fetch_sessions(self, token: str, target: dict[str, Any]) -> list[NormalizedJobStatus]:
        base = str(target["base_url"]).rstrip("/")
        sessions_url = str(target.get("sessions_url") or f"{base}/api/v1/jobs/states")
        api_version = str(target.get("api_version") or "").strip()
        headers = {"Authorization": f"Bearer {token}"}
        if api_version:
            headers["x-api-version"] = api_version

        async with httpx.AsyncClient(verify=bool(target.get("verify_ssl")), timeout=int(target.get("timeout_seconds", 20))) as client:
            resp = await client.get(sessions_url, headers=headers)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = (resp.text or "").strip()
                raise RuntimeError(
                    f"Veeam sessions query failed ({exc.response.status_code}) at {sessions_url}. "
                    f"Response: {body[:600]}"
                ) from exc
            payload = resp.json()

        if isinstance(payload, dict):
            records = payload.get("data") or payload.get("results") or payload.get("items") or []
        elif isinstance(payload, list):
            records = payload
        else:
            records = []

        output: list[NormalizedJobStatus] = []
        target_name = str(target.get("name") or "veeam")
        for item in records:
            if not isinstance(item, dict):
                continue
            job_name = _first_non_empty(item, ["jobName", "name", "job_name"], "unknown_veeam_job")
            status, status_note = _resolve_veeam_status(item)
            base_message = _first_non_empty(item, ["message", "reason", "details"], "")
            message = base_message if base_message else status_note
            started_at = _pick_datetime(item, ["startedAt", "startTime", "creationTime", "runStartTime"])
            ended_at = _pick_datetime(
                item,
                ["endedAt", "endTime", "completionTime", "lastEndTime", "runEndTime", "lastResultTime"],
            )

            # Some Veeam jobs/states payloads only expose lastRun without explicit endedAt.
            # For non-running statuses, treat lastRun as best-effort finish time.
            if ended_at is None and status in {"success", "warning", "failed"}:
                ended_at = _pick_datetime(item, ["lastRun", "lastRunTime", "latestRunTime", "lastRunAt"])
            output.append(
                NormalizedJobStatus(
                    source="veeam",
                    job_name=f"[{target_name}] {job_name}",
                    status=status,
                    message=message,
                    started_at=started_at,
                    ended_at=ended_at,
                    raw_payload={"target": target_name, "target_base_url": base, "payload": item},
                )
            )

        return output
