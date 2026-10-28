"""HILTester guided orchestration. Run from the PC, not the Raspberry Pi.

Companion backend modules must be in this directory. Existing backend CLIs are
launched unchanged to preserve their state format and behavior.
"""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from common import BackendError, load_state, save_state
from user_arguments import MODES, ask, confirm, ensure_secret, parser, project_inputs, setup_existing, preflight_choices

ROOT = Path(__file__).resolve().parent
API = "https://api.github.com"


def say(message: str) -> None:
    print(message, flush=True)


def step(project: str, name: str, status: str, **details) -> None:
    state = load_state(project, "run_all_state")
    state.setdefault("steps", {})[name] = {"status": status, **details}
    state["last_step"] = name
    save_state(project, "run_all_state", state)


def run_backend(module: str, args: list[str], *, verbose: bool, project: str) -> dict:
    cmd = [sys.executable, str(ROOT / f"{module}.py"), "--project", project, *args]
    if verbose:
        say("  $ " + " ".join(cmd[:2] + ["--project", project, *args]))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    while True:
        try:
            raw_out, raw_err = proc.communicate(timeout=15)
            break
        except subprocess.TimeoutExpired:
            say(f"  [{module}] still working...")
    out = raw_out.decode("utf-8", errors="replace").strip()
    err = raw_err.decode("utf-8", errors="replace").strip()
    if verbose and err:
        print(err, file=sys.stderr)
    try:
        payload = json.loads(out)
    except ValueError:
        payload = {}
    if proc.returncode:
        detail = payload.get("error") or err[-1500:] or out[-1500:]
        raise BackendError(f"{module} failed: {detail}")
    if verbose and out:
        say(out)
    return payload


def select_disk(args, project: str) -> str:
    """Preflight-only interactive selection. Never prompt after execution begins."""
    from boot_provision import detect_usb_disks
    while True:
        disks = detect_usb_disks()
        if not disks:
            say("[Setup] Insert the OS destination USB drive into this PC.")
            input("Press Enter to scan disks...")
            continue
        say("\nRemovable disks (SELECTED DRIVE WILL BE ERASED):")
        for i, d in enumerate(disks, 1):
            say(f"  {i}. {d.get('name') or 'USB disk'}  {d.get('size_bytes', 0)/(1024**3):.1f} GiB  [{d['device']}]")
        if args.device:
            selected = next((d for d in disks if d['device'] == args.device), None)
            if not selected:
                raise BackendError("--device is not among detected removable USB disks")
        else:
            choice = ask(None, "Select disk number (or R to rescan)")
            if choice.lower() == 'r':
                continue
            if not choice.isdigit() or not 1 <= int(choice) <= len(disks):
                say("Invalid number.")
                continue
            selected = disks[int(choice)-1]
        if input(f"Type ERASE to authorize flashing {selected['device']}: ").strip() != "ERASE":
            raise BackendError("flash cancelled (ERASE not entered)")
        return selected['device']


def do_flash(args, project: str, hostname: str, username: str) -> None:
    device = args.device
    if not device:
        raise BackendError("USB device was not selected during preflight")
    opts = ["--device", device, "--hostname", hostname, "--username", username,
            "--wifi-country", args.wifi_country, "--apply", "--yes-really-flash"]
    if args.wifi_ssid:
        opts += ["--wifi-ssid", args.wifi_ssid]
    if args.os_image:
        opts += ["--image", args.os_image]
    say("[USB] Flashing Raspberry Pi OS. Raspberry Pi Imager handles download/write progress.")
    step(project, "usb", "running", device=device)
    run_backend("boot_provision", opts, verbose=args.verbose, project=project)
    step(project, "usb", "complete", device=device)
    say("[USB] Flash complete. Safely eject the drive, plug it into the Pi, and power on.")
    input("Press Enter after powering on the Pi...")


def api_json(path: str, token: str, method: str = "GET", data: dict | None = None):
    req = urllib.request.Request(API + path, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "HILTester-run-all/1",
                 **({"Content-Type": "application/json"} if data is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        raise BackendError(f"GitHub API {method} {path} returned HTTP {exc.code}") from exc


def do_github(args, project: str, repo: str) -> None:
    ensure_secret("GITHUB_TOKEN", "GitHub API token")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise BackendError("GITHUB_TOKEN required to initialize the starter repository")
    archive = Path(args.archive).expanduser().resolve() if args.archive else ROOT.parent.parent / "starter.tar.gz"
    if not archive.is_file():
        raise BackendError(f"starter.tar.gz not found: {archive} (pass --archive)")
    say(f"[GitHub] Preparing starter archive for {repo}...")
    opts = ["--repo", repo, "--archive", str(archive), "--init-starter"]
    if args.branch:
        opts += ["--branch", args.branch]
    run_backend("github_backend", opts + ["--dry-run"], verbose=args.verbose, project=project)
    step(project, "github", "running", repo=repo)
    run_backend("github_backend", opts, verbose=args.verbose, project=project)
    # Verify and persist github_state for the existing companion backends.
    check = ["--repo", repo]
    if args.branch:
        check += ["--branch", args.branch]
    run_backend("github_backend", check, verbose=args.verbose, project=project)
    step(project, "github", "complete", repo=repo)
    say("[GitHub] Starter initialized and repository verified.")


def network(args, project: str, hostname: str, username: str, wait: bool) -> None:
    say("[Network] Checking Tailscale and project-key SSH...")
    deadline = time.monotonic() + max(1, args.wait_minutes) * 60
    while True:
        opts = ["--hostname", hostname, "--ssh-user", username]
        if args.ip:
            opts += ["--ip", args.ip]
        try:
            state = run_backend("network_tailscale", opts, verbose=args.verbose, project=project)
            if state.get("status") == "confirmed" and state.get("ssh", {}).get("ok"):
                say(f"[Network] Pi online: {state.get('tailscale_ip')}, SSH confirmed.")
                step(project, "network", "complete", ip=state.get("tailscale_ip"))
                return
        except BackendError as exc:
            if not wait:
                raise
            if args.verbose:
                say(str(exc))
        if not wait or time.monotonic() >= deadline:
            raise BackendError("Pi did not become reachable over Tailscale + SSH")
        say("[Network] Not ready; checking again in 15 seconds...")
        time.sleep(15)


def hardware(args, project: str) -> None:
    """Manifest-driven scan, without interactive selection after boot."""
    from hardware_profile import scan_remote
    from hardware_manifest import load_manifest, verify_usb, scan_boot_storage
    manifest = load_manifest(args.hardware_manifest)
    net = load_state(project, "network_state")
    say("[Hardware] Checking Pi USB hardware against hardware_manifest.json...")
    devices = scan_remote(project, net["tailscale_ip"], net["ssh_user"], int(net.get("ssh_port", 22)))
    report = verify_usb(manifest, devices)
    # Root disk verification is non-destructive; require matching SanDisk when configured.
    storage = scan_boot_storage(project, net, manifest)
    report["boot_storage"] = storage
    for device in report["devices"]:
        say(f"  {'OK' if device['present'] else 'MISSING'}: {device['name']}" +
            ("" if device['required'] else " (optional)"))
    say(f"  {'OK' if storage['present'] else 'MISSING'}: USB boot drive (model contains: {storage['expected_model_contains']})")
    missing = list(report["missing_required"])
    if not storage["present"] and manifest.get("boot_storage", {}).get("required", False):
        missing.append("boot_storage")
    state = {"step": "hardware", "status": "confirmed" if not missing else "missing_devices",
             "source": net["tailscale_ip"], "manifest": str(args.hardware_manifest or "hardware_manifest.json"),
             "verification": report, "scanned_devices": devices, "missing": missing}
    save_state(project, "hardware_profile", state)
    if missing:
        step(project, "hardware", "failed", missing=missing)
        raise BackendError("Required hardware not detected: " + ", ".join(missing) +
                           ". Check Pi USB connections or customize --hardware-manifest.")
    step(project, "hardware", "complete", verified=[d["id"] for d in report["devices"] if d["present"]])
    say("[Hardware] Manifest verification complete.")


def do_deploy(args, project: str, repo: str) -> None:
    ensure_secret("GITHUB_TOKEN", "GitHub token for runner registration")
    if not os.environ.get("GITHUB_TOKEN"):
        raise BackendError("GITHUB_TOKEN required for runner registration")
    opts = ["--repo", repo]
    if args.branch:
        opts += ["--branch", args.branch]
    release_root = ROOT.parent.parent
    manifest_path = release_root / "release.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        image_info = manifest["runner_image"]
        archive = (release_root / image_info["archive"]).resolve()
        if not archive.is_relative_to(release_root.resolve()) or not archive.is_file():
            raise BackendError("missing or unsafe bundled release image archive")
        opts += ["--image", image_info["reference"], "--image-archive", str(archive),
                 "--image-sha256", image_info["sha256"]]
        say(f"[Release] Using bundled image: {image_info['reference']}")
    elif args.image:
        opts += ["--image", args.image]
    step(project, "deploy", "running", repo=repo)
    say("[Deploy] Preparing Docker, syncing repository on Pi and registering runner...")
    result = run_backend("deploy_backend", opts, verbose=args.verbose, project=project)
    runner = result.get("runner") or {}
    if result.get("status") != "success" or runner.get("status") != "online":
        raise BackendError("deployment did not confirm runner online")
    step(project, "deploy", "complete", runner=runner.get("name"))
    say(f"[Deploy] GitHub runner online: {runner.get('name')}.")


def do_test(args, project: str, repo: str) -> None:
    """Dispatch firmware test; once on failure, use GitHub's Re-run all jobs API.

    A rerun is a new attempt of the same workflow run. It can reflash hardware,
    so it is never requested after success and is limited to one additional attempt.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise BackendError("GITHUB_TOKEN is required for workflow dispatch/report")
    branch = args.branch or load_state(project, "deploy_state").get("branch") or "main"
    workflow = quote(args.workflow, safe="")
    prefix = f"/repos/{repo}"
    current = api_json(prefix + f"/actions/workflows/{workflow}/runs?per_page=30", token)
    known = {run["id"] for run in current.get("workflow_runs", [])}
    say(f"[Test] Triggering {args.workflow} on {branch}. Hardware flashing may occur.")
    api_json(prefix + f"/actions/workflows/{workflow}/dispatches", token, "POST", {"ref": branch})
    step(project, "test", "running", workflow=args.workflow)
    run_id = None
    deadline = time.monotonic() + max(1, args.wait_minutes) * 60
    retries = 0
    waiting_for_attempt = None

    while time.monotonic() < deadline:
        if run_id is None:
            data = api_json(prefix + f"/actions/workflows/{workflow}/runs?per_page=30", token)
            candidate = next((r for r in data.get("workflow_runs", [])
                              if r.get("event") == "workflow_dispatch"
                              and r.get("head_branch") == branch
                              and r.get("id") not in known), None)
            if candidate:
                run_id = candidate["id"]
                say(f"[Test] Run #{candidate.get('run_number')} started: {candidate.get('html_url')}")

        if run_id is not None:
            run = api_json(prefix + f"/actions/runs/{run_id}", token)
            attempt = int(run.get("run_attempt") or 1)
            if waiting_for_attempt is not None:
                # GitHub may briefly return the previous attempt's completed
                # status immediately after accepting the re-run request.
                if attempt < waiting_for_attempt:
                    time.sleep(15)
                    continue
                waiting_for_attempt = None
                say(f"[Test] Re-run attempt {attempt} started.")

            if run.get("status") == "completed":
                conclusion = run.get("conclusion")
                url = run.get("html_url")
                say(f"[Test] GitHub Actions attempt {attempt}: {conclusion}. {url}")
                if conclusion == "success":
                    step(project, "test", "complete", conclusion=conclusion,
                         url=url, run_id=run_id, attempts=attempt)
                    return

                jobs = api_json(prefix + f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", token)
                for job in jobs.get("jobs", []):
                    for item in job.get("steps", []):
                        if item.get("conclusion") == "failure":
                            say(f"  Failed: {job.get('name')} / {item.get('name')}")

                if conclusion == "failure" and retries == 0:
                    # Same GitHub action as clicking "Re-run all jobs" in the UI.
                    # The initial preflight already authorized hardware flashing.
                    say("[Test] First attempt failed; requesting Re-run all jobs once.")
                    try:
                        api_json(prefix + f"/actions/runs/{run_id}/rerun", token, "POST")
                    except BackendError as exc:
                        step(project, "test", "failed", conclusion=conclusion,
                             url=url, run_id=run_id, rerun_error=str(exc))
                        raise BackendError(f"Re-run all jobs request failed: {exc}") from exc
                    retries = 1
                    waiting_for_attempt = attempt + 1
                    # Each attempt gets the usual wait budget; do not issue a
                    # second re-run even if this next attempt also fails.
                    deadline = time.monotonic() + max(1, args.wait_minutes) * 60
                    step(project, "test", "retrying", url=url, run_id=run_id,
                         expected_attempt=waiting_for_attempt)
                    time.sleep(15)
                    continue

                step(project, "test", "failed", conclusion=conclusion,
                     url=url, run_id=run_id, attempts=attempt)
                raise BackendError("GitHub Actions test failed; inspect linked run logs")
        time.sleep(15)

    step(project, "test", "timed_out", run_id=run_id,
         message="Run may still be queued or executing; no rerun requested")
    raise BackendError("GitHub Actions wait timed out; run may still be active. Check Actions before retrying")


def clone_local(args, project: str, repo: str) -> None:
    if args.no_clone:
        return
    dest = Path(args.clone_to).expanduser().resolve() if args.clone_to else Path.cwd() / repo.split("/")[1]
    if dest.exists():
        say(f"[Local] Working folder already exists: {dest} (clone skipped)")
        return
    if not shutil.which("git"):
        say("[Local] Git CLI unavailable; clone skipped")
        return
    result = subprocess.run(["git", "clone", f"https://github.com/{repo}.git", str(dest)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        say("[Local] Clone failed (private repo may require Git credential manager).")
        return
    step(project, "local_clone", "complete", path=str(dest))
    say(f"[Local] Working copy: {dest}")


def main() -> int:
    args = parser().parse_args()
    mode = args.mode or ask(None, "Mode (usb / github / usb-github / deploy / deploy-test / resume)", "deploy")
    if mode not in MODES:
        raise BackendError("unrecognized mode")
    project = ask(args.project, "Project name", "demo")
    previous = load_state(project, "run_all_state")
    if mode == "resume":
        mode = previous.get("mode") or "deploy"
        say(f"[Resume] Previous workflow: {mode}")
    settings = project_inputs(args, mode, project, previous)
    repo = settings.get("repo")
    hostname = settings.get("hostname")
    username = settings.get("username")
    args.wifi_country = settings.get("wifi_country", "US")
    args.wifi_ssid = settings.get("wifi_ssid", "")
    if not args.branch:
        args.branch = settings.get("branch") or None
    args.existing_pi, args.skip_github_init = setup_existing(args, mode, project)
    need_usb = mode in {"usb", "usb-github", "deploy", "deploy-test"} and not args.existing_pi
    need_gh = mode in {"github", "usb-github", "deploy", "deploy-test"}
    need_pi = mode in {"deploy", "deploy-test"}
    completed = previous.get("steps", {}) if args.mode == "resume" else {}
    if need_usb and completed.get("usb", {}).get("status") != "complete":
        if args.wifi_ssid:
            ensure_secret("HILTESTER_WIFI_PASSWORD", "Wi-Fi password", required=True)
        ensure_secret("TAILSCALE_AUTHKEY", "Tailscale auth key", required=need_pi)
    # All optional decisions are collected before GitHub/USB/SSH side effects.
    preflight_choices(args, mode, project, need_usb=need_usb and completed.get("usb", {}).get("status") != "complete",
                      need_github_init=need_gh and not args.skip_github_init and completed.get("github",{}).get("status") != "complete",
                      need_hardware=need_pi and not args.skip_hardware and completed.get("hardware",{}).get("status") != "complete",
                      need_test=mode == "deploy-test" and completed.get("test",{}).get("status") != "complete")
    if need_usb and completed.get("usb",{}).get("status") != "complete":
        args.device = select_disk(args, project)
    # Verify credentials at preflight, not in the middle of execution.
    if need_gh and (mode in {"deploy", "deploy-test"} or not args.skip_github_init):
        ensure_secret("GITHUB_TOKEN", "GitHub API token", required=True)
    save_state(project, "run_all_state", {"mode": mode, "repo": repo, "hostname": hostname,
              "username": username, "steps": previous.get("steps", {})})
    def should_run(stage):
        if completed.get(stage, {}).get("status") == "complete":
            say(f"[{stage}] Already completed; skipping (resume)")
            return False
        return True
    if need_gh and should_run("github"):
        if args.skip_github_init:
            verify = ["--repo", repo]
            if args.branch:
                verify += ["--branch", args.branch]
            run_backend("github_backend", verify, verbose=args.verbose, project=project)
            step(project, "github", "complete", repo=repo, skipped_init=True)
            say("[GitHub] Existing repository verified (starter upload skipped).")
        else:
            do_github(args, project, repo)
    if need_usb and should_run("usb"):
        do_flash(args, project, hostname, username)
    if need_pi:
        if should_run("network"):
            network(args, project, hostname, username, wait=True)
        if not args.skip_hardware and should_run("hardware"):
            hardware(args, project)
        if should_run("deploy"):
            do_deploy(args, project, repo)
        if mode == "deploy-test" and should_run("test"):
            do_test(args, project, repo)
    if need_gh and not args.no_clone:
        clone_local(args, project, repo)
    say("\nHILTester workflow finished. Run state: ~/.hiltester/projects/<project>/run_all_state.json")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BackendError, ValueError, KeyboardInterrupt) as exc:
        print(f"\n[HILTester] {exc or 'cancelled'}", file=sys.stderr)
        raise SystemExit(2)
