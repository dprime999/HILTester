from __future__ import annotations

import argparse
import re
from collections import Counter

from common import BackendError, load_state, print_json, run, save_state, ssh_argv

LSUSB_LINE = re.compile(
    r"^Bus\s+(?P<bus>\d+)\s+Device\s+(?P<device>\d+):\s+ID\s+"
    r"(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4})(?:\s+(?P<description>.*))?$"
)


def parse_lsusb(text: str) -> list[dict]:
    devices: list[dict] = []
    for index, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        match = LSUSB_LINE.match(line)
        if not match:
            continue
        vid = match.group("vid").lower()
        pid = match.group("pid").lower()
        desc = (match.group("description") or "Unknown USB device").strip()
        devices.append({
            "index": index,
            "bus": match.group("bus"),
            "device": match.group("device"),
            "vid": vid,
            "pid": pid,
            "vid_pid": f"{vid}:{pid}",
            "description": desc,
            "display": f"{desc} ({vid}:{pid})",
        })
    return devices


def scan_local() -> list[dict]:
    result = run(["lsusb"], timeout=20, check=True)
    return parse_lsusb(result.stdout)


def scan_remote(project: str, host: str, user: str, port: int) -> list[dict]:
    result = run(ssh_argv(project, host, user, port, "lsusb", connect_timeout=8), timeout=25, check=True)
    return parse_lsusb(result.stdout)


def resolve_scan_target(project: str, host: str | None, user: str | None, port: int) -> tuple[str | None, str, int]:
    network = load_state(project, "network_state")
    resolved_host = host or network.get("tailscale_ip")
    resolved_user = user or network.get("ssh_user") or "hiltester"
    return resolved_host, resolved_user, port or int(network.get("ssh_port", 22))


def select_devices(devices: list[dict], selectors: list[str]) -> list[dict]:
    selected: list[dict] = []
    for selector in selectors:
        selector = selector.strip().lower()
        matches = [
            d for d in devices
            if selector == str(d["index"]).lower()
            or selector == d["vid_pid"].lower()
            or selector in d["description"].lower()
        ]
        if not matches:
            raise BackendError(f"selection did not match any scanned USB device: {selector}")
        for device in matches:
            if device not in selected:
                selected.append(device)
    return selected


def profile_entries(selected: list[dict]) -> list[dict]:
    counts = Counter(d["vid_pid"] for d in selected)
    unique: list[dict] = []
    seen: set[str] = set()
    for device in selected:
        key = device["vid_pid"]
        if key in seen:
            continue
        seen.add(key)
        unique.append({
            "vid": device["vid"],
            "pid": device["pid"],
            "vid_pid": key,
            "label": device["description"],
            "required_count": counts[key],
        })
    return unique


def verify_profile(expected: list[dict], scanned: list[dict]) -> tuple[list[dict], list[dict]]:
    actual_counts = Counter(d["vid_pid"] for d in scanned)
    results = []
    missing = []
    for item in expected:
        need = int(item.get("required_count", 1))
        have = actual_counts[item["vid_pid"]]
        result = {**item, "detected_count": have, "present": have >= need}
        results.append(result)
        if have < need:
            missing.append(result)
    return results, missing


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scan USB devices, save selected devices as the expected HIL rig, or rescan and verify the saved profile."
    )
    p.add_argument("--project", required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scan", action="store_true", help="scan and print current USB devices")
    mode.add_argument("--save", action="store_true", help="scan and save selected devices as the expected profile")
    mode.add_argument("--verify", action="store_true", help="rescan and compare against the saved profile")
    p.add_argument("--local", action="store_true", help="scan this computer instead of the configured Pi")
    p.add_argument("--host", help="remote host; defaults to Tailscale IP from network state")
    p.add_argument("--ssh-user", help="defaults to network state or hiltester")
    p.add_argument("--ssh-port", type=int, default=22)
    p.add_argument(
        "--select", action="append", default=[],
        help="for --save: select by scan index, VID:PID, or description substring; repeatable",
    )
    return p


def main() -> int:
    args = parser().parse_args()
    host, user, port = resolve_scan_target(args.project, args.host, args.ssh_user, args.ssh_port)

    if args.local:
        scanned = scan_local()
        source = "local"
    else:
        if not host:
            raise BackendError("no remote host is known yet; use --local or complete the Tailscale/network step first")
        scanned = scan_remote(args.project, host, user, port)
        source = host

    if args.scan:
        state = {
            "step": "hardware",
            "status": "scanned",
            "source": source,
            "scanned_devices": scanned,
            "saved_profile_exists": bool(load_state(args.project, "hardware_profile").get("expected_devices")),
        }
        save_state(args.project, "hardware_scan", state)
        print_json(state)
        return 0

    if args.save:
        if not args.select:
            raise BackendError("--save requires at least one --select value from the current scan")
        selected = select_devices(scanned, args.select)
        expected = profile_entries(selected)
        state = {
            "step": "hardware",
            "status": "confirmed",
            "source": source,
            "expected_devices": expected,
            "selected_devices": selected,
            "selection_method": "scan_then_select",
        }
        save_state(args.project, "hardware_profile", state)
        print_json(state)
        return 0

    profile = load_state(args.project, "hardware_profile")
    expected = profile.get("expected_devices") or []
    if not expected:
        raise BackendError("no saved hardware profile; run --scan, then --save with one or more --select values")
    results, missing = verify_profile(expected, scanned)
    state = {
        **profile,
        "step": "hardware",
        "status": "confirmed" if not missing else "missing_devices",
        "last_scan_source": source,
        "verification": results,
        "missing": missing,
        "scanned_devices": scanned,
    }
    save_state(args.project, "hardware_profile", state)
    print_json(state)
    return 0 if not missing else 4


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackendError as exc:
        print_json({"status": "error", "error": str(exc)})
        raise SystemExit(2)
