from __future__ import annotations

import argparse

from common import load_state, print_json

STEPS = ["boot_state", "network_state", "github_state", "hardware_scan", "hardware_profile", "deploy_state"]


def main() -> int:
    p = argparse.ArgumentParser(description="Print current backend state for one HILTester project.")
    p.add_argument("--project", required=True)
    args = p.parse_args()
    print_json({name: load_state(args.project, name) for name in STEPS})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
