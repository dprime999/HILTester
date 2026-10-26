from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class BackendError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_root() -> Path:
    override = os.environ.get("HILTESTER_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".hiltester" / "projects").resolve()


def project_dir(project: str) -> Path:
    safe = "".join(c for c in project if c.isalnum() or c in "-_.").strip(".")
    if not safe:
        raise BackendError("project name must contain at least one safe character")
    path = state_root() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(project: str, name: str) -> Path:
    return project_dir(project) / f"{name}.json"


def load_state(project: str, name: str) -> dict[str, Any]:
    path = state_path(project, name)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(project: str, name: str, data: dict[str, Any]) -> Path:
    path = state_path(project, name)
    data = {**data, "updated_at": utc_now()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        tmp = Path(f.name)
    tmp.replace(path)
    return path


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(argv: Iterable[str], *, timeout: int = 120, check: bool = False, env: dict[str, str] | None = None) -> CommandResult:
    args = [str(x) for x in argv]
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        shell=False,
    )
    result = CommandResult(args, proc.returncode, proc.stdout.strip(), proc.stderr.strip())
    if check and not result.ok:
        msg = result.stderr or result.stdout or f"command failed with exit code {result.returncode}"
        raise BackendError(f"{' '.join(args)}: {msg}")
    return result


def require_executable(name: str) -> str:
    from shutil import which

    path = which(name)
    if not path:
        raise BackendError(f"required executable not found on PATH: {name}")
    return path


def ensure_project_ssh_key(project: str) -> tuple[Path, Path]:
    """Return a project-scoped SSH keypair, creating it when absent."""
    key_dir = project_dir(project) / "ssh"
    key_dir.mkdir(parents=True, exist_ok=True)
    private_key = key_dir / "id_ed25519"
    public_key = key_dir / "id_ed25519.pub"

    if private_key.exists() and public_key.exists():
        return private_key, public_key

    require_executable("ssh-keygen")
    result = run([
        "ssh-keygen", "-t", "ed25519", "-N", "", "-C", f"hiltester:{project}",
        "-f", str(private_key),
    ], timeout=30)
    if not result.ok:
        raise BackendError(result.stderr or "ssh-keygen failed")
    try:
        os.chmod(private_key, 0o600)
    except OSError:
        pass
    return private_key, public_key


def project_ssh_private_key(project: str) -> Path | None:
    path = project_dir(project) / "ssh" / "id_ed25519"
    return path if path.exists() else None


def ssh_argv(project: str, host: str, user: str, port: int, command: str, *, connect_timeout: int = 10) -> list[str]:
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}",
        "-o", "StrictHostKeyChecking=accept-new", "-p", str(port),
    ]
    key = project_ssh_private_key(project)
    if key:
        argv += ["-i", str(key)]
    argv += [f"{user}@{host}", command]
    return argv


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))
