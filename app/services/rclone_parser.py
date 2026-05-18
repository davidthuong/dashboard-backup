from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.services.log_source import parse_log_source, read_tail_lines
from app.services.types import NormalizedJobStatus

RCLONE_TS_PATTERN = re.compile(r"^(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})")
RCLONE_ERRORS_PATTERN = re.compile(r"errors:\s*(\d+)", re.IGNORECASE)
RCLONE_DA_MARKER_PATTERN = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]\s+(START|SUCCESS|FAILED)\s+backup\s+user=([^\s]+)(.*)$",
    re.IGNORECASE,
)
RCLONE_SOURCE_FILE_PATTERN = re.compile(r"\bfile=([^\s]+)")
RCLONE_EXIT_CODE_PATTERN = re.compile(r"\bexit_code=(\d+)")
RCLONE_REASON_PATTERN = re.compile(r"\breason=([^\s]+)")
RCLONE_TARGET_PATH_PATTERN = re.compile(r"->\s*(.+)$")


def _parse_rclone_datetime(line: str) -> datetime | None:
    match = RCLONE_TS_PATTERN.match(line.strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None


def _parse_marker_datetime(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _last_non_empty(lines: list[str]) -> str:
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _status_from_text(lines: list[str]) -> tuple[str, str]:
    combined = "\n".join(lines[-250:]) if lines else ""
    lower = combined.lower()
    errors_count = None
    for m in RCLONE_ERRORS_PATTERN.finditer(lower):
        try:
            errors_count = int(m.group(1))
        except ValueError:
            errors_count = None
    has_error_word = "failed to" in lower or "critical" in lower or "fatal" in lower
    if errors_count is not None and errors_count > 0:
        has_error_word = True

    if errors_count == 0 and not has_error_word:
        return "success", "No error found in summary"
    if has_error_word:
        return "failed", "Error keyword found in latest log section"
    if "transferred:" in lower or "elapsed time:" in lower:
        return "success", "Transfer summary detected"
    return "unknown", "Could not infer final state from log"


def _status_from_json_logs(lines: list[str]) -> tuple[str, str, datetime | None]:
    status = "unknown"
    message = "JSON logs parsed"
    ended_at = None

    for line in reversed(lines):
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        level = str(item.get("level", "")).lower()
        msg = str(item.get("msg", "")).strip()
        timestamp = item.get("time")
        if timestamp and not ended_at:
            try:
                ended_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            except ValueError:
                ended_at = None

        if level in {"error", "fatal"}:
            status = "failed"
            message = msg or "Error from JSON log"
            break
        if msg and ("transferred" in msg.lower() or "elapsed time" in msg.lower()):
            status = "success"
            message = msg
            break

    return status, message, ended_at


def _parse_marker_jobs(path: Path, node_name: str, lines: list[str]) -> list[NormalizedJobStatus]:
    active_starts: dict[str, datetime | None] = {}
    active_source_file: dict[str, str] = {}
    latest_by_job: dict[str, NormalizedJobStatus] = {}
    marker_found = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        match = RCLONE_DA_MARKER_PATTERN.match(stripped)
        if not match:
            continue
        marker_found = True

        marker_ts_raw = match.group(1)
        marker_event = match.group(2).upper()
        user = match.group(3).strip() or "unknown-user"
        marker_rest = (match.group(4) or "").strip()
        marker_ts = _parse_marker_datetime(marker_ts_raw)

        job_name = f"[{node_name}] {(path.stem or path.name)}:{user}"
        if marker_event == "START":
            active_starts[user] = marker_ts
            source_match = RCLONE_SOURCE_FILE_PATTERN.search(marker_rest)
            if source_match:
                active_source_file[user] = source_match.group(1).strip()
            continue

        started_at = active_starts.get(user)
        source_file = active_source_file.get(user, "")
        status = "success" if marker_event == "SUCCESS" else "failed"
        target_path = ""
        exit_code = ""
        reason = ""
        target_match = RCLONE_TARGET_PATH_PATTERN.search(marker_rest)
        if target_match:
            target_path = target_match.group(1).strip()
        exit_code_match = RCLONE_EXIT_CODE_PATTERN.search(marker_rest)
        if exit_code_match:
            exit_code = exit_code_match.group(1).strip()
        reason_match = RCLONE_REASON_PATTERN.search(marker_rest)
        if reason_match:
            reason = reason_match.group(1).strip()
        if status == "success":
            if source_file and target_path:
                message = f"{source_file} -> {target_path}"
            elif target_path:
                message = f"-> {target_path}"
            else:
                message = marker_rest or "backup completed"
        else:
            if source_file and exit_code:
                message = f"{source_file}; exit_code={exit_code}"
            else:
                message = marker_rest or "backup failed"

        latest_by_job[job_name] = NormalizedJobStatus(
            source="rclone",
            job_name=job_name,
            status=status,
            message=message,
            started_at=started_at,
            ended_at=marker_ts,
            raw_payload={
                "path": str(path),
                "node": node_name,
                "format": "da_marker",
                "user": user,
                "event": marker_event,
                "detail": marker_rest,
                "source_file": source_file,
                "target_path": target_path,
                "exit_code": exit_code,
                "reason": reason,
            },
        )
        active_starts.pop(user, None)
        active_source_file.pop(user, None)

    if not marker_found:
        return []

    for user, started_at in active_starts.items():
        job_name = f"[{node_name}] {(path.stem or path.name)}:{user}"
        if job_name in latest_by_job:
            continue
        source_file = active_source_file.get(user, "")
        message = f"in progress: {source_file}" if source_file else "backup in progress"
        latest_by_job[job_name] = NormalizedJobStatus(
            source="rclone",
            job_name=job_name,
            status="running",
            message=message,
            started_at=started_at,
            ended_at=None,
            raw_payload={
                "path": str(path),
                "node": node_name,
                "format": "da_marker",
                "user": user,
                "event": "START",
                "detail": "open run without terminal SUCCESS/FAILED yet",
                "source_file": source_file,
            },
        )

    return [latest_by_job[name] for name in sorted(latest_by_job.keys(), key=str.lower)]


class RcloneParser:
    def __init__(self, settings: Settings):
        self.settings = settings

    def collect(self) -> list[NormalizedJobStatus]:
        if not self.settings.rclone_enabled:
            return []

        paths = self.settings.rclone_log_path_list
        if not paths:
            return [
                NormalizedJobStatus(
                    source="rclone",
                    job_name="rclone_config_missing",
                    status="unknown",
                    message="RCLONE_LOG_PATHS is empty",
                    raw_payload={},
                )
            ]

        output: list[NormalizedJobStatus] = []
        for raw_path in paths:
            try:
                source = parse_log_source(raw_path, default_node="local")
            except Exception as exc:
                output.append(
                    NormalizedJobStatus(
                        source="rclone",
                        job_name="rclone_source_parse_error",
                        status="failed",
                        message=str(exc),
                        raw_payload={"raw_path": raw_path},
                    )
                )
                continue

            try:
                lines = read_tail_lines(source, max_lines=self.settings.rclone_max_lines_per_file)
            except Exception as exc:
                output.append(
                    NormalizedJobStatus(
                        source="rclone",
                        job_name=f"[{source.node_name}] {source.display_name}",
                        status="failed",
                        message=f"Cannot read log source: {exc}",
                        raw_payload={"path": source.path_for_payload, "node": source.node_name},
                    )
                )
                continue

            display_path = Path(source.remote_path) if source.is_remote else (source.local_path or Path(source.raw_spec))
            node_name = source.node_name

            marker_records = _parse_marker_jobs(path=display_path, node_name=node_name, lines=lines)
            if marker_records:
                for item in marker_records:
                    if isinstance(item.raw_payload, dict):
                        item.raw_payload["path"] = source.path_for_payload
                output.extend(marker_records)
                continue

            status, message = _status_from_text(lines)
            ended_at = None
            for line in reversed(lines):
                ts = _parse_rclone_datetime(line)
                if ts:
                    ended_at = ts
                    break

            first_line = next((line.strip() for line in lines if line.strip()), "")
            if first_line.startswith("{"):
                json_status, json_message, json_end = _status_from_json_logs(lines)
                if json_status != "unknown":
                    status = json_status
                    message = json_message
                if json_end:
                    ended_at = json_end

            output.append(
                NormalizedJobStatus(
                    source="rclone",
                    job_name=f"[{node_name}] {source.display_name}",
                    status=status,
                    message=message if message else _last_non_empty(lines),
                    started_at=None,
                    ended_at=ended_at,
                    raw_payload={
                        "path": source.path_for_payload,
                        "node": node_name,
                        "last_line": _last_non_empty(lines),
                    },
                )
            )

        return output
