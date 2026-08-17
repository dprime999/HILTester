# HILTester — Pico + STM32F446RE CI/CD

Push firmware source to `main`, and a self-hosted GitHub Actions runner on a Raspberry Pi builds and flashes both boards automatically.

## Repo layout

```
HILTester/
├── .github/workflows/main.yml
├── .gitignore
├── blink/     # Pico project (source only — no build/)
└── blink2/    # STM32F446RE project (source only — no build/)
```

## One-time Pi setup

```bash
# Toolchain
sudo apt update
sudo apt install -y build-essential cmake make git openocd \
  gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi

# Pico SDK
cd ~
wget https://raw.githubusercontent.com/raspberrypi/pico-setup/master/pico_setup.sh
chmod +x pico_setup.sh
./pico_setup.sh
sudo reboot
```

Register the Pi as a self-hosted runner: repo **Settings → Actions → Runners → New self-hosted runner**, then on the Pi:

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
# paste the download/config commands shown on that GitHub page
./config.sh --url https://github.com/dprime999/HILTester --token <TOKEN>
sudo ./svc.sh install
sudo ./svc.sh start
```

## Pico: enable hands-free flashing

New/fresh Pico → one manual BOOTSEL flash first (hold BOOTSEL while plugging in USB). To make every flash after that hands-free, in `CMakeLists.txt`:

```cmake
pico_enable_stdio_usb(blink 1)
```

...and call `stdio_init_all()` in `main()`. After that, `picotool load -f -v file.uf2` reboots the board into BOOTSEL automatically — no button needed.

Check the board is visible: `lsusb` on the Pi.

## Day-to-day workflow

```bash
git pull --rebase origin main
# edit blink/blink.c or blink2/ source
git add blink blink2
git commit -m "Update firmware"
git push origin main
```

Push → Actions builds both projects on the Pi → flashes Pico, then STM32F446RE → done in ~20s. Check the **Actions** tab for status and the compiled `.uf2`/`.elf` artifact.

## .gitignore essentials

```
build/
**/build/
CMakeCache.txt
compile_commands.json
*.ninja_log
.vscode/
```

## Troubleshooting

| Symptom | Check |
|---|---|
| Runner offline | `cd ~/actions-runner && sudo ./svc.sh status` |
| Pico not found | `lsusb`; if never flashed with USB-stdio firmware, do one manual BOOTSEL flash |
| openocd can't claim ST-Link | udev rules + user in `plugdev` group |
| Push rejected | `git fetch origin && git pull --rebase origin main` |

**Full setup guide:** see `Raspberry_Pi_GitHub_Actions_Pico_STM32_Deployment_Guide.docx`
