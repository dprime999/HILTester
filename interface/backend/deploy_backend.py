"""Provision HILTester's Docker/GitHub Actions runner on a Tailscale-connected Pi.

The PC runs this CLI. The Pi downloads the user's repository and prebuilt image.
No image compilation takes place during deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import gzip
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import BackendError, load_state, print_json, save_state, ssh_argv

DEFAULT_IMAGE = "ghcr.io/dprime999/hiltester-runner:latest"
API_VERSION = "2026-03-10"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
IMAGE_RE = re.compile(r"^[A-Za-z0-9_./:@-]+$")
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


def info(stage: str, message: str) -> None:
    print(f"[deploy:{stage}] {message}", file=sys.stderr, flush=True)


def parse_repo(value: str) -> str:
    value = value.strip()
    if value.startswith("https://github.com/"):
        value = value[len("https://github.com/"):]
    value = value.removesuffix(".git").strip("/")
    if not REPO_RE.fullmatch(value) or ".." in value:
        raise BackendError("--repo must be GitHub owner/name (or https://github.com/owner/name)")
    return value


def github_api(path: str, token: str, *, method: str = "GET") -> dict:
    req = urllib.request.Request(
        "https://api.github.com" + path,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "HILTester-deploy/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise BackendError(
            f"GitHub API {method} {path} failed (HTTP {exc.code}). "
            "Confirm the repository exists and token has repository Administration read/write permission."
        ) from None
    except (urllib.error.URLError, ValueError) as exc:
        raise BackendError(f"GitHub API communication failed: {exc}") from exc


def remote(project: str, net: dict, script: str, *, timeout: int = 1200, secret: str = "") -> str:
    """Send script on SSH stdin rather than exposing secrets in SSH command arguments."""
    cmd = ssh_argv(
        project, net["tailscale_ip"], net["ssh_user"],
        int(net.get("ssh_port", 22)), "bash -se",
    )
    try:
        # Windows text=True translates newlines on stdin to CRLF. Bash on the Pi
        # interprets the trailing CR as part of `pipefail`, causing an immediate
        # failure. Send UTF-8 bytes to preserve LF-only Bash scripts on every OS.
        payload = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        p = subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackendError(f"Pi SSH command failed: {type(exc).__name__}: {exc}") from exc
    if p.returncode:
        detail = (p.stderr or p.stdout or b"unknown remote error").decode("utf-8", errors="replace")[-1800:]
        if secret:
            detail = detail.replace(secret, "[REDACTED]")
        raise BackendError(f"Pi command failed (exit {p.returncode}): {detail}")
    output = p.stdout.decode("utf-8", errors="replace").strip()
    return output.replace(secret, "[REDACTED]") if secret else output


def quoted(value: str) -> str:
    return shlex.quote(value)


def compose_locator(workspace: str, choice: str | None) -> str:
    # Execute as root, so the repository and runtime can be managed consistently.
    if choice:
        if choice.startswith("/") or ".." in Path(choice).parts:
            raise BackendError("--compose-file must be a relative path within the repo")
        search = "candidate=" + quoted(choice) + "\n" + "test -f \"$candidate\" || { echo 'Compose file missing' >&2; exit 9; }"
    else:
        search = (
            "mapfile -d '' -t matches < <(find . -maxdepth 3 -type f "
            "\\( -name compose.yaml -o -name compose.yml -o -name docker-compose.yaml -o -name docker-compose.yml \\) -print0)\n"
            "if [ ${#matches[@]} -eq 0 ]; then echo 'No Compose file in repository' >&2; exit 9; fi\n"
            "candidate=''\n"
            "for f in \"${matches[@]}\"; do case \"$f\" in "
            "./compose.yaml|./compose.yml|./docker-compose.yaml|./docker-compose.yml) "
            "if [ -n \"$candidate\" ]; then echo 'Multiple root Compose files; use --compose-file' >&2; exit 9; fi; candidate=\"$f\" ;; esac; done\n"
            "if [ -z \"$candidate\" ]; then "
            "if [ ${#matches[@]} -ne 1 ]; then echo 'Multiple Compose files; use --compose-file' >&2; exit 9; fi; "
            "candidate=\"${matches[0]}\"; fi"
        )
    return "cd " + quoted(workspace) + "\n" + search + "\nprintf '%s\\n' \"$candidate\""


def provision_script(repo: str, branch: str, image: str, name: str, labels: str, token: str,
                     compose_file: str | None, workspace: str, offline: bool = False) -> str:
    # Token never appears in the SSH command line, state JSON, or remote Git URL.
    # Root-owned .env is required by the current entrypoint, which requests a fresh
    # runner-registration token when the container starts.
    q = quoted
    locator = compose_locator(workspace, compose_file)
    image_action = ("printf '[pi] using preloaded release image\\n'\n"
                    if offline else "docker compose --env-file \"$runtime/runner.env\" -f \"$candidate\" -f \"$runtime/image-override.yml\" pull")
    up_action = "docker compose --env-file \"$runtime/runner.env\" -f \"$candidate\" -f \"$runtime/image-override.yml\" up -d --no-build" + (" --pull never" if offline else "")
    return f'''set -Eeuo pipefail
sudo -n true || {{ echo "Passwordless sudo required for initial Docker setup" >&2; exit 3; }}
sudo -n bash -se <<'HIL_ROOT_PROVISION'
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
umask 077
printf '[pi] checking packages\\n'
if ! command -v git >/dev/null || ! command -v curl >/dev/null || ! command -v ca-certificates >/dev/null; then
  apt-get update -qq
  apt-get install -y git curl ca-certificates
fi
if ! command -v docker >/dev/null; then
  printf '[pi] installing Docker\\n'
  curl -fsSL https://get.docker.com -o /tmp/hiltester-get-docker.sh
  sh /tmp/hiltester-get-docker.sh
  rm -f /tmp/hiltester-get-docker.sh
fi
systemctl enable --now docker
docker info >/dev/null
if ! docker compose version >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y docker-compose-plugin || {{ echo 'Docker Compose plugin installation failed' >&2; exit 4; }}
fi
printf '[pi] Docker is ready\\n'
workspace={q(workspace)}
mkdir -p "$(dirname "$workspace")"
if [ -e "$workspace" ] && [ ! -d "$workspace/.git" ]; then
  echo 'Existing workspace is not a git repository; refusing to overwrite' >&2
  exit 6
fi
repo={q(repo)}
branch={q(branch)}
# Ephemeral askpass program permits cloning a private repository without saving
# the PAT in a git remote URL or git credential store.
askpass=$(mktemp /tmp/hiltester-askpass.XXXXXX)
printf '%s\\n' '#!/bin/sh' 'case "$1" in *Username*) printf %s x-access-token ;; *) printf %s "$HIL_GIT_TOKEN" ;; esac' > "$askpass"
chmod 700 "$askpass"
export GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 HIL_GIT_TOKEN={q(token)}
trap 'rm -f "$askpass"; unset HIL_GIT_TOKEN' EXIT
if [ -d "$workspace/.git" ]; then
  existing=$(git -C "$workspace" remote get-url origin)
  case "$existing" in "https://github.com/$repo"|"https://github.com/$repo.git") ;; *) echo 'Workspace origin mismatch; refusing sync' >&2; exit 7 ;; esac
  if [ -n "$(git -C "$workspace" status --porcelain)" ]; then echo "Workspace has uncommitted changes; refusing update" >&2; exit 8; fi
  git -C "$workspace" fetch --prune origin "$branch"
  git -C "$workspace" checkout -B "$branch" "origin/$branch"
else
  git clone --depth 1 --branch "$branch" "https://github.com/$repo.git" "$workspace"
fi
unset HIL_GIT_TOKEN
rm -f "$askpass"
trap - EXIT
printf '[pi] repository synced\\n'
# Store runtime credentials outside the GitHub checkout. Root-only because the
# compose configuration must pass GH_TOKEN to the existing entrypoint.
runtime=/etc/hiltester
mkdir -p "$runtime"
chmod 700 "$runtime"
cat > "$runtime/runner.env" <<'HIL_RUNTIME_ENV'
GH_REPO={repo}
GH_TOKEN={token}
RUNNER_NAME={name}
RUNNER_LABELS={labels}
HIL_RUNTIME_ENV
chmod 600 "$runtime/runner.env"
# Avoid exporting the token into command output or JSON logs.
# Use a separate override so user starter Compose needs no changes.
cat > "$runtime/image-override.yml" <<'HIL_RUNTIME_OVERRIDE'
services:
  github-runner:
    image: {image}
    pull_policy: {"never" if offline else "missing"}
HIL_RUNTIME_OVERRIDE
chmod 600 "$runtime/image-override.yml"
{locator}
printf '[pi] preparing runner image\\n'
cd "$workspace"
docker compose --env-file "$runtime/runner.env" -f "$candidate" -f "$runtime/image-override.yml" config --quiet
{image_action}
{up_action}
printf '[pi] container status\\n'
docker compose --env-file "$runtime/runner.env" -f "$candidate" -f "$runtime/image-override.yml" ps --format json
HIL_ROOT_PROVISION
'''


def load_offline_image(project: str, net: dict, archive: Path, image: str, expected_sha256: str | None) -> str:
    """Stream compressed Docker archive PC -> Pi over SSH (no remote GHCR)."""
    if not archive.is_file():
        raise BackendError(f"release Docker image archive missing: {archive}")
    if expected_sha256:
        digest = hashlib.sha256()
        with archive.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected_sha256:
            raise BackendError("offline image SHA256 mismatch; refusing load")
    check = remote(project, net, "sudo -n docker image inspect " + quoted(image) + " >/dev/null 2>&1 && echo PRESENT || true", timeout=45)
    if check.strip() == "PRESENT":
        info("image", "image already cached on Pi; skipping transfer")
        return "cached"
    info("image", f"streaming {archive.stat().st_size / 1024**2:.1f} MiB compressed image to Pi")
    cmd = ssh_argv(project, net["tailscale_ip"], net["ssh_user"], int(net.get("ssh_port", 22)),
                   "gzip -dc | sudo -n docker load")
    # Linux/Windows subprocess binary streaming: no file upload or shell pipeline on PC.
    try:
        with archive.open("rb") as f:
            result = subprocess.run(cmd, stdin=f, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackendError(f"offline image transfer failed: {exc}") from exc
    if result.returncode:
        raise BackendError("offline Docker load failed: " + result.stderr.decode("utf-8", "replace")[-1200:])
    verify = remote(project, net, "sudo -n docker image inspect " + quoted(image) + " >/dev/null && echo READY", timeout=45)
    if verify.strip() != "READY":
        raise BackendError("loaded Docker archive does not contain the release image tag")
    return "loaded"


def check_runner(project: str, net: dict, repo: str, token: str, name: str, seconds: int) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        data = github_api(f"/repos/{repo}/actions/runners?per_page=100", token)
        match = next((r for r in data.get("runners", []) if r.get("name") == name), None)
        if match and match.get("status") == "online":
            return {"name": name, "status": "online", "id": match.get("id"), "labels": [x.get("name") for x in match.get("labels", [])]}
        time.sleep(5)
    logs = remote(project, net, "sudo -n docker logs --tail 35 stm32-hil-runner 2>&1 || true", timeout=40)
    raise BackendError(f"Runner '{name}' did not report online within {seconds}s. Recent container logs:\n{logs[-1800:]}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deploy a prebuilt HILTester GitHub Actions runner to a bare Pi 5")
    p.add_argument("--project", required=True, help="local project/state name")
    p.add_argument("--repo", required=True, help="user's GitHub owner/repo, e.g. dprime999/HILStarter")
    p.add_argument("--branch", help="branch; defaults to GitHub default branch")
    p.add_argument("--image", default=DEFAULT_IMAGE, help="runner image reference")
    p.add_argument("--image-archive", help="local .tar.gz Docker save archive; disables GHCR pulls")
    p.add_argument("--image-sha256", help="expected SHA256 of image archive")
    p.add_argument("--token-env", default="GITHUB_TOKEN", help="environment variable with GitHub token")
    p.add_argument("--workspace", help="absolute Pi path; default /opt/hiltester/<repo>")
    p.add_argument("--compose-file", help="relative path to Compose file when multiple are present")
    p.add_argument("--runner-name", help="runner name; defaults to hil-<project>-pi5")
    p.add_argument("--runner-labels", default="hil,docker,pi5")
    p.add_argument("--wait-seconds", type=int, default=90)
    p.add_argument("--dry-run", action="store_true", help="do not contact or change GitHub or Pi")
    p.add_argument("--require-hardware", action="store_true", help="also require confirmed hardware profile")
    return p


def main() -> int:
    args = parser().parse_args()
    repo = parse_repo(args.repo)
    if not IMAGE_RE.fullmatch(args.image):
        raise BackendError("invalid Docker image reference")
    if args.image_archive and not Path(args.image_archive).is_file():
        raise BackendError("bundled Docker archive not found")
    if args.branch and (not BRANCH_RE.fullmatch(args.branch) or ".." in args.branch):
        raise BackendError("invalid --branch")
    net = load_state(args.project, "network_state")
    if not net or net.get("status") != "confirmed" or not net.get("tailscale_ip"):
        raise BackendError("network_state must be confirmed; run network_tailscale first")
    if args.require_hardware and load_state(args.project, "hardware_profile").get("status") != "confirmed":
        raise BackendError("hardware_profile not confirmed")
    token = os.environ.get(args.token_env, "")
    if token and not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise BackendError("GitHub token has unexpected characters; refusing unsafe transfer")
    if not token and not args.dry_run:
        raise BackendError(f"{args.token_env} is missing in this process environment")
    name = args.runner_name or ("hil-" + re.sub(r"[^A-Za-z0-9_-]", "-", args.project)[:35] + "-pi5")
    labels = args.runner_labels
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not re.fullmatch(r"[A-Za-z0-9_,.-]+", labels):
        raise BackendError("invalid runner name or labels")
    workspace = args.workspace or f"/opt/hiltester/{repo.split('/')[1]}"
    if not workspace.startswith("/") or ".." in Path(workspace).parts or any(c in workspace for c in "\n\r"):
        raise BackendError("--workspace must be a safe absolute Pi path")
    branch = args.branch
    state = {"step": "deploy", "status": "planned", "repo": repo, "branch": branch or "(GitHub default)",
             "image": args.image, "workspace": workspace, "runner_name": name, "runner_labels": labels,
             "host": net["tailscale_ip"], "token_persisted_in_local_state": False,
             "remote_runtime_credentials": "/etc/hiltester/runner.env (root-only)", "results": []}
    if args.dry_run:
        state["planned_steps"] = ["check GitHub repo and runner registration permissions", "SSH over Tailscale", "install Git/curl/Docker/Compose if missing", "clone or sync repo ON THE PI", "write root-only runner credentials", "load bundled ARM64 image" if args.image_archive else "pull selected ARM64 image", "start Compose", "verify GitHub runner online"]
        print_json(state)
        return 0
    try:
        info("github", "checking repository access and runner registration permissions")
        metadata = github_api(f"/repos/{repo}", token)
        branch = branch or metadata.get("default_branch")
        if not branch:
            raise BackendError("repository has no default branch; initialize the GitHub starter first")
        if not BRANCH_RE.fullmatch(branch):
            raise BackendError("unsupported branch name; provide --branch explicitly")
        github_api(f"/repos/{repo}/actions/runners/registration-token", token, method="POST")
        state["branch"] = branch
        state["status"] = "deploying"
        save_state(args.project, "deploy_state", state)
        info("ssh", "installing/verifying Docker and downloading project on Pi")
        if args.image_archive:
            # Docker must exist before `docker load` is available.
            remote(project, net, "sudo -n bash -se <<'HIL_BOOTSTRAP_DOCKER'\nset -euo pipefail\nif ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com -o /tmp/hiltester-docker.sh; sh /tmp/hiltester-docker.sh; rm -f /tmp/hiltester-docker.sh; fi\nsystemctl enable --now docker\ndocker info >/dev/null\nHIL_BOOTSTRAP_DOCKER", timeout=1200)
            state["image_source"] = load_offline_image(project, net, Path(args.image_archive), args.image, args.image_sha256)
        script = provision_script(repo, branch, args.image, name, labels, token, args.compose_file, workspace, offline=bool(args.image_archive))
        output = remote(args.project, net, script, timeout=1800, secret=token)
        state["results"].append({"stage": "pi_deploy", "stdout_tail": output[-1200:]})
        save_state(args.project, "deploy_state", state)
        info("runner", "waiting for GitHub to report runner online")
        runner = check_runner(args.project, net, repo, token, name, max(5, args.wait_seconds))
        state["runner"] = runner
        state["status"] = "success"
        save_state(args.project, "deploy_state", state)
        print_json(state)
        return 0
    except BackendError as exc:
        state["status"] = "error"
        state["error"] = str(exc).replace(token, "[REDACTED]") if token else str(exc)
        save_state(args.project, "deploy_state", state)
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackendError as exc:
        print_json({"status": "error", "error": str(exc)})
        raise SystemExit(2)
