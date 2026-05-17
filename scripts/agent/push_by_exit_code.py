#!/usr/bin/env python3
"""
Push status by exit code to central dashboard.

Usage:
  python scripts/agent/push_by_exit_code.py \
    --hub http://10.10.10.10:8000 \
    --token your_ingest_token \
    --node win-bk01 \
    --source rclone \
    --job nightly-share \
    --exit-code 0 \
    --message "rclone sync completed"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib import request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True, help="Central dashboard URL")
    parser.add_argument("--token", required=True, help="INGEST_API_TOKEN")
    parser.add_argument("--node", required=True, help="Node name")
    parser.add_argument("--source", default="rclone", help="Source name")
    parser.add_argument("--job", required=True, help="Job name")
    parser.add_argument("--exit-code", type=int, required=True, help="Exit code from backup command")
    parser.add_argument("--message", default="", help="Extra message")
    parser.add_argument("--dry-run", action="store_true", help="Print payload only")
    return parser.parse_args()


def map_status(code: int) -> str:
    if code == 0:
        return "success"
    if code in {1, 2, 3}:
        return "warning"
    return "failed"


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
    status = map_status(args.exit_code)
    msg = f"exit_code={args.exit_code}"
    if args.message:
        msg = f"{msg}; {args.message}"

    payload = {
        "items": [
            {
                "node": args.node,
                "source": args.source,
                "job_name": args.job,
                "status": status,
                "message": msg,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "raw_payload": {"exit_code": args.exit_code},
            }
        ]
    }

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

