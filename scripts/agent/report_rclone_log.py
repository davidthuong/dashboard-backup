#!/usr/bin/env python3
"""
Read a rclone log file, infer final status, and push status to central hub.

Example:
  python scripts/agent/report_rclone_log.py \
    --hub http://10.10.10.10:8000 \
    --token your_ingest_token \
    --node linux-bk01 \
    --job daily-home \
    --log /var/log/rclone/daily-home.log
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

TS_PATTERN = re.compile(r"^(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})")
ERROR_WORDS = ("failed to", "critical", "fatal")
ERRORS_COUNT_PATTERN = re.compile(r"errors:\s*(\d+)", re.IGNORECASE)
DA_MARKER_PATTERN = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]\s+(START|SUCCESS|FAILED)\s+backup\s+user=([^\s]+)(.*)$",
    re.IGNORECASE,
)
SOURCE_FILE_PATTERN = re.compile(r"\bfile=([^\s]+)")
EXIT_CODE_PATTERN = re.compile(r"\bexit_code=(\d+)")
REASON_PATTERN = re.compile(r"\breason=([^\s]+)")
TARGET_PATH_PATTERN = re.compile(r"->\s*(.+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True, help="Central dashboard URL. Example: http://10.0.0.10:8000")
    parser.add_argument("--token", required=True, help="INGEST_API_TOKEN configured on hub")
    parser.add_argument("--node", required=True, help="Node name, ex: linux-bk01")
    parser.add_argument("--job", required=True, help="Logical job name")
    parser.add_argument("--log", required=True, help="Path to rclone log file")
    parser.add_argument("--source", default="rclone", help="Source name (default: rclone)")
    parser.add_argument("--max-lines", type=int, default=3000, help="Tail lines to parse (default: 3000)")
    parser.add_argument("--dry-run", action="store_true", help="Print payload only, do not send")
    return parser.parse_args()


def tail_lines(path: Path, max_lines: int) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return lines
    return lines[-max_lines:]


def parse_ts_text(line: str) -> datetime | None:
    m = TS_PATTERN.match(line.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None


def parse_marker_ts_text(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def detect_status(lines: list[str]) -> tuple[str, str]:
    if not lines:
        return "unknown", "Log file is empty"

    window = "\n".join(lines[-250:]).lower()
    errors_count = None
    for m in ERRORS_COUNT_PATTERN.finditer(window):
        try:
            errors_count = int(m.group(1))
        except ValueError:
            errors_count = None
    has_error = any(word in window for word in ERROR_WORDS)
    if errors_count is not None and errors_count > 0:
        has_error = True
    if errors_count == 0 and not has_error:
        return "success", "errors: 0"
    if has_error:
        return "failed", "error keyword detected in log"
    if "transferred:" in window or "elapsed time:" in window:
        return "success", "transfer summary detected"
    return "unknown", "cannot infer final status from log"


def detect_json_status(lines: list[str]) -> tuple[str | None, str | None, datetime | None]:
    for raw in reversed(lines):
        line = raw.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        level = str(item.get("level", "")).lower()
        msg = str(item.get("msg", "")).strip()
        ts = None
        t = item.get("time")
        if t:
            try:
                ts = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            except ValueError:
                ts = None
        if level in {"error", "fatal"}:
            return "failed", (msg or "error from json log"), ts
        if msg and ("transferred" in msg.lower() or "elapsed time" in msg.lower()):
            return "success", msg, ts
    return None, None, None


def detect_ended_at(lines: list[str]) -> datetime:
    for raw in reversed(lines):
        ts = parse_ts_text(raw)
        if ts:
            return ts
        stripped = raw.strip()
        if stripped.startswith("{"):
            try:
                item = json.loads(stripped)
                if item.get("time"):
                    return datetime.fromisoformat(str(item["time"]).replace("Z", "+00:00"))
            except Exception:
                pass
    return datetime.now(timezone.utc)


def detect_marker_items(lines: list[str], job_name: str) -> list[dict]:
    active_starts: dict[str, datetime | None] = {}
    active_source_file: dict[str, str] = {}
    latest_by_user: dict[str, dict] = {}
    found = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = DA_MARKER_PATTERN.match(line)
        if not m:
            continue
        found = True

        ts = parse_marker_ts_text(m.group(1))
        event = m.group(2).upper()
        user = (m.group(3) or "").strip() or "unknown-user"
        detail = (m.group(4) or "").strip()
        effective_job = f"{job_name}:{user}"
        source_match = SOURCE_FILE_PATTERN.search(detail)
        source_file = source_match.group(1).strip() if source_match else ""
        target_match = TARGET_PATH_PATTERN.search(detail)
        target_path = target_match.group(1).strip() if target_match else ""
        exit_code_match = EXIT_CODE_PATTERN.search(detail)
        exit_code = exit_code_match.group(1).strip() if exit_code_match else ""
        reason_match = REASON_PATTERN.search(detail)
        reason = reason_match.group(1).strip() if reason_match else ""

        if event == "START":
            active_starts[user] = ts
            if source_file:
                active_source_file[user] = source_file
            continue

        status = "success" if event == "SUCCESS" else "failed"
        source_file = active_source_file.get(user, source_file)
        if status == "success":
            if source_file and target_path:
                message = f"{source_file} -> {target_path}"
            elif target_path:
                message = f"-> {target_path}"
            else:
                message = detail or "backup completed"
        else:
            if source_file and exit_code:
                message = f"{source_file}; exit_code={exit_code}"
            else:
                message = detail or "backup failed"
        latest_by_user[user] = {
            "job_name": effective_job,
            "status": status,
            "message": message,
            "started_at": active_starts.get(user),
            "ended_at": ts,
            "user": user,
            "event": event,
            "detail": detail,
            "source_file": source_file,
            "target_path": target_path,
            "exit_code": exit_code,
            "reason": reason,
        }
        active_starts.pop(user, None)
        active_source_file.pop(user, None)

    if not found:
        return []

    for user, started_at in active_starts.items():
        if user in latest_by_user:
            continue
        source_file = active_source_file.get(user, "")
        message = f"in progress: {source_file}" if source_file else "backup in progress"
        latest_by_user[user] = {
            "job_name": f"{job_name}:{user}",
            "status": "running",
            "message": message,
            "started_at": started_at,
            "ended_at": None,
            "user": user,
            "event": "START",
            "detail": "open run without terminal SUCCESS/FAILED yet",
            "source_file": source_file,
            "target_path": "",
            "exit_code": "",
            "reason": "",
        }

    return [latest_by_user[user] for user in sorted(latest_by_user.keys(), key=str.lower)]


def post_status(hub: str, token: str, payload: dict) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{hub.rstrip('/')}/api/ingest/status",
        data=body,
        headers={"Content-Type": "application/json", "X-Ingest-Token": token},
        method="POST",
    )
    with request.urlopen(req, timeout=20) as resp:
        return resp.status, resp.read().decode("utf-8")


def main() -> int:
    args = parse_args()
    log_path = Path(args.log)
    if not log_path.exists():
        print(f"Log file not found: {log_path}", file=sys.stderr)
        return 2

    try:
        lines = tail_lines(log_path, max_lines=max(100, args.max_lines))
    except Exception as exc:
        print(f"Failed to read log: {exc}", file=sys.stderr)
        return 2

    marker_items = detect_marker_items(lines, args.job)
    if marker_items:
        payload_items = []
        for item in marker_items:
            payload_items.append(
                {
                    "node": args.node,
                    "source": args.source,
                    "job_name": item["job_name"],
                    "status": item["status"],
                    "message": item["message"],
                    "started_at": item["started_at"].isoformat() if item.get("started_at") else None,
                    "ended_at": item["ended_at"].isoformat() if item.get("ended_at") else None,
                    "raw_payload": {
                        "log_path": str(log_path),
                        "line_count": len(lines),
                        "last_line": (lines[-1].strip() if lines else ""),
                        "format": "da_marker",
                        "user": item.get("user"),
                        "event": item.get("event"),
                        "detail": item.get("detail"),
                        "source_file": item.get("source_file"),
                        "target_path": item.get("target_path"),
                        "exit_code": item.get("exit_code"),
                        "reason": item.get("reason"),
                    },
                }
            )
    else:
        status, message = detect_status(lines)
        ended_at = detect_ended_at(lines)
        json_status, json_message, json_ended_at = detect_json_status(lines)
        if json_status:
            status = json_status
        if json_message:
            message = json_message
        if json_ended_at:
            ended_at = json_ended_at

        payload_items = [
            {
                "node": args.node,
                "source": args.source,
                "job_name": args.job,
                "status": status,
                "message": message,
                "ended_at": ended_at.isoformat(),
                "raw_payload": {
                    "log_path": str(log_path),
                    "line_count": len(lines),
                    "last_line": (lines[-1].strip() if lines else ""),
                },
            }
        ]

    payload = {"items": payload_items}

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    try:
        status_code, content = post_status(args.hub, args.token, payload)
        print(status_code, content)
    except Exception as exc:
        print(f"Push failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
