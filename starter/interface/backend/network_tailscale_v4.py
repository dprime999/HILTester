from __future__ import annotations

import argparse
import json
import re

from common import BackendError, ensure_project_ssh_key, print_json, run, save_state, ssh_argv

TS_IP = re.compile(r"^100(?:\.\d{1,3}){3}$")


def local_tailscale_peers() -> list[dict]:
    result = run(["tailscale", "status", "--json"], timeout=30, check=True)
    data = json.loads(result.stdout)
    peers = []
    self_node = data.get("Self") or {}
    nodes = [("self", self_node)] + [(key, value) for key, value in (data.get("Peer") or {}).items()]
    for key, node in nodes:
        addrs = node.get("TailscaleIPs") or []
        peers.append({
            "id": key,
            "hostname": node.get("HostName") or node.get("DNSName"),
            "dns_name": node.get("DNSName"),
            "online": node.get("Online", True if key == "self" else None),
            "tailscale_ips": addrs,
        })
    return peers


def choose_peer(peers: list[dict], hostname: str | None, ip: str | None) -> dict:
    if ip:
        for peer in peers:
            if ip in peer.get("tailscale_ips", []):
                return peer

    if hostname:
        matches = []
        for peer in peers:
            names = {peer.get("hostname"), peer.get("dns_name")}
            if hostname in names or any(str(x).startswith(hostname + ".") for x in names if x):
                matches.append(peer)
        if matches:
            return next((peer for peer in matches if peer.get("online") is True), matches[0])

    raise BackendError("target was not found in local Tailscale status")


def ssh_check(project: str, host: str, user: str, port: int, timeout: int) -> dict:
    result = run(ssh_argv(project, host, user, port, "printf HILTESTER_SSH_OK", connect_timeout=timeout), timeout=timeout + 10)
    return {"ok": result.ok and "HILTESTER_SSH_OK" in result.stdout, "returncode": result.returncode, "stderr": result.stderr}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Resolve the Pi on Tailscale and verify project-key SSH.")
    p.add_argument("--project", required=True)
    p.add_argument("--hostname", default="rpi-hiltester")
    p.add_argument("--ip", help="optional known Tailscale IP; normally auto-discovered")
    p.add_argument("--ssh-user", default="hiltester")
    p.add_argument("--ssh-port", type=int, default=22)
    p.add_argument("--timeout", type=int, default=8)
    p.add_argument("--skip-ssh-check", action="store_true")
    return p


def main() -> int:
    args = parser().parse_args()
    private_key, public_key = ensure_project_ssh_key(args.project)
    peers = local_tailscale_peers()
    target = choose_peer(peers, args.hostname, args.ip)
    ip = args.ip or next((x for x in target.get("tailscale_ips", []) if TS_IP.match(x)), None)
    if not ip:
        raise BackendError("target has no IPv4 Tailscale address")

    ssh = {"ok": None}
    if not args.skip_ssh_check:
        ssh = ssh_check(args.project, ip, args.ssh_user, args.ssh_port, args.timeout)

    state = {
        "step": "network",
        "status": "confirmed" if (args.skip_ssh_check or ssh.get("ok")) else "ssh_failed",
        "hostname": target.get("hostname") or args.hostname,
        "tailscale_ip": ip,
        "online": target.get("online"),
        "ssh_user": args.ssh_user,
        "ssh_port": args.ssh_port,
        "ssh": ssh,
        "ssh_private_key_path": str(private_key),
        "ssh_public_key_path": str(public_key),
        "ssh_key_generated_or_reused": True,
    }
    save_state(args.project, "network_state", state)
    print_json(state)
    return 0 if state["status"] == "confirmed" else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackendError as exc:
        print_json({"status": "error", "error": str(exc)})
        raise SystemExit(2)
