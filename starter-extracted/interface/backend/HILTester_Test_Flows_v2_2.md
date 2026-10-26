# HILTester Backend v2.2 - Test Flows

*Fresh bare Pi and already-provisioned Pi*

> **Key change:** You no longer provide or download the normal Raspberry Pi OS image. The backend resolves the latest official Raspberry Pi OS Lite 64-bit image compatible with Pi 5 and gives its remote URL/checksum to Raspberry Pi Imager, which downloads and flashes it. `--image` is only an advanced override.

## Flow A - Brand-new / bare Raspberry Pi

Use this when the Pi has no usable HILTester OS yet. The PC prepares and flashes the USB, then the Pi boots, joins Wi-Fi/Tailscale, and becomes reachable over SSH.

### A1. Set transient secrets

```bash
export HILTESTER_WIFI_PASSWORD="YOUR_WIFI_PASSWORD"
export TAILSCALE_AUTHKEY="YOUR_TS_KEY"
```

PowerShell:

```powershell
$env:HILTESTER_WIFI_PASSWORD="YOUR_WIFI_PASSWORD"
$env:TAILSCALE_AUTHKEY="YOUR_TS_KEY"
```

These secrets are not written to project state JSON.

### A2. Verify automatic OS selection

```bash
python interface/backend/boot_provision.py --project demo --resolve-os
```

Safe test with no USB required. It should report Raspberry Pi OS Lite (64-bit), a Pi 5-compatible device list, release date, remote image URL, checksum, and init format.

### A3. Find the USB drive

```bash
python interface/backend/boot_provision.py --project demo --list-disks
```

Identify the 64 GB removable USB. Use the returned device path in the flash command.

### A4. Preflight provisioning

```bash
python interface/backend/boot_provision.py --project demo --hostname rpi-hiltester --username hiltester --wifi-ssid "YOUR_WIFI" --wifi-country US
```

No flash occurs. The backend resolves the OS and prepares project SSH/provisioning state.

### A5. Flash + inject first-boot provisioning

```bash
python interface/backend/boot_provision.py --project demo --device /dev/sdX --hostname rpi-hiltester --username hiltester --wifi-ssid "YOUR_WIFI" --wifi-country US --apply --yes-really-flash
```

Replace `/dev/sdX` with the detected removable device. No `--image` is needed. Imager downloads the resolved official OS, verifies it, flashes the USB, and injects first-boot configuration.

### A6. Move USB to Pi and boot

Physical step: eject USB from PC -> insert into Pi -> power Pi.

On first boot the provisioning should configure hostname/user, SSH key, Wi-Fi, and Tailscale. Give the Pi time to complete first boot.

### A7. Discover Pi / verify SSH

```bash
python interface/backend/network_tailscale.py --project demo --hostname rpi-hiltester --ssh-user hiltester
```

The backend should find the Pi through Tailscale, discover its `100.x` address, and test SSH.

### A8. Scan and save hardware profile

```bash
python interface/backend/hardware_profile.py --project demo --scan
python interface/backend/hardware_profile.py --project demo --save --select 1 --select 2 --select 3
python interface/backend/hardware_profile.py --project demo --verify
```

Use the scan indexes that correspond to the ST-LINK/STM32, Pico, and hub. Future verify/rescan checks that the saved devices are still present.

### A9. Verify repo and dry-run deployment

```bash
python interface/backend/github_backend.py --project demo --repo dprime999/HILTester --branch tui
python interface/backend/deploy_backend.py --project demo --dry-run
```

Public repos do not need GitHub credentials. Dry-run shows the SSH/Docker/repo/Compose actions before changing the Pi.

### A10. Deploy

```bash
python interface/backend/deploy_backend.py --project demo
```

Runs the real repository update and Docker Compose deployment after the dry-run looks correct.

## Flow B - Pi that is already provisioned and connected

Use this for your current Pi that already boots, has networking/Tailscale/SSH, and may already be running Docker. Skip the entire USB/OS provisioning section.

### B1. Verify current Pi connectivity

```bash
python interface/backend/network_tailscale.py --project demo --hostname YOUR_PI_HOSTNAME --ssh-user YOUR_PI_USER
```

Confirms Tailscale discovery and SSH using the existing Pi.

### B2. Scan currently attached USB hardware

```bash
python interface/backend/hardware_profile.py --project demo --scan
```

Safe even if nothing useful is plugged in. Repeat later when the STM32/Pico/hub are connected.

### B3. Verify the repository

```bash
python interface/backend/github_backend.py --project demo --repo dprime999/HILTester --branch tui
```

Checks repo/branch access. Omit `--branch` if you want the repository default branch.

### B4. Dry-run deployment

```bash
python interface/backend/deploy_backend.py --project demo --dry-run
```

Important on the existing Pi: inspect what repo/Compose project it will target before touching the container already running there.

### B5. Inspect project state

```bash
python interface/backend/status_backend.py --project demo
```

Shows the state the frontend will eventually consume.

### B6. Deploy only when the dry-run is correct

```bash
python interface/backend/deploy_backend.py --project demo
```

This may update/recreate containers belonging to the same Compose project, so use it only after confirming the dry-run target.

## Advanced image override

```bash
python interface/backend/boot_provision.py --project demo --image /path/to/custom.img.xz --device /dev/sdX --apply --yes-really-flash
```

Use `--image` only when you intentionally want a custom, local, or pinned OS image. The normal Pi 5 workflow does not require it.
