"""Load and verify the bundled HILTester Pi/MCU hardware manifest."""
from __future__ import annotations

import json
import re
from pathlib import Path

from common import BackendError, run, ssh_argv

DEFAULT_MANIFEST = Path(__file__).with_name("hardware_manifest.json")
HEX4 = re.compile(r"^[0-9a-fA-F]{4}$")


def load_manifest(path: str | Path | None = None) -> dict:
    location = Path(path).expanduser().resolve() if path else DEFAULT_MANIFEST
    try:
        data = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackendError(f"Cannot load hardware manifest {location}: {exc}") from exc
    if data.get("schema_version") != 1 or not isinstance(data.get("usb_hardware"), list):
        raise BackendError("Unsupported hardware manifest schema")
    ids = set()
    for item in data["usb_hardware"]:
        if not isinstance(item, dict) or not item.get("id") or item["id"] in ids:
            raise BackendError("Hardware manifest contains duplicate or missing device IDs")
        ids.add(item["id"])
        vid = str(item.get("usb_vid", ""))
        pids = item.get("usb_pids", [])
        if not HEX4.fullmatch(vid) or not isinstance(pids, list) or not all(HEX4.fullmatch(str(p)) for p in pids):
            raise BackendError(f"Invalid USB VID/PID for {item['id']}")
    return data


def verify_usb(manifest: dict, scanned: list[dict]) -> dict:
    """Match ST-LINK and Pico independently. A USB device cannot satisfy both."""
    details = []
    missing = []
    for spec in manifest["usb_hardware"]:
        vid = spec["usb_vid"].lower()
        pids = {str(p).lower() for p in spec.get("usb_pids", [])}
        matches = [d for d in scanned if d.get("vid", "").lower() == vid and
                   (not pids or d.get("pid", "").lower() in pids)]
        found = bool(matches)
        details.append({"id": spec["id"], "name": spec.get("name", spec["id"]),
                        "required": bool(spec.get("required", True)), "present": found,
                        "matches": matches, "expected_vid": vid, "expected_pids": sorted(pids)})
        if not found and spec.get("required", True):
            missing.append(spec["id"])
    return {"ok": not missing, "devices": details, "missing_required": missing}


def inspect_boot_storage(block_devices: list[dict], expected: dict) -> dict:
    """Verify the mounted root filesystem lives on the expected USB-backed disk.

    Unlike a model-only scan, this cannot pass on an unrelated USB drive plugged
    into a Pi that is actually booted from SD/NVMe. `lsblk` includes children for
    partitions, so the mounted root partition is traced back to its parent disk.
    """
    required = bool(expected.get("required", True))
    transport = str(expected.get("expected_transport", "usb")).casefold()
    model_marker = str(expected.get("model_contains") or "").strip().casefold()
    vendor_marker = str(expected.get("vendor_contains") or "").strip().casefold()
    all_disks = []
    matches = []

    def mounted_at_root(node: dict) -> bool:
        points = node.get("mountpoints") or []
        if isinstance(points, str):
            points = [points]
        return "/" in points or node.get("mountpoint") == "/"

    def walk(node: dict, parent_disk: dict | None = None) -> None:
        disk = parent_disk
        if node.get("type") == "disk":
            disk = {"name": node.get("name"),
                    "model": (node.get("model") or "").strip(),
                    "vendor": (node.get("vendor") or "").strip(),
                    "transport": (node.get("tran") or "").strip(),
                    "root_partition": None}
            all_disks.append(disk)
        if disk is not None and mounted_at_root(node):
            disk["root_partition"] = node.get("name")
        for child in node.get("children") or []:
            walk(child, disk)

    for node in block_devices:
        walk(node)

    for disk in all_disks:
        if not disk["root_partition"]:
            continue
        if transport and disk["transport"].casefold() != transport:
            continue
        # Backward compatibility: the original manifest used model_contains:
        # "SanDisk" even though Linux reports SanDisk as VENDOR, and Cruzer
        # Glide as MODEL. Accept the marker in either field.
        combined = f"{disk['vendor']} {disk['model']}".casefold()
        if model_marker and model_marker not in combined:
            continue
        if vendor_marker and vendor_marker not in disk["vendor"].casefold():
            continue
        matches.append(disk)

    return {"present": bool(matches), "matching_disks": matches,
            "all_disks": all_disks,
            "expected_model_contains": expected.get("model_contains"),
            "required": required}


def scan_boot_storage(project: str, net: dict, manifest: dict) -> dict:
    """Inspect the root filesystem's parent disk over SSH (no assumed /dev/sda)."""
    script = "lsblk -J -o NAME,MODEL,VENDOR,TRAN,TYPE,MOUNTPOINTS"
    result = run(ssh_argv(project, net["tailscale_ip"], net["ssh_user"],
                          int(net.get("ssh_port", 22)), script), timeout=30)
    if not result.ok:
        raise BackendError("Unable to inspect Pi storage: " + (result.stderr or result.stdout)[-500:])
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise BackendError("Pi returned malformed lsblk output") from exc
    return inspect_boot_storage(data.get("blockdevices") or [], manifest.get("boot_storage") or {})
