"""Interactive inputs and non-secret project preferences for HILTester.

Passwords and API tokens live only in process environment for the current run.
"""
from __future__ import annotations

import argparse
import getpass
import os
from dataclasses import dataclass

from common import load_state, save_state

MODES = ("usb", "github", "usb-github", "deploy", "deploy-test", "resume")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="HILTester guided Pi 5 / GitHub setup")
    p.add_argument("mode", nargs="?", choices=MODES, help="operation; prompted if omitted")
    p.add_argument("--project", help="local project name")
    p.add_argument("--repo", help="GitHub owner/repo")
    p.add_argument("--branch", help="branch, otherwise GitHub default")
    p.add_argument("--hostname", help="Pi hostname")
    p.add_argument("--username", help="Pi/SSH username")
    p.add_argument("--wifi-ssid", help="Wi-Fi name, or empty string for Ethernet")
    p.add_argument("--wifi-country", default=None)
    p.add_argument("--archive", help="starter.tar.gz path")
    p.add_argument("--image", help="runner Docker image override")
    p.add_argument("--ip", help="known Pi Tailscale IP")
    p.add_argument("--device", help="USB device; otherwise select from menu")
    p.add_argument("--os-image", help="Pi OS image override")
    p.add_argument("--workflow", default="main.yml")
    p.add_argument("--wait-minutes", type=int, default=15)
    p.add_argument("--skip-hardware", action="store_true")
    p.add_argument("--hardware-manifest", help="JSON description of expected Pi storage and USB hardware")
    p.add_argument("--existing-pi", action="store_true")
    p.add_argument("--skip-github-init", action="store_true")
    p.add_argument("--no-clone", action="store_true")
    p.add_argument("--clone-to")
    p.add_argument("--assume-yes", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def ask(value: str | None, prompt: str, default: str | None = None, *, required: bool = True) -> str:
    if value is not None:
        return value
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip() or (default or "")
    if required and not answer:
        raise ValueError(f"{prompt} is required")
    return answer


def ensure_secret(env_var: str, prompt: str, *, required: bool = False) -> bool:
    if os.environ.get(env_var):
        print(f"[Credentials] Using {env_var} from this terminal session.")
        return True
    secret = getpass.getpass(f"{prompt} (hidden){'' if required else ', Enter to skip'}: ").strip()
    if secret:
        os.environ[env_var] = secret
    if required and not os.environ.get(env_var):
        raise ValueError(f"{env_var} is required for this operation")
    return bool(os.environ.get(env_var))


def confirm(message: str, *, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    answer = input(f"{message} [{suffix}]: ").strip().lower()
    if not answer:
        return default
    if answer not in {"y", "yes", "n", "no"}:
        raise ValueError("Please answer yes or no")
    return answer in {"y", "yes"}


def project_inputs(args, mode: str, project: str, previous: dict) -> dict:
    """Collect reusable non-secret configuration, honoring command-line overrides."""
    saved = load_state(project, "user_config")
    need_repo = mode in ("github", "usb-github", "deploy", "deploy-test")
    need_pi = mode in ("usb", "usb-github", "deploy", "deploy-test")
    settings = {k: v for k, v in saved.items() if k in {"repo", "branch", "hostname", "username", "wifi_country", "wifi_ssid"}}
    if need_repo:
        settings["repo"] = ask(args.repo, "GitHub owner/repository", saved.get("repo") or previous.get("repo"))
        settings["branch"] = ask(args.branch, "Branch (blank = repository default)", saved.get("branch", ""), required=False)
    if need_pi:
        settings["hostname"] = ask(args.hostname, "Pi hostname", saved.get("hostname") or previous.get("hostname") or "raspberrypi")
        settings["username"] = ask(args.username, "Pi Linux / SSH username", saved.get("username") or previous.get("username") or "hiltester")
        settings["wifi_country"] = args.wifi_country or saved.get("wifi_country") or "US"
    if mode in ("usb", "usb-github", "deploy", "deploy-test"):
        settings["wifi_ssid"] = ask(args.wifi_ssid, "Wi-Fi SSID (blank for Ethernet)", saved.get("wifi_ssid", ""), required=False)
    # Explicit allowlist: never persist passwords, tokens, private keys or environment.
    allowed = {"repo", "branch", "hostname", "username", "wifi_country", "wifi_ssid"}
    save_state(project, "user_config", {key: settings[key] for key in allowed if key in settings})
    return settings


def setup_existing(args, mode: str, project: str) -> tuple[bool, bool]:
    """Return (existing_pi, existing_github) without silently assuming a bare Pi."""
    has_pi = mode in ("deploy", "deploy-test")
    has_repo = mode in ("github", "usb-github", "deploy", "deploy-test")
    pi_state = load_state(project, "network_state")
    starter_state = load_state(project, "starter_state")
    existing_pi = args.existing_pi
    existing_repo = args.skip_github_init
    if has_pi and not existing_pi:
        default = pi_state.get("status") == "confirmed"
        existing_pi = confirm("Pi already has an OS, Tailscale and SSH? (skip USB flashing)", default=default)
    if has_repo and not existing_repo:
        default = starter_state.get("status") == "uploaded" or bool(load_state(project, "github_state").get("repo"))
        existing_repo = confirm("GitHub repository already populated? (skip starter upload)", default=default)
    return existing_pi, existing_repo


def preflight_choices(args, mode: str, project: str, *, need_usb: bool,
                      need_github_init: bool, need_hardware: bool, need_test: bool) -> None:
    """All optional configuration and approvals before the first deployment step."""
    print("\n========== HILTester setup decisions ==========", flush=True)
    if need_github_init and not args.assume_yes:
        if not confirm("Upload starter files to the selected GitHub repository?", default=True):
            raise ValueError("GitHub initialization cancelled")
    if need_hardware:
        from hardware_manifest import load_manifest, DEFAULT_MANIFEST
        manifest = load_manifest(args.hardware_manifest)
        print(f"[Hardware] Using manifest: {args.hardware_manifest or DEFAULT_MANIFEST}")
        for device in manifest["usb_hardware"]:
            required = "required" if device.get("required", True) else "optional"
            print(f"  - {device.get('name', device['id'])}: {device['usb_vid']}:{'/'.join(device['usb_pids'])} ({required})")
        print("[Hardware] Device identities will be verified automatically after Pi boot.")
    if need_test and not args.assume_yes:
        if not confirm("Allow triggering the GitHub Actions workflow (may flash MCUs)?"):
            raise ValueError("Actions test not authorized")
    if mode in ("github","usb-github","deploy","deploy-test") and not args.no_clone:
        if not confirm("Create a separate working clone on this PC after completion?", default=False):
            args.no_clone = True
        else:
            args.clone_to = ask(args.clone_to, "Local clone destination (blank = working directory/repo)", required=False) or None
    print("[Setup] Decisions collected. Only physical drive/board readiness will pause execution.", flush=True)
