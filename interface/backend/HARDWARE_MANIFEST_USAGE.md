# HILTester hardware manifest

Put `hardware_manifest.json` and `hardware_manifest.py` alongside `run_all.py`, `user_arguments.py`, `hardware_profile.py`, and `common.py` inside `interface/backend/`.

## Normal operation

```powershell
python interface/backend/run_all.py deploy-test
```

The application reads `interface/backend/hardware_manifest.json` automatically. You **do not** type hardware identifiers or choose USB scan numbers. The Pi is scanned after boot and SSH connection. You may still have to insert the boot drive in Windows and press Enter when the flashed Pi has been powered on. Disk erasure still needs explicit confirmation.

The file models these connections:

- Windows PC USB -> SanDisk USB drive (during flashing); later SanDisk USB drive -> Pi 5 USB (during boot).
- Pi 5 USB -> ST-LINK USB debugger -> SWD pins on the STM32F4 board.
- Pi 5 USB -> Raspberry Pi Pico USB (possibly via a USB hub).
- Pi 5 -> Tailscale over Wi-Fi/Ethernet for remote SSH and GitHub runner.

## Expected hardware / limitations

- **ST-LINK (required):** one of the listed STMicroelectronics USB VID/PIDs. Edit `usb_pids` to match a different programmer model. A USB hub is optional.
- **Pico (optional for `lsusb` verification):** the Pico's USB identity changes with firmware and BOOTSEL mode. Some blink programs provide no USB interface at all. The GitHub Actions flashing job remains the real functional test of Pico flashing.
- **SanDisk drive (required):** scans `lsblk` on the Pi and checks for a USB disk with `SanDisk` in its model name. This is a presence check, not proof which disk mounted `/` (the OS). Edit `model_contains` when using another brand, or set `required` to `false` for SSD/SD-based systems.

The supplied VID/PID alternatives are general matching candidates, **not verified IDs from your current hardware**. If verification fails but the board is connected, inspect `lsusb` on the Pi and update the manifest.

## Custom manifest

```powershell
python interface/backend/run_all.py deploy-test --hardware-manifest "C:\path\to\my_hardware.json"
```

`--skip-hardware` still bypasses manifest verification for troubleshooting. `resume` can skip completed hardware checks recorded in the project state; to force a fresh check, use a new project or reset the step appropriately.

The results are written to `~/.hiltester/projects/<project>/hardware_profile.json` for the existing status frontend.
