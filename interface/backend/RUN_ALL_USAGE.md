# HILTester — `run_all.py` usage guide

This guide matches the current `run_all.py` and `user_arguments.py`. Run the CLI on your **Windows PC**, from your HILTester project root, with all companion Python backends together in `interface/backend/`.

## Quick start

```powershell
python interface/backend/run_all.py
```

The CLI asks which mode to run. You can also supply **only the mode**; the rest is prompted or filled from saved project preferences:

```powershell
python interface/backend/run_all.py deploy
```

Available modes:

| Mode | Behavior |
| --- | --- |
| `usb` | Select a removable disk and flash Pi OS. |
| `github` | Initialize the supplied repo from `starter.tar.gz`, or verify an already-populated repo. |
| `usb-github` | GitHub setup followed by USB flashing. |
| `deploy` | GitHub setup/verification, optional USB flash, Tailscale/SSH check, optional hardware selection, Docker and runner deployment. |
| `deploy-test` | Same as `deploy`, then dispatch and monitor a GitHub Actions workflow. **Can flash connected MCUs.** |
| `resume` | Skip stages recorded as complete in a previous `run_all.py` run for this project. |

`deploy` is a real deployment, **not** a read-only health check: it can sync the repo on the Pi, pull an image, and restart/recreate the runner container.

## What the interactive prompts ask

1. **Project name** (for example, `demo`). Enter the same name as earlier if you want to reuse that project's SSH key and state.
2. **GitHub repository** (`owner/repo`, e.g. `dprime999/HILStarter`) and optional branch (leave blank to use its default).
3. **Pi hostname** (e.g. `raspberrypi`), **Pi SSH username** (e.g. `dprime999`), and Wi-Fi SSID when applicable.
4. For deploy modes: **Is the Pi already running an OS, Tailscale and SSH?** Say **yes** to skip USB flashing.
5. **Is the GitHub repository already populated?** Say **yes** to skip uploading the starter archive.
6. If flashing: choose a numbered removable disk, verify its identity and size, and type **`ERASE`** to confirm destruction of that disk's data. Insert the flashed drive into the Pi and power on when prompted.
7. If deploying: the CLI checks Tailscale and SSH every **15 seconds** while waiting (default timeout **15 minutes**). When prompted, connect MCUs/programmers to the Pi, scan the numbered USB device list, then select indexes such as `1,3` (or `R` to rescan).
8. The CLI deploys your Docker runner. For `deploy-test` it asks before dispatching the firmware workflow and reports the GitHub Actions conclusion.
9. It optionally asks whether to clone a **separate working copy onto the Windows PC**. Answer **no** if you do not need one. The deployment backend independently clones/syncs the repository *on the Pi*.

**Important:** `usb` always offers disk flashing; use `deploy` with **existing Pi = yes** when you already have a provisioned Pi. Hardware selection identifies expected USB devices; it does not itself flash firmware.

## Credentials and persistent settings

The program reuses non-sensitive defaults from:

```text
~/.hiltester/projects/<project>/user_config.json
```

This includes the repo, branch, hostname, SSH username, Wi-Fi SSID and country. Stage completion is tracked in `run_all_state.json` in the same directory; the other backends also keep their individual state files. **Changing the project name means using different saved state and potentially a different generated SSH key.**

Secrets are **not saved to these local JSON files**. The CLI uses environment variables already present, or asks you to paste secrets into hidden prompts when needed:

| Environment variable | Needed for |
| --- | --- |
| `GITHUB_TOKEN` | Uploading the starter, registering the runner, and dispatching/reporting Actions. |
| `TAILSCALE_AUTHKEY` | Configuring Tailscale on a *newly flashed* Pi. |
| `HILTESTER_WIFI_PASSWORD` | Configuring Wi-Fi on a *newly flashed* Pi when an SSID is entered. |

Optional PowerShell setup (don't echo these secrets or paste them into screenshots):

```powershell
$env:GITHUB_TOKEN = "YOUR_GITHUB_TOKEN"
$env:TAILSCALE_AUTHKEY = "YOUR_TAILSCALE_AUTH_KEY"
$env:HILTESTER_WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
```

The **deployment backend** still places runner credentials in a root-restricted `/etc/hiltester/runner.env` on the Pi because the current Docker runner entrypoint requires them. Avoid committing that file. Use a scoped token with appropriate GitHub Actions runner administration permissions.

## Example A — Existing HILStarter repo and provisioned Pi (recommended first test)

```powershell
python interface/backend/run_all.py deploy
```

At the prompts, use project `demo`, repo `dprime999/HILStarter`, hostname `raspberrypi` and SSH user `dprime999` (or your actual settings). Answer **yes** for **existing Pi** and **existing GitHub repo**. Connect the MCUs when asked, or use the non-hardware option below.

If you want to skip most prompts explicitly (flags are optional overrides):

```powershell
python interface/backend/run_all.py deploy --project demo --repo dprime999/HILStarter --hostname raspberrypi --username dprime999 --existing-pi --skip-github-init --skip-hardware --no-clone
```

`--skip-hardware` skips choosing a hardware profile; it does **not** prevent already-enabled GitHub workflows from reacting to pushes. Avoid unsolicited firmware flashing by reviewing the repository's workflow triggers.

## Example B — Fresh Pi + already-populated GitHub repository

```powershell
python interface/backend/run_all.py deploy
```

At the prompts answer **no** for **existing Pi**, **yes** for **existing GitHub repo**. Have a removable USB boot drive ready, and double-check the chosen disk before typing `ERASE`. Following the flash, move the drive to the Pi and power it on. The CLI waits for the Pi to appear on Tailscale, then asks for connected hardware and deploys the Docker runner.

## Example C — Full first-time setup (empty GitHub repo + bare Pi)

Place `starter.tar.gz` in the top-level HILTester folder, or provide `--archive` to override its location. Set up a GitHub repo before starting; the GitHub backend fills it using the API without cloning it on Windows.

```powershell
python interface/backend/run_all.py deploy
```

Answer **no** to both **GitHub already populated?** and **Pi already provisioned?** The CLI uploads the starter first, flashes the chosen drive, waits for network access, asks you to select hardware, then deploys Docker and verifies the runner online. The initializer checks for conflicting existing files; an existing root `README.md` can be replaced by the starter.

If you want to separate the phases:

```powershell
python interface/backend/run_all.py github
python interface/backend/run_all.py deploy
```

For the second command, answer **yes** when asked if the repo is already populated.

## Example D — Run the end-to-end firmware Actions test

Only do this with the expected Pico/STM32 and flashing hardware connected, powered and safe to program:

```powershell
python interface/backend/run_all.py deploy-test
```

For an already-provisioned system, answer **yes** for existing Pi and existing GitHub repo. The default workflow is `main.yml`; it must support `workflow_dispatch`. The CLI prompts before dispatching it and checks the result approximately every 15 seconds. **This mode does not automatically change any `.c` files or blink speed.** It runs your workflow as currently committed.

To select a different workflow:

```powershell
python interface/backend/run_all.py deploy-test --workflow test-runner.yml
```

A test-only workflow that does not flash hardware is useful for initial validation, if you've added one to the repo with `workflow_dispatch` enabled.

## Example E — Resume after interruption

```powershell
python interface/backend/run_all.py resume
```

Choose the same project name, and verify any prompted settings. `resume` uses **`run_all_state.json`**, not just older state from individual backends. It skips steps marked `complete`. It does not automatically establish that a previously completed step is still healthy; reconnect/check your Pi as necessary.

## Useful optional flags

```powershell
python interface/backend/run_all.py --help
```

| Flag | Use |
| --- | --- |
| `--project demo` | Select/reuse local project state and SSH key. |
| `--repo owner/repo` | Override saved GitHub repo. |
| `--hostname NAME`, `--username USER` | Override Pi address identity. |
| `--existing-pi` | Skip OS flashing. |
| `--skip-github-init` | Verify existing repo rather than uploading starter again. |
| `--skip-hardware` | Do not scan/select MCUs during this run. |
| `--no-clone` | Skip optional *Windows PC* working clone. |
| `--archive PATH` | Choose a particular starter archive. |
| `--device PATH` | Supply a disk path (still requires typing `ERASE`). |
| `--ip ADDRESS` | Supply a known Pi Tailscale IP. |
| `--image IMAGE` | Override the Docker runner image; default is maintained HILTester image. |
| `--workflow FILE` | Select workflow for `deploy-test`; default `main.yml`. |
| `--wait-minutes N` | Change waiting timeout for Pi connectivity / Actions test. |
| `-v`, `--verbose` | Print fuller backend output. |

## How to verify success

- **Boot/Network:** Pi is visible in Tailscale, and SSH works with the project key.
- **GitHub:** Target repository contains the starter files and expected workflow files.
- **Docker:** Runner container is up on the Pi and uses the intended image.
- **Runner:** In GitHub repo **Settings → Actions → Runners**, the expected `hil-<project>-pi5` runner is **online**.
- **Firmware:** A dispatched Actions run compiles successfully; with connected hardware, Pico and STM32 flash stages succeed. Your working STM32 workflow requires suitable ST-LINK USB permissions (you previously validated `sudo openocd`).

If a step fails, rerun with `-v` and inspect its error. The CLI itself doesn't guarantee the Pi remains online indefinitely; runner status is verified at deployment time.

## Reset and retest safely

For routine experiments **do not wipe the USB**. Preview a normal cleanup, then run it:

```powershell
python interface/backend/clean.py --project demo --repo dprime999/HILStarter --pi-all --dry-run
python interface/backend/clean.py --project demo --repo dprime999/HILStarter --pi-all --reset-progress
```

This removes the HILTester runner/container/deployment checkout but preserves Raspberry Pi OS, Docker, SSH, Tailscale and the GitHub repo. Then run `python interface/backend/run_all.py deploy`, answering **yes** to the existing-Pi/existing-repo questions.

Use `--total-deprovision` only for more destructive testing: it can remove Docker, Tailscale and HILTester's SSH key, ending remote access. **It leaves the OS installed and does not automatically re-enable the original first-boot provisioning on the already-booted OS.** For a true fresh-system test, safely shut down the Pi, move its storage to the PC and reflash via the normal `usb` workflow.

See `CLEAN_USAGE.md` for cleanup ordering and warnings.

## Current limitations

- The scripts have been checked locally, but this combined procedure has not been verified against your live Pi end-to-end.
- No automated STM32 blink-speed source edit is implemented.
- An existing private repository may require appropriate Git credential configuration for the optional *PC clone*.
- The GitHub initializer uploads files individually, so a failed initialization may leave partial commits. Inspect the repo before retrying.
- Resume tracks orchestration stages; it is not a substitute for fresh runtime health checks.
- Pi-side `git clone`/sync is intentional and separate from starter initialization through the GitHub API.
