from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


@dataclass
class ParsedLogSource:
    node_name: str
    raw_spec: str
    is_remote: bool
    local_path: Path | None = None
    ssh_user: str = ""
    ssh_host: str = ""
    ssh_port: int = 22
    remote_path: str = ""

    @property
    def display_name(self) -> str:
        if self.is_remote:
            p = Path(self.remote_path.strip())
            return p.stem or p.name or self.remote_path
        if self.local_path is None:
            return self.raw_spec
        return self.local_path.stem or self.local_path.name or str(self.local_path)

    @property
    def path_for_payload(self) -> str:
        if self.is_remote:
            return f"ssh://{self.ssh_user}@{self.ssh_host}:{self.ssh_port}{self.remote_path}"
        if self.local_path is None:
            return self.raw_spec
        return str(self.local_path)


def parse_log_source(raw_path: str, default_node: str = "local") -> ParsedLogSource:
    if "::" in raw_path:
        node_name, path_spec = raw_path.split("::", 1)
    else:
        node_name, path_spec = default_node, raw_path
    node_name = node_name.strip() or "unknown-node"
    path_spec = path_spec.strip()

    if path_spec.lower().startswith("ssh://"):
        parsed = urlparse(path_spec)
        host = (parsed.hostname or "").strip()
        user = (parsed.username or "root").strip()
        remote_path = unquote(parsed.path or "").strip()
        port = int(parsed.port or 22)
        if not host:
            raise ValueError(f"Invalid ssh source (missing host): {path_spec}")
        if not remote_path:
            raise ValueError(f"Invalid ssh source (missing path): {path_spec}")
        if not remote_path.startswith("/"):
            remote_path = f"/{remote_path}"
        return ParsedLogSource(
            node_name=node_name,
            raw_spec=path_spec,
            is_remote=True,
            ssh_user=user,
            ssh_host=host,
            ssh_port=port,
            remote_path=remote_path,
        )

    return ParsedLogSource(node_name=node_name, raw_spec=path_spec, is_remote=False, local_path=Path(path_spec))


def read_tail_lines(source: ParsedLogSource, max_lines: int) -> list[str]:
    if source.is_remote:
        return _read_ssh_tail(source, max_lines=max_lines)
    return _read_local_lines(source, max_lines=max_lines)


def _read_local_lines(source: ParsedLogSource, max_lines: int) -> list[str]:
    if source.local_path is None:
        raise RuntimeError("Local source path is missing")
    lines = source.local_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if len(lines) > max_lines:
        return lines[-max_lines:]
    return lines


def _read_ssh_tail(source: ParsedLogSource, max_lines: int) -> list[str]:
    remote_target = f"{source.ssh_user}@{source.ssh_host}"
    # Use remote tail to avoid copying full log over network.
    remote_cmd = f"tail -n {max(10, max_lines)} -- {shlex.quote(source.remote_path)}"
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=12",
        "-p",
        str(source.ssh_port),
        remote_target,
        remote_cmd,
    ]
    try:
        res = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ssh client is not installed on hub container/host") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"SSH read timeout: {remote_target}:{source.remote_path}") from exc

    if res.returncode != 0:
        stderr = (res.stderr or "").strip()
        if not stderr:
            stderr = (res.stdout or "").strip()
        raise RuntimeError(f"SSH read failed ({remote_target}): {stderr or 'unknown error'}")

    lines = res.stdout.splitlines()
    if len(lines) > max_lines:
        return lines[-max_lines:]
    return lines
