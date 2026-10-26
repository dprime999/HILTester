# HILTester backend v2.2 integration notes

This revision keeps the v2 workflow but closes the fresh-Pi OS-selection gap.

## What changed in v2.2

Only `interface/backend/boot_provision.py` changed.

Normal provisioning no longer requires the user to find or download a Raspberry Pi OS file. `--os lite-64` is the default and resolves the latest official Raspberry Pi OS Lite 64-bit image compatible with Raspberry Pi 5 from Raspberry Pi Imager's official repository JSON.

The resolved entry supplies:

- image URL
- official compressed-image SHA256
- release date
- supported device tags
- Imager init/provisioning format

Raspberry Pi Imager receives the remote image URL directly, so Imager performs the download and flash. `--image` is retained as an advanced override for custom/local/pinned images.

## Useful boot commands

Resolve the current official image without a USB drive or flash:

```bash
python interface/backend/boot_provision.py --project demo --resolve-os
```

List removable drives:

```bash
python interface/backend/boot_provision.py --project demo --list-disks
```

Preflight with the default OS:

```bash
python interface/backend/boot_provision.py \
  --project demo \
  --hostname rpi-hiltester \
  --username hiltester \
  --wifi-ssid "YOUR_WIFI" \
  --wifi-country US
```

Flash the default current official Lite 64-bit OS:

```bash
python interface/backend/boot_provision.py \
  --project demo \
  --device /dev/sdX \
  --hostname rpi-hiltester \
  --username hiltester \
  --wifi-ssid "YOUR_WIFI" \
  --wifi-country US \
  --apply \
  --yes-really-flash
```

Advanced custom image override:

```bash
python interface/backend/boot_provision.py \
  --project demo \
  --image /path/to/custom.img.xz \
  --device /dev/sdX \
  --apply \
  --yes-really-flash
```

## Existing screen/backend mapping

- Boot / provision -> `boot_provision.py`
- Network / Tailscale / SSH -> `network_tailscale.py`
- GitHub -> `github_backend.py`
- Hardware scan/save/rescan -> `hardware_profile.py`
- Deploy -> `deploy_backend.py`
- Project status -> `status_backend.py`

All other v2 behavior remains unchanged.
