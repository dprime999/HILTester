"""Selective HILTester deprovisioning for a Pi and its GitHub starter repo.

Run beside common.py. Pi OS and boot partitions are always preserved.
Full deprovisioning can remove Docker, Tailscale and the HILTester SSH key.
"""
from __future__ import annotations

import argparse
import getpass
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from common import BackendError, load_state, save_state, ssh_argv, project_ssh_private_key

API = "https://api.github.com"
REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def notice(label, message):
    print(f"[clean:{label}] {message}", flush=True)


def api(method, endpoint, token, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        API + endpoint, data=body, method=method,
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {token}",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "HILTester-clean/1",
                 **({"Content-Type": "application/json"} if body is not None else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read(512).decode("utf-8", "replace")
        if exc.code == 401:
            raise BackendError(
                "GitHub rejected the token (HTTP 401 Bad credentials). "
                "Check for an expired, revoked, or incorrectly pasted token. "
                "For a classic PAT, the 'repo' scope is sufficient for repository runners "
                "when your GitHub account has repository admin access. "
                "No Pi cleanup was performed if this occurred during preflight."
            ) from None
        if exc.code == 403:
            raise BackendError(
                f"GitHub denied {method} {endpoint} (HTTP 403). "
                "Check repository admin permissions, token scopes, SSO authorization, or rate limits. "
                f"Response: {detail}"
            ) from None
        raise BackendError(f"GitHub {method} {endpoint}: HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise BackendError(f"GitHub API unavailable: {exc.reason}") from exc


def invoke_pi(project, net, script):
    cmd = ssh_argv(project, net["tailscale_ip"], net["ssh_user"], int(net.get("ssh_port", 22)), "bash -se")
    payload = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    try:
        p = subprocess.run(cmd, input=payload, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackendError(f"Pi SSH failed: {exc}") from exc
    stdout = p.stdout.decode("utf-8", "replace").strip()
    stderr = p.stderr.decode("utf-8", "replace").strip()
    if p.returncode:
        raise BackendError(f"Pi returned {p.returncode}: {(stderr or stdout)[-1200:]}")
    if stdout:
        for line in stdout.splitlines()[-12:]:
            notice("pi", line)


def pi_script(repo, workspace, name, stop, remove_files):
    """Never delete arbitrary paths; validate origin before touching workspace."""
    quoted = shlex.quote
    return f'''set -Eeuo pipefail
sudo -n true
sudo -n bash -se <<'HIL_CLEAN_ROOT'
set -Eeuo pipefail
workspace={quoted(workspace)}
repo={quoted(repo)}
runner={quoted(name)}
stop_requested={'1' if stop else '0'}
files_requested={'1' if remove_files else '0'}
case "$workspace" in /opt/hiltester/*) ;; *) echo 'Refusing workspace outside /opt/hiltester' >&2; exit 11 ;; esac
case "$workspace" in *'..'*|'/opt/hiltester/'|'/') echo 'Unsafe workspace' >&2; exit 11 ;; esac
if [ -d "$workspace" ]; then
  test -d "$workspace/.git" || {{ echo 'Workspace exists without .git; refusing removal' >&2; exit 12; }}
  origin=$(git -C "$workspace" remote get-url origin)
  case "$origin" in "https://github.com/$repo"|"https://github.com/$repo.git") ;; *) echo 'Repo origin mismatch; refusing removal' >&2; exit 12 ;; esac
fi
if [ "$stop_requested" = 1 ]; then
  echo 'Stopping targeted HILTester runner container'
  if command -v docker >/dev/null 2>&1; then
    ids=$(docker ps -aq --filter "label=com.docker.compose.service=github-runner" --filter "label=com.docker.compose.project={repo.split('/')[1].lower()}" || true)
    for id in $ids; do
      # Confirm the container has the expected configured runner name when available.
      docker stop "$id" >/dev/null || true
      docker rm "$id" >/dev/null || true
    done
  fi
fi
if [ "$files_requested" = 1 ]; then
  echo 'Deleting validated Pi repository checkout'
  if [ -d "$workspace/.git" ]; then rm -rf -- "$workspace"; fi
  # /etc/hiltester is currently shared among HILTester deployments. Only remove
  # runtime files when their GH_REPO identity matches the requested repository.
  if [ -f /etc/hiltester/runner.env ]; then
    if grep -Fqx "GH_REPO=$repo" /etc/hiltester/runner.env; then
      rm -f -- /etc/hiltester/runner.env /etc/hiltester/image-override.yml
      rmdir /etc/hiltester 2>/dev/null || true
      echo 'Removed matching runtime configuration'
    else
      echo 'Preserved runtime configuration (belongs to another repository)'
    fi
  fi
fi
echo 'Pi cleanup finished; OS, Docker installation, Tailscale and SSH untouched'
HIL_CLEAN_ROOT
'''



def docker_uninstall_script(repo):
    """Purge HILTester Docker installation only when no other containers remain."""
    project = shlex.quote(repo.split("/")[1].lower())
    return f'''set -Eeuo pipefail
sudo -n bash -se <<'HIL_DOCKER_CLEAN'
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
if command -v docker >/dev/null 2>&1; then
  # Do not destroy unrelated Docker workloads or volumes.
  others=$(docker ps -aq || true)
  if [ -n "$others" ]; then
    echo 'OTHER_DOCKER_CONTAINERS_PRESENT; refusing Docker removal' >&2
    exit 20
  fi
fi
echo 'Purging Docker packages and Docker-owned state'
if command -v apt-get >/dev/null 2>&1; then
  installed=$(dpkg-query -W -f='${{db:Status-Abbrev}} ${{Package}}\n' docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras docker.io docker-compose-v2 docker-compose 2>/dev/null | awk '$1 == "ii" {{print $2}}' | tr '\n' ' ' || true)
  if [ -n "$installed" ]; then
    apt-get purge -y $installed
    apt-get autoremove -y
  fi
else
  echo 'Unsupported package manager; refusing Docker removal' >&2
  exit 21
fi
# These paths hold Docker images, caches and volumes and are deleted intentionally.
rm -rf -- /var/lib/docker /var/lib/containerd
rm -f -- /etc/apt/sources.list.d/docker.sources /etc/apt/sources.list.d/docker.list /etc/apt/keyrings/docker.asc /etc/apt/keyrings/docker.gpg
echo 'Docker removal complete'
HIL_DOCKER_CLEAN
'''


def disconnect_script(remove_tailscale, remove_ssh, public_key):
    """Schedule final network/SSH cleanup so SSH can return acknowledgment first."""
    if remove_ssh and not public_key:
        raise BackendError("HILTester project public SSH key is required to remove access safely")
    # Key is a public key, not a password. Compare complete authorized_keys lines.
    encoded_key = base64.b64encode((public_key or "").encode("utf-8")).decode("ascii")
    final_script = f'''#!/bin/bash
set -Eeuo pipefail
exec >>/var/log/hiltester-final-clean.log 2>&1
printf 'Final HILTester deprovision started\n'
if [ "{'1' if remove_ssh else '0'}" = 1 ]; then
  key=$(printf '%s' '{encoded_key}' | base64 -d)
  # Remove only this project's SSH public key; do not disable system SSH.
  for f in /home/*/.ssh/authorized_keys /root/.ssh/authorized_keys; do
    [ -f "$f" ] || continue
    tmp=$(mktemp)
    grep -Fvx -- "$key" "$f" > "$tmp" || true
    cat "$tmp" > "$f"
    rm -f -- "$tmp"
  done
  echo 'Removed exact HILTester SSH authorized key where present'
fi
if [ "{'1' if remove_tailscale else '0'}" = 1 ]; then
  if command -v tailscale >/dev/null 2>&1; then tailscale logout || true; fi
  systemctl disable --now tailscaled 2>/dev/null || true
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y tailscale || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y || true
  fi
  rm -rf -- /var/lib/tailscale
  rm -f -- /etc/apt/sources.list.d/tailscale.list /etc/apt/sources.list.d/tailscale.sources /usr/share/keyrings/tailscale-archive-keyring.gpg
  echo 'Tailscale removed; this host may no longer be remotely reachable'
fi
echo 'Final HILTester deprovision complete'
rm -f -- /run/hiltester-final-clean.sh
'''
    b64 = base64.b64encode(final_script.encode("utf-8")).decode("ascii")
    return f'''set -Eeuo pipefail
sudo -n bash -se <<'HIL_FINAL_SCHEDULE'
set -Eeuo pipefail
command -v systemd-run >/dev/null || {{ echo 'systemd-run unavailable; refusing disconnecting cleanup' >&2; exit 24; }}
printf '%s' '{b64}' | base64 -d > /run/hiltester-final-clean.sh
chmod 700 /run/hiltester-final-clean.sh
systemd-run --unit=hiltester-final-deprovision --collect --on-active=15s /bin/bash /run/hiltester-final-clean.sh
echo 'Scheduled final cleanup in 15 seconds; SSH/Tailscale access may be lost'
HIL_FINAL_SCHEDULE
'''


def read_public_key(project):
    key = project_ssh_private_key(project)
    public = Path(str(key) + ".pub") if key else None
    if public and public.is_file():
        return public.read_text(encoding="utf-8").strip()
    return None

def runners(repo, token):
    prefix = f"/repos/{repo}/actions/runners"
    data = api("GET", prefix + "?per_page=100", token)
    return data.get("runners", [])


def github_preflight(repo, token, *, runner_access=False, github_clean=False):
    """Check GitHub credentials and required readable endpoints BEFORE Pi changes.

    A GET cannot guarantee future DELETE/PATCH success (branch protection, etc.),
    but it catches invalid/expired tokens and most permission problems early.
    """
    notice("github", "Checking token and repository access before any changes")
    user = api("GET", "/user", token)
    if not user.get("login"):
        raise BackendError("GitHub token validation returned no authenticated account")
    info = api("GET", f"/repos/{repo}", token)
    if info.get("archived") or info.get("disabled"):
        raise BackendError("Target GitHub repository is archived or disabled")
    if runner_access:
        runners(repo, token)  # requires access to repository Actions runners
    if github_clean:
        if not info.get("permissions", {}).get("push", False):
            raise BackendError("Authenticated GitHub account lacks push permission to clean this repo")
        # This still cannot predict protected-branch or ruleset restrictions.
    notice("github", f"Authenticated as {user['login']}; preflight OK")


def unregister(repo, token, name):
    matches = [r for r in runners(repo, token) if r.get("name") == name]
    if not matches:
        notice("runner", f"{name} not registered (already clean)")
        return
    if len(matches) != 1:
        raise BackendError("Multiple runners have this name; refusing ambiguous removal")
    api("DELETE", f"/repos/{repo}/actions/runners/{matches[0]['id']}", token)
    notice("runner", f"Removed runner {name} (id {matches[0]['id']}) from GitHub")


def clear_repo(repo, token, branch):
    """Commit removal of all tracked files from one branch; DOES NOT erase history."""
    prefix = "/repos/" + "/".join(quote(p, safe="") for p in repo.split("/"))
    metadata = api("GET", prefix, token)
    branch = branch or metadata.get("default_branch")
    if not branch or not re.fullmatch(r"[A-Za-z0-9_./-]+", branch) or ".." in branch:
        raise BackendError("Valid target branch required")
    ref = api("GET", prefix + "/git/ref/heads/" + quote(branch, safe=""), token)
    parent = ref["object"]["sha"]
    commit = api("GET", prefix + "/git/commits/" + parent, token)
    current_tree = commit["tree"]["sha"]
    listing = api("GET", prefix + "/git/trees/" + current_tree + "?recursive=1", token)
    if listing.get("truncated"):
        raise BackendError("Repository tree truncated; refusing to wipe")
    entries = [e for e in listing.get("tree", []) if e.get("type") in {"blob", "commit"}]
    if not entries:
        notice("github", f"{repo}@{branch} already has no tracked files")
        return {"branch": branch, "files_removed": 0}
    if any(e.get("type") == "commit" for e in entries):
        raise BackendError("Repo contains submodules; refusing automatic branch cleanup")
    # Git's SHA-1 hash for an empty tree. Reusing it avoids POST /git/trees,
    # which can fail for large/nested deletion payloads. This creates a normal
    # non-force commit with the previous HEAD as parent, preserving history.
    # GitHub's ordinary repositories currently use SHA-1 Git object IDs.
    empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    if len(current_tree) != 40:
        raise BackendError("Unsupported repository Git object format; refusing automatic cleanup")
    try:
        new_commit = api("POST", prefix + "/git/commits", token,
                         {"message": "Reset HILTester starter repository files",
                          "tree": empty_tree, "parents": [parent]})["sha"]
    except BackendError as exc:
        raise BackendError(
            "Could not create the empty-tree cleanup commit. The GitHub API may "
            "not accept the empty-tree object for this repository, or a repository "
            "policy may block the operation. No branch ref was updated. "
            f"Details: {exc}"
        ) from exc
    api("PATCH", prefix + "/git/refs/heads/" + quote(branch, safe=""), token,
        {"sha": new_commit, "force": False})
    notice("github", f"Removed {len(entries)} tracked files on {repo}@{branch} in one commit")
    return {"branch": branch, "files_removed": len(entries), "commit": new_commit}


def parser():
    p = argparse.ArgumentParser(description="Selectively deprovision HILTester while keeping Pi OS installed")
    p.add_argument("--project", default="demo", help="local project state name (default: demo)")
    p.add_argument("--repo", help="GitHub owner/repo; otherwise use saved deployment/user configuration")
    p.add_argument("--branch", help="branch to clear for --github-clean (default: remote default)")
    p.add_argument("--runner-name", help="runner name; defaults to last deployed name")
    p.add_argument("--workspace", help="Pi checkout directory; defaults to saved deployment workspace")
    p.add_argument("--token-env", default="GITHUB_TOKEN")
    p.add_argument("--prompt-token", action="store_true", help="ignore saved environment token and prompt for a fresh GitHub PAT")
    p.add_argument("--stop", action="store_true", help="stop/remove HILTester runner Docker container on Pi")
    p.add_argument("--unregister", action="store_true", help="remove named runner registration from GitHub")
    p.add_argument("--remove-files", action="store_true", help="remove matching repo checkout and runtime config on Pi")
    p.add_argument("--uninstall-docker", action="store_true", help="purge Docker packages, images, volumes and caches (refuses other containers)")
    p.add_argument("--uninstall-tailscale", action="store_true", help="remove Tailscale after other cleanup; may cut SSH connectivity")
    p.add_argument("--remove-ssh-key", action="store_true", help="remove only this project's SSH public key from Pi authorized_keys")
    p.add_argument("--total-deprovision", action="store_true", help="Pi cleanup + Docker + Tailscale + HILTester SSH key; keeps OS and GitHub repo")
    p.add_argument("--pi-all", action="store_true", help="stop, unregister, and remove Pi deployment files")
    p.add_argument("--all", action="store_true", help="alias of --pi-all; excludes GitHub repository cleaning")
    p.add_argument("--github-clean", action="store_true", help="DELETE ALL TRACKED FILES on selected GitHub branch by new commit")
    p.add_argument("--reset-progress", action="store_true", help="reset local run_all progress, leave SSH and network states")
    p.add_argument("--dry-run", action="store_true", help="show plan without changing Pi or GitHub")
    p.add_argument("--yes", action="store_true", help="skip confirmation for normal Pi cleanup only; not full/uninstall/GitHub wipe")
    return p


def main():
    args = parser().parse_args()
    deployment = load_state(args.project, "deploy_state")
    config = load_state(args.project, "user_config")
    gh = load_state(args.project, "github_state")
    net = load_state(args.project, "network_state")
    repo = args.repo or deployment.get("repo") or config.get("repo") or gh.get("repo_label")
    if repo and repo.startswith("https://github.com/"):
        repo = repo.removeprefix("https://github.com/").removesuffix(".git")
    if not repo or not REPO_PATTERN.fullmatch(repo) or ".." in repo:
        raise BackendError("Provide --repo owner/name or configure a GitHub project first")
    name = args.runner_name or deployment.get("runner_name") or "hil-" + re.sub(r"[^A-Za-z0-9_-]", "-", args.project)[:35] + "-pi5"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise BackendError("Invalid runner name")
    workspace = args.workspace or deployment.get("workspace") or "/opt/hiltester/" + repo.split("/")[1]
    if workspace != "/opt/hiltester/" + repo.split("/")[1] and not args.workspace:
        raise BackendError("Unexpected saved workspace; provide explicit --workspace")
    if not re.fullmatch(r"/opt/hiltester/[A-Za-z0-9_.-]+", workspace) or workspace.endswith("/.") or ".." in workspace:
        raise BackendError("Only direct /opt/hiltester/<repo-name> workspaces can be cleaned")
    stop = args.stop or args.pi_all or args.all or args.total_deprovision or args.uninstall_docker
    unregister_runner = args.unregister or args.pi_all or args.all or args.total_deprovision
    files = args.remove_files or args.pi_all or args.all or args.total_deprovision
    uninstall_docker = args.uninstall_docker or args.total_deprovision
    uninstall_tailscale = args.uninstall_tailscale or args.total_deprovision
    remove_ssh = args.remove_ssh_key or args.total_deprovision
    if files and not stop:
        raise BackendError("Use --stop alongside --remove-files to avoid deleting files beneath a live container")
    if not any((stop, unregister_runner, files, uninstall_docker, uninstall_tailscale, remove_ssh, args.github_clean, args.reset_progress)):
        raise BackendError("Select --stop, --unregister, --remove-files, --pi-all, --github-clean, or --reset-progress")
    plan = {"project": args.project, "repo": repo, "runner": name, "workspace": workspace,
            "pi": net.get("tailscale_ip", "unknown"), "stop": stop,
            "unregister": unregister_runner, "remove_files": files,
            "uninstall_docker": uninstall_docker, "uninstall_tailscale": uninstall_tailscale,
            "remove_ssh_key": remove_ssh, "total_deprovision": args.total_deprovision,
            "github_clean": args.github_clean, "reset_progress": args.reset_progress,
            "preserve": ["Raspberry Pi OS", "boot drive partitions", "GitHub repo/history unless --github-clean", "system SSH daemon and user accounts"],
            "warning": "Tailscale/SSH-key cleanup is scheduled LAST and may disconnect this Pi" if (uninstall_tailscale or remove_ssh) else None}
    print(json.dumps(plan, indent=2))
    if args.dry_run:
        return 0
    if not args.yes or args.github_clean or args.total_deprovision or uninstall_tailscale or remove_ssh or uninstall_docker:
        expected = f"WIPE {repo}" if args.github_clean else (f"DEPROVISION {args.project}" if (args.total_deprovision or uninstall_tailscale or remove_ssh or uninstall_docker) else f"CLEAN {args.project}")
        typed = input(f"Type '{expected}' to proceed: ").strip()
        if typed != expected:
            notice("cancel", "No changes made")
            return 1
    # Preflight *all* credentials before stopping any container or deleting files.
    # Never write the PAT to local state or display it in logs.
    token = ""
    if unregister_runner or args.github_clean:
        token = "" if args.prompt_token else os.environ.get(args.token_env, "").strip()
        if not token:
            token = getpass.getpass(f"{args.token_env} (hidden): ").strip()
        if not token:
            raise BackendError("GitHub token required; nothing was cleaned")
        github_preflight(repo, token, runner_access=unregister_runner, github_clean=args.github_clean)

    if stop or files or uninstall_docker or uninstall_tailscale or remove_ssh:
        if net.get("status") != "confirmed" or not net.get("tailscale_ip"):
            raise BackendError("Pi network state not confirmed; no Pi changes made")
    # Stop first, then unregister, and only then delete deployment files.
    # This leaves recoverable files on the Pi if GitHub deletion fails.
    if stop:
        notice("pi", "Stopping targeted runtime over SSH")
        invoke_pi(args.project, net, pi_script(repo, workspace, name, True, False))
    if unregister_runner:
        unregister(repo, token, name)
    if files:
        notice("pi", "Removing matching deployment files over SSH")
        invoke_pi(args.project, net, pi_script(repo, workspace, name, False, True))
    if args.github_clean:
        clear_repo(repo, token, args.branch)
    if uninstall_docker:
        notice("docker", "Uninstalling Docker after runner and repository cleanup")
        invoke_pi(args.project, net, docker_uninstall_script(repo))
    if uninstall_tailscale or remove_ssh:
        notice("disconnect", "Scheduling last-stage network/SSH-key cleanup on Pi")
        invoke_pi(args.project, net, disconnect_script(uninstall_tailscale, remove_ssh, read_public_key(args.project)))
        notice("disconnect", "Scheduled only; completion cannot be verified after losing access. Check /var/log/hiltester-final-clean.log locally on the Pi.")
    if args.reset_progress or args.total_deprovision:
        progress = load_state(args.project, "run_all_state")
        save_state(args.project, "run_all_state", {"status": "reset", "previous_mode": progress.get("mode")})
        notice("local", "Reset run_all progress; retained SSH and network settings")
    if args.total_deprovision:
        save_state(args.project, "network_state", {"status": "deprovisioned", "note": "Tailscale and project SSH access removed"})
        save_state(args.project, "deploy_state", {"status": "deprovisioned", "repo": repo})
    save_state(args.project, "clean_state", {**plan, "status": "scheduled_disconnect" if (uninstall_tailscale or remove_ssh) else "success"})
    notice("done", "Selected cleanup completed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BackendError, OSError, KeyError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        sys.exit(2)
