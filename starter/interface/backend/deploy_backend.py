from __future__ import annotations

import argparse
import posixpath
import shlex

from common import BackendError, load_state, print_json, run, save_state, ssh_argv

REQUIRED_STATES = ["network_state", "github_state", "hardware_profile"]
COMPOSE_NAMES = ["compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"]


def require_confirmed(project: str) -> dict[str, dict]:
    states = {name: load_state(project, name) for name in REQUIRED_STATES}
    problems = []
    allowed = {
        "network_state": {"confirmed"},
        "github_state": {"confirmed"},
        "hardware_profile": {"confirmed"},
    }
    for name, state in states.items():
        if not state:
            problems.append(f"{name}: missing")
        elif state.get("status") not in allowed[name]:
            problems.append(f"{name}: status={state.get('status')}")
    if problems:
        raise BackendError("deployment gates failed: " + "; ".join(problems))
    return states


def ssh(project: str, host: str, user: str, port: int, command: str, timeout: int = 1200):
    return run(ssh_argv(project, host, user, port, command), timeout=timeout, check=True)


def repo_basename(repo: str) -> str:
    path = repo.split(":", 1)[1] if repo.startswith("git@") else repo.rstrip("/").split("/")[-1]
    return path.removesuffix(".git") or "HILTester"


def default_workspace(repo: str) -> str:
    return f"~/hiltester/{repo_basename(repo)}"


def find_compose(project: str, host: str, user: str, port: int, workspace: str) -> str:
    names = " ".join(f"-name {shlex.quote(name)}" for name in COMPOSE_NAMES)
    # shellcheck-style grouping is intentional: find needs escaped parentheses remotely.
    command = (
        f"cd {shlex.quote(workspace)} && "
        f"find . -maxdepth 3 -type f \\( {names.replace(' -name ', ' -o -name ', 3).replace('-o -name', '-name', 1)} \\) -print"
    )
    result = ssh(project, host, user, port, command, timeout=60)
    candidates = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    # Prefer a standard compose name at repo root if present.
    root_candidates = [c for c in candidates if c.count("/") == 1 and c.removeprefix("./") in COMPOSE_NAMES]
    if len(root_candidates) == 1:
        return root_candidates[0]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise BackendError("no Compose file found in the repository (searched three directory levels)")
    raise BackendError("multiple Compose files found; deployment needs a one-time selection: " + ", ".join(candidates))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Deploy the selected GitHub repository to the Pi, auto-discovering the Compose file and its images/tags."
    )
    p.add_argument("--project", required=True)
    p.add_argument("--workspace", help="optional override; defaults to ~/hiltester/<repo-name>")
    p.add_argument("--compose-file", help="optional override only when more than one Compose file exists")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-hardware-gate", action="store_true", help="development-only override")
    return p


def main() -> int:
    args = parser().parse_args()
    if args.skip_hardware_gate:
        states = {name: load_state(args.project, name) for name in REQUIRED_STATES}
        for name in ["network_state", "github_state"]:
            if not states.get(name):
                raise BackendError(f"{name} is missing")
    else:
        states = require_confirmed(args.project)

    net = states["network_state"]
    gh = states["github_state"]
    host = net["tailscale_ip"]
    user = net["ssh_user"]
    port = int(net.get("ssh_port", 22))
    repo = gh["repo"]
    branch = gh["branch"]
    workspace = args.workspace or default_workspace(repo)

    install_docker = "command -v docker >/dev/null 2>&1 || (curl -fsSL https://get.docker.com | sh)"
    sync_repo = (
        f"mkdir -p {shlex.quote(posixpath.dirname(workspace))} && "
        f"if [ -d {shlex.quote(workspace)}/.git ]; then "
        f"git -C {shlex.quote(workspace)} fetch --prune && "
        f"git -C {shlex.quote(workspace)} checkout {shlex.quote(branch)} && "
        f"git -C {shlex.quote(workspace)} pull --ff-only; "
        f"else git clone --branch {shlex.quote(branch)} {shlex.quote(repo)} {shlex.quote(workspace)}; fi"
    )

    state = {
        "step": "deploy",
        "status": "planned" if args.dry_run else "running",
        "host": host,
        "repo": repo,
        "branch": branch,
        "workspace": workspace,
        "compose_file": args.compose_file,
        "compose_discovery": "automatic unless overridden",
        "results": [],
    }
    save_state(args.project, "deploy_state", state)

    if args.dry_run:
        state["planned_commands"] = [
            install_docker,
            sync_repo,
            "auto-discover Compose file",
            "docker compose config",
            "docker compose pull",
            "docker compose up -d --no-build",
            "docker compose ps",
        ]
        save_state(args.project, "deploy_state", state)
        print_json(state)
        return 0

    for name, command in [("docker_check", install_docker), ("repo_sync", sync_repo)]:
        result = ssh(args.project, host, user, port, command)
        state["results"].append({"name": name, "command": command, "stdout": result.stdout, "stderr": result.stderr})
        state["status"] = f"{name}_complete"
        save_state(args.project, "deploy_state", state)

    compose = args.compose_file or find_compose(args.project, host, user, port, workspace)
    state["compose_file"] = compose
    save_state(args.project, "deploy_state", state)

    compose_q = shlex.quote(compose)
    workspace_q = shlex.quote(workspace)
    commands = [
        ("compose_validate", f"cd {workspace_q} && docker compose -f {compose_q} config"),
        ("image_pull", f"cd {workspace_q} && docker compose -f {compose_q} pull"),
        ("compose_up", f"cd {workspace_q} && docker compose -f {compose_q} up -d --no-build"),
        ("compose_status", f"cd {workspace_q} && docker compose -f {compose_q} ps"),
    ]
    for name, command in commands:
        result = ssh(args.project, host, user, port, command)
        state["results"].append({"name": name, "command": command, "stdout": result.stdout, "stderr": result.stderr})
        state["status"] = f"{name}_complete"
        save_state(args.project, "deploy_state", state)

    state["status"] = "success"
    save_state(args.project, "deploy_state", state)
    print_json(state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackendError as exc:
        print_json({"status": "error", "error": str(exc)})
        raise SystemExit(2)
