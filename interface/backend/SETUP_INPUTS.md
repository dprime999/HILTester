# HILTester v2.2 - minimum setup inputs

The normal setup should ask the user for intent and secrets only. The backend discovers or resolves everything else.

## You provide

### Project
- **Project name**.

### Raspberry Pi
- **Desired hostname** - default can be `rpi-hiltester`.
- **Desired username** - default can be `hiltester`.
- **OS choice only if overriding the default**. Normal default is `lite-64`, which resolves the latest official Raspberry Pi OS Lite 64-bit image compatible with Pi 5.

### Wi-Fi
- **Wi-Fi SSID**.
- **Wi-Fi password** - secret; pass transiently through `HILTESTER_WIFI_PASSWORD`.

### Tailscale
- **Tailscale account** - created outside HILTester.
- **Tailscale auth key** - secret; pass transiently through `TAILSCALE_AUTHKEY`.

### GitHub
- **GitHub repository** - e.g. `dprime999/HILTester`.
- **Authentication only if needed** - public repos can clone/pull anonymously.
- **Branch only if you do not want the repository default branch**.

## The backend creates, resolves, or discovers

### OS / boot media
- official Raspberry Pi Imager OS catalog
- current Pi 5-compatible Raspberry Pi OS Lite 64-bit image URL
- official compressed-image SHA256
- image release date and provisioning format
- removable USB drive list, device path and size
- project-scoped SSH keypair
- first-run/cloud-init provisioning payload
- image download through Raspberry Pi Imager
- checksum verification and flash verification through Raspberry Pi Imager
- Wi-Fi, SSH key, hostname and Tailscale bootstrap on first boot

### Network / Tailscale
- Pi Tailscale IPv4 address
- online/offline state
- SSH connectivity result

### Hardware
- USB device list from `lsusb`
- VID/PID and descriptions
- saved expected-hardware profile from selected scan results
- missing/present status on rescan

### Docker / deployment
- Docker availability
- repository clone/update state
- Compose file path when unambiguous
- image names/tags from Compose
- pull/start/status operations

## Not normally required

Do not ask the user for these during ordinary setup:

- Raspberry Pi OS download URL or local image file
- Raspberry Pi OS SHA256
- Tailscale IP
- SSH key path
- USB VID/PID values
- Docker image name/tag
- Compose path
- current Git commit
- remote clone directory

`--image` remains available only as an advanced override for a custom or pinned image.
