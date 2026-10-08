"""Agent node helper, run inside the app container:

  docker exec backup-dashboard-app python -m app.agent_cli list
  docker exec backup-dashboard-app python -m app.agent_cli enroll [--name NAME]
  docker exec backup-dashboard-app python -m app.agent_cli health [NAME]
  docker exec backup-dashboard-app python -m app.agent_cli trigger NAME|--all

`enroll` prints the one-line install command for a new pull agent (same as the dashboard "Add node").
`health` checks hub -> node for legacy nodes (signature, IP allowlist, receiver up) and the last poll
for pull agents; it never runs a job.
`trigger` runs a legacy node's configured action now, or queues a run for a pull agent (picked up on
its next poll, within about a minute) - same as the dashboard "Trigger Agents" button.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import AgentNodeRecord
from app.services import pull_agents
from app.services.agent_schedule import parse_trigger_times
from app.services.agent_trigger import AgentNode, AgentTriggerService


def _mask(payload: dict | None) -> dict:
    return {
        key: ("***" if any(word in key.lower() for word in ("token", "secret")) else value)
        for key, value in (payload or {}).items()
    }


def _legacy_nodes(service: AgentTriggerService) -> list[AgentNode]:
    return service.list_nodes()


def _pull_nodes(db, name: str | None = None) -> list[AgentNodeRecord]:
    query = db.query(AgentNodeRecord).order_by(AgentNodeRecord.name)
    if name:
        query = query.filter(AgentNodeRecord.name == name)
    return query.all()


def _pick(service: AgentTriggerService, db, name: str | None) -> tuple[list[AgentNode], list[AgentNodeRecord]]:
    legacy = _legacy_nodes(service)
    pull = _pull_nodes(db)
    if not name:
        return legacy, pull
    picked_pull = [node for node in pull if node.name == name]
    picked_legacy = [] if picked_pull else [node for node in legacy if node.name == name]
    if not picked_pull and not picked_legacy:
        known = ", ".join([node.name for node in pull] + [node.name for node in legacy]) or "(none)"
        sys.exit(f"Node not found: {name}. Known nodes: {known}")
    return picked_legacy, picked_pull


def _print_result(result: dict) -> None:
    mark = "OK  " if result["ok"] else "FAIL"
    print(f"[{mark}] {result['name']} -> HTTP {result['status_code']} exit_code={result.get('exit_code')} {result['response']}")


def _pull_line(node: AgentNodeRecord, settings) -> str:
    state = "online" if pull_agents.is_online(node, settings) else "OFFLINE"
    if not node.enabled:
        state = "disabled"
    jobs = pull_agents.node_to_dict(node, settings, "")["jobs"]
    job_names = ",".join(job.get("job_name", "") for job in jobs) or "(no jobs)"
    pending = " | run pending" if node.run_id else ""
    return (
        f"- {node.name} | pull | {state} | last_seen={pull_agents.as_utc(node.last_seen_at)} | host={node.hostname}"
        f" | ip={node.remote_ip} | v{node.agent_version} | jobs={job_names} | last_run={node.last_run_summary or '-'}{pending}"
    )


def cmd_list(service: AgentTriggerService, db) -> int:
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

    legacy = _legacy_nodes(service)
    pull = _pull_nodes(db)
    print(
        f"auto trigger: {schedule} | pull agents: {len(pull)} | "
        f"legacy nodes: {len(legacy)} (AGENT_TRIGGER_ENABLED={settings.agent_trigger_enabled})"
    )
    for node in pull:
        print(_pull_line(node, settings))
    for node in legacy:
        print(
            f"- {node.name} | legacy | {node.url} | action={node.action} | verify_ssl={node.verify_ssl}"
            f" | secret_len={len(node.shared_secret)} | payload={_mask(node.payload)}"
        )
    return 0


def cmd_enroll(db, name: str, hub_url: str) -> int:
    settings = get_settings()
    hub_url = (hub_url or settings.agent_hub_url).strip().rstrip("/")
    if not hub_url:
        sys.exit("Set AGENT_HUB_URL in .env (e.g. https://103.238.214.35) or pass --hub-url.")
    cert_sha1 = pull_agents.hub_cert_sha1(settings, hub_url)
    if hub_url.startswith("https://") and not cert_sha1:
        sys.exit("Cannot read the hub TLS certificate. Set AGENT_HUB_CERT_SHA1 or AGENT_TLS_PROBE_ADDR in .env.")
    try:
        token, enrollment = pull_agents.create_enrollment(db, settings, name)
    except ValueError as exc:
        sys.exit(str(exc))

    print(f"Node name : {enrollment.node_name or '(hostname of the node)'}")
    print(f"Expires   : {pull_agents.as_utc(enrollment.expires_at).isoformat()} (one-time use)")
    print(f"Hub cert  : {cert_sha1 or '(none, http)'}")
    print("Paste into PowerShell (Run as Administrator) on the node:\n")
    print(pull_agents.build_install_command(hub_url, cert_sha1, token))
    return 0


async def cmd_health(service: AgentTriggerService, db, name: str | None) -> int:
    failed = 0
    legacy, pull = _pick(service, db, name)
    for node in pull:
        ok = node.enabled and pull_agents.is_online(node, service.settings)
        print(f"[{'OK  ' if ok else 'FAIL'}] {_pull_line(node, service.settings)[2:]}")
        failed += 0 if ok else 1
    for node in legacy:
        result = await service.trigger_node(dataclasses.replace(node, action="health", payload={}))
        _print_result(result)
        failed += 0 if result["ok"] else 1
    return 1 if failed else 0


async def cmd_trigger(service: AgentTriggerService, db, name: str | None) -> int:
    failed = 0
    legacy, pull = _pick(service, db, name)
    for node in pull:
        if not node.enabled:
            print(f"[SKIP] {node.name} is disabled")
            continue
        _print_result(pull_agents.queue_run(db, node, service.settings))
    for node in legacy:
        result = await service.trigger_node(node)
        _print_result(result)
        failed += 0 if result["ok"] else 1
    if pull:
        print("Pull agents run on their next poll (~1 min); results show up on the dashboard.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.agent_cli", description="Check / trigger / enroll agent nodes.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show pull agents and AGENT_NODES_JSON nodes (secrets masked)")
    enroll = sub.add_parser("enroll", help="print a one-time install command for a new pull agent")
    enroll.add_argument("--name", default="", help="node name (default: the node's hostname)")
    enroll.add_argument("--hub-url", default="", help="URL nodes use to reach the hub (default: AGENT_HUB_URL)")
    health = sub.add_parser("health", help="check nodes, does not run any job")
    health.add_argument("name", nargs="?", help="node name (default: all nodes)")
    trigger = sub.add_parser("trigger", help="run the node's job(s)")
    target = trigger.add_mutually_exclusive_group(required=True)
    target.add_argument("name", nargs="?", help="node name")
    target.add_argument("--all", action="store_true", help="trigger every node")
    args = parser.parse_args()

    init_db()
    service = AgentTriggerService(get_settings())
    db = SessionLocal()
    try:
        if args.command == "list":
            return cmd_list(service, db)
        if args.command == "enroll":
            return cmd_enroll(db, args.name, args.hub_url)
        if args.command == "health":
            return asyncio.run(cmd_health(service, db, args.name))
        return asyncio.run(cmd_trigger(service, db, None if args.all else args.name))
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
