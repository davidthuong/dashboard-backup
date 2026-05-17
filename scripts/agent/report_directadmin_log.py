#!/usr/bin/env python3
"""
Parse DirectAdmin backup log and push status to hub.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

DA_TS_PATTERN = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s+(?:AM|PM))$", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True, help="Hub URL")
    parser.add_argument("--token", required=True, help="INGEST_API_TOKEN")
    parser.add_argument("--node", required=True, help="Node name")
    parser.add_argument("--job", default="directadmin-nightly", help="Job name")
    parser.add_argument("--log", required=True, help="DirectAdmin backup log file")
    parser.add_argument("--dry-run", action="store_true", help="Print payload only")
    return parser.parse_args()


def _parse_dt(line: str) -> datetime | None:
    match = DA_TS_PATTERN.match(line.strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1).upper(), "%m/%d/%Y %I:%M %p").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def detect_status(lines: list[str]) -> tuple[str, str, int]:
    if not lines:
        return "unknown", "Log file is empty", 0

    backed_up_count = sum(1 for ln in lines if " has been backed up" in ln.lower())
    errors = []
    for ln in lines:
        low = ln.lower()
        if "an error occurred during the backup" in low:
            errors.append(ln.strip())
        elif low.startswith("error "):
            errors.append(ln.strip())
        elif "error copying" in low:
            errors.append(ln.strip())

    if errors:
        return "failed", errors[-1], backed_up_count
    if backed_up_count > 0:
        return "success", f"Backed up users: {backed_up_count}", backed_up_count
    return "unknown", "Could not infer final state from directadmin log", backed_up_count


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
        print(f"Log not found: {log_path}", file=sys.stderr)
        return 2

    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    status, message, backed_up = detect_status(lines)
    ended_at = datetime.now(timezone.utc)
    for line in reversed(lines):
        ts = _parse_dt(line)
        if ts:
            ended_at = ts
            break

    payload = {
        "items": [
            {
                "node": args.node,
                "source": "directadmin",
                "job_name": args.job,
                "status": status,
                "message": message,
                "ended_at": ended_at.isoformat(),
                "raw_payload": {
                    "backed_up_users": backed_up,
                    "log_path": str(log_path),
                    "last_line": (lines[-1].strip() if lines else ""),
                },
            }
        ]
    }

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    try:
        code, content = post_status(args.hub, args.token, payload)
        print(code, content)
    except Exception as exc:
        print(f"Push failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
