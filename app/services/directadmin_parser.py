from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.services.types import NormalizedJobStatus

DA_TS_PATTERN = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s+(?:AM|PM))$", re.IGNORECASE)


def _parse_da_datetime(line: str) -> datetime | None:
    text = line.strip()
    match = DA_TS_PATTERN.match(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1).upper(), "%m/%d/%Y %I:%M %p")
    except ValueError:
        return None


def _parse_node_and_path(raw_path: str) -> tuple[str, str]:
    if "::" in raw_path:
        node, path = raw_path.split("::", 1)
        return node.strip() or "unknown-node", path.strip()
    return "local", raw_path


def _extract_status(lines: list[str]) -> tuple[str, str, int, str]:
    if not lines:
        return "unknown", "Log file is empty", 0, ""

    lower_lines = [line.lower() for line in lines]
    backed_up_count = sum(1 for line in lower_lines if " has been backed up" in line)
    error_lines: list[str] = []
    for line in lines:
        low = line.lower()
        if "an error occurred during the backup" in low:
            error_lines.append(line.strip())
        elif low.startswith("error "):
            error_lines.append(line.strip())
        elif "error copying" in low:
            error_lines.append(line.strip())

    if error_lines:
        return "failed", error_lines[-1], backed_up_count, (error_lines[0] if len(error_lines) > 1 else error_lines[-1])
    if backed_up_count > 0:
        return "success", f"Backed up users: {backed_up_count}", backed_up_count, ""
    return "unknown", "Could not infer final state from directadmin log", backed_up_count, ""


class DirectAdminParser:
    def __init__(self, settings: Settings):
        self.settings = settings

    def collect(self) -> list[NormalizedJobStatus]:
        if not self.settings.directadmin_enabled:
            return []

        paths = self.settings.directadmin_log_path_list
        if not paths:
            return [
                NormalizedJobStatus(
                    source="directadmin",
                    job_name="directadmin_config_missing",
                    status="unknown",
                    message="DIRECTADMIN_LOG_PATHS is empty",
                    raw_payload={},
                )
            ]

        output: list[NormalizedJobStatus] = []
        for raw_path in paths:
            node_name, parsed_path = _parse_node_and_path(raw_path)
            path = Path(parsed_path)
            if not path.exists():
                output.append(
                    NormalizedJobStatus(
                        source="directadmin",
                        job_name=f"[{node_name}] {path.stem or str(path)}",
                        status="failed",
                        message=f"Log file not found: {path}",
                        raw_payload={"path": str(path), "node": node_name},
                    )
                )
                continue

            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError as exc:
                output.append(
                    NormalizedJobStatus(
                        source="directadmin",
                        job_name=f"[{node_name}] {path.stem or str(path)}",
                        status="failed",
                        message=f"Cannot read log file: {exc}",
                        raw_payload={"path": str(path), "node": node_name},
                    )
                )
                continue

            if len(lines) > self.settings.directadmin_max_lines_per_file:
                lines = lines[-self.settings.directadmin_max_lines_per_file :]

            status, message, backed_up_count, first_error = _extract_status(lines)
            ended_at = None
            for line in reversed(lines):
                ts = _parse_da_datetime(line)
                if ts:
                    ended_at = ts
                    break

            output.append(
                NormalizedJobStatus(
                    source="directadmin",
                    job_name=f"[{node_name}] {path.stem or path.name}",
                    status=status,
                    message=message,
                    started_at=None,
                    ended_at=ended_at,
                    raw_payload={
                        "node": node_name,
                        "path": str(path),
                        "backed_up_users": backed_up_count,
                        "first_error": first_error,
                        "last_line": next((ln for ln in reversed(lines) if ln.strip()), ""),
                    },
                )
            )
        return output
