# HILTester cleanup and deprovisioning

Put `clean.py` next to `common.py` in `interface/backend/`. Run commands from your HILTester project directory using PowerShell (or adjust for your shell). The examples use the existing project `demo` and GitHub repository `dprime999/HILStarter`.

## What cleanup means

| Mode | Removes | Keeps |
| --- | --- | --- |
| Normal Pi cleanup (`--pi-all`) | HIL runner container, named GitHub runner registration, validated Pi repository checkout, matching `/etc/hiltester` runner configuration | Raspberry Pi OS, disk partitions, SSH, Tailscale, Docker installation, GitHub repository/files/history |
| Additional Docker cleanup (`--uninstall-docker`) | Docker packages and `/var/lib/docker`, `/var/lib/containerd` (includes **images, volumes, build cache**) | Raspberry Pi OS, SSH, Tailscale |
| Tailscale cleanup (`--uninstall-tailscale`) | Tailscale installation, local Tailscale identity/state | Raspberry Pi OS, system SSH service |
| SSH cleanup (`--remove-ssh-key`) | Only the exact HILTester **project public key** found in the Pi's `authorized_keys` files | System SSH service, other SSH keys, users |
| Total (`--total-deprovision`) | Normal Pi cleanup + Docker uninstall + Tailscale uninstall + HILTester SSH public key removal; resets run-all progress and marks network/deploy state deprovisioned | Raspberry Pi OS and boot disk, other SSH keys/users, GitHub repository/history |
| GitHub branch cleanup (`--github-clean`) | Files tracked on one branch via a **new commit** | GitHub repository identity and commit history; other branches |

`--total-deprovision` **does not** clean the GitHub repository unless you explicitly add `--github-clean`. It does not erase the USB storage or reinstall Raspberry Pi OS. It also leaves the Raspberry Pi user account and system SSH daemon intact. It is a *HILTester deprovision*, not a factory reset.

## Recommended order: normal cleanup

1. Confirm the correct project and repository. Ensure you're not interrupting an active firmware job.
2. Preview without changes:

   ```powershell
   python interface/backend/clean.py --project demo --repo dprime999/HILStarter --pi-all --dry-run
   ```

3. Clean the runner, registration and application files:

   ```powershell
   python interface/backend/clean.py --project demo --repo dprime999/HILStarter --pi-all --reset-progress
   ```

4. Type `CLEAN demo` at the confirmation prompt. This **keeps** Docker, Pi OS, Tailscale and SSH so redeployment is fast.
5. Re-run the normal provisioning/deployment workflow as needed. If the Pi and GitHub repository are already initialized, tell `run_all.py` so it doesn't reflash the OS or initialize the repo again.

## Recommended order: total deprovision (keep OS)

**Before starting:** Keep physical access to the Pi and its drive. Confirm the Pi is reachable over SSH, and that Docker isn't used by unrelated containers or applications. Export `GITHUB_TOKEN` for GitHub runner removal or enter it at the hidden prompt. Avoid doing this over an unattended, Tailscale-only connection unless you are comfortable losing remote access.

1. Preview:

   ```powershell
   python interface/backend/clean.py --project demo --repo dprime999/HILStarter --total-deprovision --dry-run
   ```

2. Run:

   ```powershell
   python interface/backend/clean.py --project demo --repo dprime999/HILStarter --total-deprovision
   ```

3. Type `DEPROVISION demo`. The script proceeds in this order:
   - Stop/remove the matching HILTester runner container and validated clone/configuration.
   - Unregister the runner from GitHub.
   - Purge Docker packages and data **if no other containers remain**. If other containers exist, it refuses Docker removal rather than deleting them.
   - Queue the exact HILTester SSH public-key removal and Tailscale uninstall as a one-time `systemd-run` job, due in ~15 seconds.
   - Reset local run progress and mark the cached network/deploy states as deprovisioned.
4. Remote access can disappear after the final job runs. The CLI confirms **scheduling**, not the end of the final job. With local console access, check `/var/log/hiltester-final-clean.log` and `journalctl -u hiltester-final-deprovision` on the Pi if necessary.
5. To set up again, repeat network/Tailscale/SSH bootstrap as appropriate. Because OS and user account remain, you do **not** need to reflash solely to reinstall Docker and the runner; however, your original first-boot Tailscale/SSH bootstrap will not automatically repeat on an already-booted OS.

## Selective examples

Stop the runner but leave deployment files:

```powershell
python interface/backend/clean.py --project demo --stop
```

Stop the container and remove its checkout/runtime files, leaving its GitHub registration to clean separately:

```powershell
python interface/backend/clean.py --project demo --stop --remove-files
```

Remove only the GitHub runner registration:

```powershell
python interface/backend/clean.py --project demo --unregister
```

After normal `--pi-all` cleanup, uninstall Docker as a separate experiment:

```powershell
python interface/backend/clean.py --project demo --uninstall-docker
```

**Important:** Docker removal deletes Docker's global images and volume storage on this Pi. Don't run it if unrelated applications depend on Docker. It refuses if other containers exist but cannot guarantee other software isn't relying on shared files.

Remove only HILTester's SSH public key (system SSH remains enabled):

```powershell
python interface/backend/clean.py --project demo --remove-ssh-key
```

Remove Tailscale (scheduled last; current connection may be lost):

```powershell
python interface/backend/clean.py --project demo --uninstall-tailscale
```

Clear HILStarter's **tracked branch files** only when you explicitly want to reinitialize the GitHub starter:

```powershell
python interface/backend/clean.py --project demo --repo dprime999/HILStarter --github-clean
```

Confirmation is `WIPE dprime999/HILStarter`. This does **not** destroy the Git repository or purge its history; it commits file deletions. If combining GitHub cleanup with Pi cleanup, stop/unregister the runner first to avoid new workflow jobs.

## Important limitations and safeguards

- All actions have `--dry-run`; it prints a plan but does not prove the remote Pi is currently reachable.
- Docker uninstall uses Debian/apt commands intended for Raspberry Pi OS. It is **not** a generic Linux package cleanup tool.
- The Pi repository directory must be a safe `/opt/hiltester/<repo-name>` path with the expected Git origin, so deletion cannot accidentally target arbitrary files.
- Tailscale and SSH cleanup are intentionally the **last** remote operations; losing the connection is expected. The final job requires `systemd-run`.
- SSH cleanup removes only the local HILTester project public key from `authorized_keys`. It does not disable SSH, delete the `dprime999` user, revoke all SSH credentials or erase local private keys.
- The script does **not** wipe storage, remove Raspberry Pi OS, remove other system packages or reverse every change first-boot provisioning ever made. For a truly blank device, power off safely, remove the USB drive, then reflash it on your PC using your existing boot-provisioning flow.
- GitHub runner cleanup requires an appropriately permissioned `GITHUB_TOKEN`. No token is written into local run state.
- Validate `--dry-run` first. No live Pi or GitHub account was modified during development or testing of this version.
