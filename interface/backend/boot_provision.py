from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import tempfile
import urllib.request
from pathlib import Path

from common import BackendError, ensure_project_ssh_key, print_json, run, save_state

OFFICIAL_IMAGER_REPO = "https://downloads.raspberrypi.com/os_list_imagingutility_v4.json"


def _windows_disks() -> list[dict]:
    ps = [
        "powershell", "-NoProfile", "-Command",
        "Get-Disk | Where-Object {$_.BusType -eq 'USB'} | "
        "Select-Object Number,FriendlyName,SerialNumber,Size,OperationalStatus | ConvertTo-Json -Compress",
    ]
    result = run(ps, timeout=30, check=True)
    if not result.stdout:
        return []
    raw = json.loads(result.stdout)
    if isinstance(raw, dict):
        raw = [raw]
    return [{
        "device": rf"\\.\PhysicalDrive{d['Number']}",
        "name": d.get("FriendlyName"),
        "serial": d.get("SerialNumber"),
        "size_bytes": int(d.get("Size") or 0),
        "status": d.get("OperationalStatus"),
    } for d in raw]


def _linux_disks() -> list[dict]:
    result = run(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,MODEL,SERIAL,TRAN,TYPE,RM"], timeout=30, check=True)
    raw = json.loads(result.stdout)
    return [{
        "device": d.get("path"),
        "name": (d.get("model") or d.get("name") or "").strip(),
        "serial": (d.get("serial") or "").strip() or None,
        "size_bytes": int(d.get("size") or 0),
        "status": "online",
    } for d in raw.get("blockdevices", []) if d.get("type") == "disk" and (d.get("tran") == "usb" or d.get("rm") == 1)]


def _mac_disks() -> list[dict]:
    import plistlib
    result = run(["diskutil", "list", "-plist", "external", "physical"], timeout=30, check=True)
    raw = plistlib.loads(result.stdout.encode())
    disks = []
    for whole in raw.get("WholeDisks", []):
        info = run(["diskutil", "info", "-plist", f"/dev/{whole}"], timeout=30, check=True)
        item = plistlib.loads(info.stdout.encode())
        disks.append({
            "device": f"/dev/{whole}",
            "name": item.get("MediaName"),
            "serial": item.get("DiskUUID"),
            "size_bytes": int(item.get("TotalSize") or 0),
            "status": "online",
        })
    return disks


def detect_usb_disks() -> list[dict]:
    system = platform.system()
    if system == "Windows":
        return _windows_disks()
    if system == "Linux":
        return _linux_disks()
    if system == "Darwin":
        return _mac_disks()
    raise BackendError(f"unsupported host OS: {system}")


def _sq(value: str) -> str:
    return shlex.quote(value)


def make_first_run_script(
    hostname: str,
    username: str,
    ssh_key: str,
    wifi_ssid: str | None,
    wifi_password: str | None,
    wifi_country: str,
    tailscale_auth_key: str | None,
) -> str:
    """Generate a Raspberry Pi OS first-boot script for Imager --first-run-script."""
    lines = [
        "#!/bin/bash",
        "set -euo pipefail",
        "exec > >(tee -a /var/log/hiltester-firstboot.log) 2>&1",
        "echo '[HILTester] first-boot provisioning started'",
        f"hostnamectl set-hostname {_sq(hostname)}",
        f"if ! id -u {_sq(username)} >/dev/null 2>&1; then useradd -m -s /bin/bash {_sq(username)}; fi",
        f"for g in adm sudo dialout plugdev video audio netdev gpio i2c spi; do getent group \"$g\" >/dev/null 2>&1 && usermod -aG \"$g\" {_sq(username)} || true; done",
        f"install -d -m 700 -o {_sq(username)} -g {_sq(username)} /home/{_sq(username)}/.ssh",
        f"printf '%s\\n' {_sq(ssh_key)} > /home/{_sq(username)}/.ssh/authorized_keys",
        f"chown {_sq(username)}:{_sq(username)} /home/{_sq(username)}/.ssh/authorized_keys",
        f"chmod 600 /home/{_sq(username)}/.ssh/authorized_keys",
        f"printf '%s\\n' {_sq(username + ' ALL=(ALL) NOPASSWD:ALL')} > /etc/sudoers.d/010-hiltester",
        "chmod 0440 /etc/sudoers.d/010-hiltester",
        "systemctl enable ssh || true",
        "systemctl start ssh || true",
        f"command -v raspi-config >/dev/null 2>&1 && raspi-config nonint do_wifi_country {_sq(wifi_country)} || true",
        "rfkill unblock wifi || true",
    ]

    if wifi_ssid and wifi_password:
        lines += [
            "if command -v nmcli >/dev/null 2>&1; then",
            "  nmcli radio wifi on || true",
            "  nmcli connection delete hiltester >/dev/null 2>&1 || true",
            f"  nmcli connection add type wifi ifname wlan0 con-name hiltester ssid {_sq(wifi_ssid)}",
            f"  nmcli connection modify hiltester wifi-sec.key-mgmt wpa-psk wifi-sec.psk {_sq(wifi_password)} ipv4.method auto ipv6.method auto connection.autoconnect yes",
            "  nmcli connection up hiltester || true",
            "fi",
        ]

    if tailscale_auth_key:
        lines += [
            "timedatectl set-ntp true || true",
            "for attempt in $(seq 1 60); do",
            '  if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ]; then break; fi',
            '  echo "[HILTester] waiting for system time synchronization ($attempt/60)"',
            "  sleep 2",
            "done",
            "for attempt in $(seq 1 30); do",
            "  if curl -fsS --connect-timeout 5 https://tailscale.com/install.sh -o /tmp/tailscale-install.sh; then break; fi",
            "  echo \"[HILTester] waiting for network before Tailscale install ($attempt/30)\"",
            "  sleep 5",
            "done",
            "test -s /tmp/tailscale-install.sh",
            "sh /tmp/tailscale-install.sh",
            "systemctl enable --now tailscaled",
            "for attempt in $(seq 1 20); do systemctl is-active --quiet tailscaled && break; sleep 2; done",
            f"tailscale up --auth-key={_sq(tailscale_auth_key)} --hostname={_sq(hostname)} --ssh=false",
            "rm -f /tmp/tailscale-install.sh",
        ]

    lines += [
        "echo '[HILTester] first-boot provisioning complete'",
        "rm -f /boot/firstrun.sh /boot/firmware/firstrun.sh 2>/dev/null || true",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


def make_cloud_init(
    hostname: str,
    username: str,
    ssh_key: str,
    wifi_ssid: str | None,
    wifi_password: str | None,
    wifi_country: str,
    tailscale_auth_key: str | None,
) -> tuple[str, str]:
    """Generate cloud-init user-data + network-config for newer Raspberry Pi OS images."""
    runcmd = [
        "systemctl enable --now ssh",
        f"command -v raspi-config >/dev/null 2>&1 && raspi-config nonint do_wifi_country {_sq(wifi_country)} || true",
        "rfkill unblock wifi || true",
    ]
    if tailscale_auth_key:
        runcmd += [
            "timedatectl set-ntp true || true",
            'for i in $(seq 1 60); do [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ] && break; sleep 2; done',
            "for i in $(seq 1 30); do curl -fsSL --connect-timeout 5 https://tailscale.com/install.sh -o /tmp/tailscale-install.sh && break; sleep 5; done",
            "test -s /tmp/tailscale-install.sh && sh /tmp/tailscale-install.sh",
            "systemctl enable --now tailscaled",
            f"tailscale up --auth-key={_sq(tailscale_auth_key)} --hostname={_sq(hostname)} --ssh=false",
            "rm -f /tmp/tailscale-install.sh",
        ]

    user_data = [
        "#cloud-config",
        "manage_resolv_conf: false",
        f"hostname: {json.dumps(hostname)}",
        "manage_etc_hosts: true",
        "ssh_pwauth: false",
        "user:",
        f"  name: {json.dumps(username)}",
        "  shell: /bin/bash",
        "  lock_passwd: true",
        "  sudo: ALL=(ALL) NOPASSWD:ALL",
        "  ssh_authorized_keys:",
        f"    - {json.dumps(ssh_key)}",
        "runcmd:",
    ]
    for command in runcmd:
        user_data.append(f"  - [ sh, -c, {json.dumps(command)} ]")

    network = ["version: 2"]
    if wifi_ssid and wifi_password:
        network += [
            "wifis:",
            "  wlan0:",
            "    dhcp4: true",
            "    optional: true",
            "    access-points:",
            f"      {json.dumps(wifi_ssid)}:",
            f"        password: {json.dumps(wifi_password)}",
        ]
    else:
        network += ["ethernets:", "  eth0:", "    dhcp4: true", "    optional: true"]
    return "\n".join(user_data) + "\n", "\n".join(network) + "\n"


def _auto_provisioning_format(image: str | None) -> str:
    name = (image or "").lower()
    return "cloudinit" if "trixie" in name else "first-run"



def _flatten_os_items(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        subitems = item.get("subitems")
        if isinstance(subitems, list):
            out.extend(_flatten_os_items(subitems))
        elif item.get("url"):
            out.append(item)
    return out


def fetch_official_os_catalog(repo_url: str = OFFICIAL_IMAGER_REPO) -> dict:
    request = urllib.request.Request(
        repo_url,
        headers={"User-Agent": "HILTester/2.2 (+Raspberry-Pi-Imager-compatible)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except Exception as exc:
        raise BackendError(f"unable to fetch Raspberry Pi Imager OS catalog: {exc}") from exc


def resolve_os(os_choice: str, repo_url: str = OFFICIAL_IMAGER_REPO) -> dict:
    aliases = {"latest": "lite-64", "lite": "lite-64", "lite-64": "lite-64"}
    normalized = aliases.get(os_choice.lower())
    if normalized != "lite-64":
        raise BackendError(f"unsupported OS choice: {os_choice}; supported choices: lite-64, latest")

    catalog = fetch_official_os_catalog(repo_url)
    entries = _flatten_os_items(catalog.get("os_list", []))
    candidates = []
    for item in entries:
        name = str(item.get("name") or "")
        devices = item.get("devices") or []
        if name == "Raspberry Pi OS Lite (64-bit)" and "pi5-64bit" in devices:
            candidates.append(item)

    if not candidates:
        raise BackendError("official catalog did not contain a Pi 5-compatible Raspberry Pi OS Lite (64-bit) image")

    candidates.sort(key=lambda item: str(item.get("release_date") or ""), reverse=True)
    chosen = candidates[0]
    return {
        "choice": normalized,
        "name": chosen.get("name"),
        "description": chosen.get("description"),
        "url": chosen.get("url"),
        "sha256": chosen.get("image_download_sha256"),
        "release_date": chosen.get("release_date"),
        "download_size": chosen.get("image_download_size"),
        "extract_size": chosen.get("extract_size"),
        "init_format": chosen.get("init_format"),
        "devices": chosen.get("devices") or [],
        "repo_url": repo_url,
    }


def _provisioning_format_from_init(init_format: str | None, image: str | None) -> str:
    fmt = (init_format or "").lower()
    if fmt.startswith("cloudinit"):
        return "cloudinit"
    if fmt in {"systemd", "none"}:
        return "first-run"
    return _auto_provisioning_format(image)

def _run_imager(cmd: list[str]):
    """Run Raspberry Pi Imager, requesting UAC elevation on Windows only."""
    if platform.system() != "Windows":
        return run(cmd, timeout=7200, check=True)

    def ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    exe = ps_quote(cmd[0])
    arguments = ", ".join(ps_quote(arg) for arg in cmd[1:])
    command = (
        f"$p = Start-Process -FilePath {exe} "
        f"-ArgumentList @({arguments}) -Verb RunAs -Wait -PassThru; "
        "exit $p.ExitCode"
    )
    return run(["powershell", "-NoProfile", "-Command", command], timeout=7200, check=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Detect USB media and prepare/flash Raspberry Pi OS for one HILTester project.")
    p.add_argument("--project", required=True)
    p.add_argument("--list-disks", action="store_true", help="print removable USB disks and exit")
    p.add_argument("--device", help="explicit destination device, e.g. /dev/sdb or \\.\\PhysicalDrive2")
    p.add_argument("--os", default="lite-64", help="OS alias to resolve automatically; default: lite-64 (latest official Raspberry Pi OS Lite 64-bit for Pi 5)")
    p.add_argument("--resolve-os", action="store_true", help="resolve and print the official OS metadata, then exit without flashing")
    p.add_argument("--repo-url", default=OFFICIAL_IMAGER_REPO, help="Raspberry Pi Imager repository JSON URL")
    p.add_argument("--image", help="advanced override: local image path or image URI; skips automatic OS resolution")
    p.add_argument("--sha256", help="advanced override: expected image SHA256; otherwise taken from the official OS catalog")
    p.add_argument("--hostname", default="rpi-hiltester")
    p.add_argument("--username", default="hiltester")
    p.add_argument("--wifi-ssid")
    p.add_argument("--wifi-country", default="US")
    p.add_argument("--wifi-password-env", default="HILTESTER_WIFI_PASSWORD")
    p.add_argument("--tailscale-auth-key-env", default="TAILSCALE_AUTHKEY")
    p.add_argument(
        "--provisioning-format",
        choices=("auto", "first-run", "cloudinit"),
        default="auto",
        help="first-run for Bookworm-style images, cloudinit for current Trixie-style images; auto infers from image name",
    )
    p.add_argument("--cloud-init-out", help="optional path to write a copy of generated cloud-init user-data")
    p.add_argument("--keep-provisioning-files", action="store_true", help="keep generated first-run/cloud-init files after flashing for debugging")
    p.add_argument("--apply", action="store_true", help="actually write the selected disk")
    p.add_argument("--yes-really-flash", action="store_true", help="second destructive confirmation; required with --apply")
    return p


def main() -> int:
    args = parser().parse_args()
    disks = detect_usb_disks()
    if args.list_disks:
        print_json(disks)
        return 0

    resolved_os = None
    image = args.image
    sha256 = args.sha256
    init_format = None
    if not image:
        resolved_os = resolve_os(args.os, args.repo_url)
        image = resolved_os["url"]
        sha256 = sha256 or resolved_os.get("sha256")
        init_format = resolved_os.get("init_format")

    if args.resolve_os:
        print_json(resolved_os or {
            "choice": "custom-image",
            "url": image,
            "sha256": sha256,
            "repo_url": None,
        })
        return 0

    private_key, public_key = ensure_project_ssh_key(args.project)
    ssh_key = public_key.read_text(encoding="utf-8").strip()
    wifi_password = os.environ.get(args.wifi_password_env) if args.wifi_ssid else None
    ts_key = os.environ.get(args.tailscale_auth_key_env)
    if args.wifi_ssid and not wifi_password:
        raise BackendError(f"Wi-Fi SSID was provided but env var {args.wifi_password_env} is empty")

    provisioning_format = args.provisioning_format
    if provisioning_format == "auto":
        provisioning_format = _provisioning_format_from_init(init_format, image)

    first_run = make_first_run_script(
        args.hostname, args.username, ssh_key, args.wifi_ssid, wifi_password, args.wifi_country, ts_key
    )
    cloud_user, cloud_network = make_cloud_init(
        args.hostname, args.username, ssh_key, args.wifi_ssid, wifi_password, args.wifi_country, ts_key
    )

    debug_cloud_path = None
    if args.cloud_init_out:
        debug_cloud_path = Path(args.cloud_init_out).expanduser().resolve()
        debug_cloud_path.write_text(cloud_user, encoding="utf-8")

    selected = next((d for d in disks if d["device"] == args.device), None) if args.device else None
    state = {
        "step": "boot",
        "status": "configured",
        "host_os": platform.system(),
        "hostname": args.hostname,
        "username": args.username,
        "wifi_country": args.wifi_country,
        "device": args.device,
        "detected_device": selected,
        "os_choice": args.os if not args.image else None,
        "resolved_os": resolved_os,
        "image": image,
        "sha256": sha256,
        "provisioning_format": provisioning_format,
        "ssh_private_key_path": str(private_key),
        "ssh_public_key_path": str(public_key),
        "ssh_key_generated_or_reused": True,
        "wifi_configured": bool(args.wifi_ssid),
        "tailscale_bootstrap_configured": bool(ts_key),
        "cloud_init_debug_path": str(debug_cloud_path) if debug_cloud_path else None,
        "first_boot_provisioning_injected": False,
        "secret_values_persisted": False,
    }

    if not args.apply:
        state["status"] = "ready_to_flash"
        save_state(args.project, "boot_state", state)
        print_json(state)
        return 0

    if not args.yes_really_flash:
        raise BackendError("refusing destructive write: pass both --apply and --yes-really-flash")
    if not args.device or not image:
        raise BackendError("--device and either an automatic OS choice or --image are required with --apply")
    if not selected:
        raise BackendError("selected device is not currently detected as removable USB media")

    imager = shutil.which("rpi-imager") or shutil.which("rpi-imager-cli")
    if not imager and platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Raspberry Pi Ltd\Imager\rpi-imager.exe",
            r"C:\Program Files\Raspberry Pi Imager\rpi-imager.exe",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                imager = candidate
                break
    if not imager:
        raise BackendError("Raspberry Pi Imager CLI was not found on PATH or in the standard Windows install locations")

    tempdir = Path(tempfile.mkdtemp(prefix="hiltester-provision-"))
    first_run_path = tempdir / "firstrun.sh"
    user_data_path = tempdir / "user-data"
    network_config_path = tempdir / "network-config"
    first_run_path.write_text(first_run, encoding="utf-8", newline="\n")
    user_data_path.write_text(cloud_user, encoding="utf-8", newline="\n")
    network_config_path.write_text(cloud_network, encoding="utf-8", newline="\n")
    try:
        try:
            first_run_path.chmod(0o700)
        except OSError:
            pass

        cmd = [imager]
        if Path(imager).stem.lower() == "rpi-imager":
            cmd.append("--cli")
        if sha256:
            cmd += ["--sha256", sha256]

        if provisioning_format == "cloudinit":
            cmd += [
                "--cloudinit-userdata", str(user_data_path),
                "--cloudinit-networkconfig", str(network_config_path),
            ]
        else:
            cmd += ["--first-run-script", str(first_run_path)]

        cmd += [image, args.device]
        result = _run_imager(cmd)
        state.update({
            "status": "flashed_and_provisioned",
            "imager_returncode": result.returncode,
            "first_boot_provisioning_injected": True,
        })
        save_state(args.project, "boot_state", state)
        print_json(state)
    finally:
        if args.keep_provisioning_files:
            state["provisioning_debug_dir"] = str(tempdir)
            save_state(args.project, "boot_state", state)
        else:
            shutil.rmtree(tempdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackendError as exc:
        print_json({"status": "error", "error": str(exc)})
        raise SystemExit(2)
