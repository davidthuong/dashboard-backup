"""Agent node helper, run inside the app container:

  docker exec backup-dashboard-app python -m app.agent_cli list
  docker exec backup-dashboard-app python -m app.agent_cli health [NAME]
  docker exec backup-dashboard-app python -m app.agent_cli trigger NAME|--all

`health` only checks hub -> node (signature, IP allowlist, receiver up); it never runs a job.
`trigger` runs the node's configured action, same as the dashboard "Trigger Agents" button.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys

from app.config import get_settings
from app.services.agent_schedule import parse_trigger_times
from app.services.agent_trigger import AgentNode, AgentTriggerService


def _mask(payload: dict | None) -> dict:
    return {
        key: ("***" if any(word in key.lower() for word in ("token", "secret")) else value)
        for key, value in (payload or {}).items()
    }


def _pick(nodes: list[AgentNode], name: str | None) -> list[AgentNode]:
    if not name:
        return nodes
    picked = [node for node in nodes if node.name == name]
    if not picked:
        known = ", ".join(node.name for node in nodes) or "(none)"
        sys.exit(f"Node not found: {name}. Known nodes: {known}")
    return picked


def _print_result(result: dict) -> None:
    mark = "OK  " if result["ok"] else "FAIL"
    print(f"[{mark}] {result['name']} -> HTTP {result['status_code']} exit_code={result.get('exit_code')} {result['response']}")


def cmd_list(service: AgentTriggerService) -> int:
    settings = service.settings
    times = settings.agent_auto_trigger_times.strip()
    if times:
        try:
            parse_trigger_times(times)
            schedule = f"{times} ({settings.timezone})"
        except ValueError as exc:
            schedule = f"INVALID - {exc}"
    else:
        schedule = "off (manual only)"

    nodes = service.list_nodes()
    print(f"AGENT_TRIGGER_ENABLED={settings.agent_trigger_enabled} | auto trigger: {schedule} | nodes: {len(nodes)}")
    for node in nodes:
        print(
            f"- {node.name} | {node.url} | action={node.action} | verify_ssl={node.verify_ssl}"
            f" | secret_len={len(node.shared_secret)} | payload={_mask(node.payload)}"
        )
    return 0


async def cmd_health(service: AgentTriggerService, name: str | None) -> int:
    failed = 0
    for node in _pick(service.list_nodes(), name):
        result = await service.trigger_node(dataclasses.replace(node, action="health", payload={}))
        _print_result(result)
        failed += 0 if result["ok"] else 1
    return 1 if failed else 0


async def cmd_trigger(service: AgentTriggerService, name: str | None) -> int:
    failed = 0
    for node in _pick(service.list_nodes(), name):
        result = await service.trigger_node(node)
        _print_result(result)
        failed += 0 if result["ok"] else 1
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.agent_cli", description="Check / trigger agent nodes from AGENT_NODES_JSON.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show nodes parsed from AGENT_NODES_JSON (secrets masked)")
    health = sub.add_parser("health", help="signed health check, does not run any job")
    health.add_argument("name", nargs="?", help="node name (default: all nodes)")
    trigger = sub.add_parser("trigger", help="run the node's configured action")
    target = trigger.add_mutually_exclusive_group(required=True)
    target.add_argument("name", nargs="?", help="node name")
    target.add_argument("--all", action="store_true", help="trigger every node")
    args = parser.parse_args()

    service = AgentTriggerService(get_settings())
    if args.command == "list":
        return cmd_list(service)
    if args.command == "health":
        return asyncio.run(cmd_health(service, args.name))
    return asyncio.run(cmd_trigger(service, None if args.all else args.name))


if __name__ == "__main__":
    sys.exit(main())
