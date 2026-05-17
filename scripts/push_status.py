#!/usr/bin/env python3
"""
Push a single backup status record to central dashboard.

Usage:
  python scripts/push_status.py \
    --hub http://127.0.0.1:8000 \
    --token your_ingest_token \
    --node linux-bk01 \
    --source rclone \
    --job nightly-home \
    --status success \
    --message "errors=0"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib import request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True, help="Central dashboard URL. Example: http://10.0.0.10:8000")
    parser.add_argument("--token", required=True, help="INGEST_API_TOKEN configured on hub")
    parser.add_argument("--node", required=True, help="Source node name, ex: win-bk01")
    parser.add_argument("--source", default="rclone", help="Data source, default rclone")
    parser.add_argument("--job", required=True, help="Job name")
    parser.add_argument("--status", required=True, help="success|warning|failed|running|unknown")
    parser.add_argument("--message", default="", help="Optional status message")
    parser.add_argument("--ended-at", default="", help="ISO datetime, default now UTC")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ended_at = args.ended_at.strip() or datetime.now(timezone.utc).isoformat()
    payload = {
        "items": [
            {
                "node": args.node,
                "source": args.source,
                "job_name": args.job,
                "status": args.status,
                "message": args.message,
                "ended_at": ended_at,
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{args.hub.rstrip('/')}/api/ingest/status",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Ingest-Token": args.token,
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            print(resp.status, resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"Push failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

