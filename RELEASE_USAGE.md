# HILTester local, offline-image developer releases

## Purpose

`release.py` packages a first-time HILTester Pi provisioning application that can run from its own extracted folder. **The Pi never needs GHCR to obtain the HILTester runner image.** Release creation may use GHCR to fetch the prebuilt Linux ARM64 runner image. `release.py` **does not build a Docker image**.

The user's Pi still needs Internet connectivity for OS bootstrap, Tailscale, GitHub runner registration, fetching the user's starter repository, and potentially installing Docker. Bundling the Docker image removes only the GHCR dependency for that image.

## Installation (developer)

Copy these patch files to matching paths in the HILTester developer repository:

- `release.py` and `release_launcher.py` at the root (beside `interface/`, `blink/`).
- `interface/backend/github_backend.py`, `run_all.py`, `deploy_backend.py` to their existing locations.
- `starter_overrides/.github/workflows/main.yml` to the same path in the developer repo.

**Important:** `release.py` prefers an existing populated `starter/` directory as the template source. Otherwise it uses `blink/`, `blink2/`, the root Compose/Docker assets, and the `main.yml` starter override. Inspect the generated `starter.tar.gz` to confirm it includes every file your firmware projects need. Ensure the starter Compose file has service name `github-runner`.

Recommended developer prerequisites: Python 3.10+, Docker Engine/Desktop with Linux containers and ARM64 image support, Git, and working GHCR access if you are using the default `--image` source.

## Build a new release

From your HILTester root:

```powershell
python release.py --version 0.1.0 --comment "Initial offline-image provisioning test"
```

Creates `releases/` if missing and then creates:

```text
HILTester/
  releases/
    HILTester-v0.1.0/
      launch.py
      release.json
      README.md
      starter.tar.gz
      runner-image.tar.gz
      interface/...
      installation/...      # if present in your source repository
```

The script pulls the prebuilt `ghcr.io/dprime999/hiltester-runner:latest` **on the developer's machine only**, confirms Linux ARM64, creates a release-specific image tag, saves it with Docker, compresses it, and copies it into the release. To use an image already cached on the developer PC:

```powershell
python release.py --version 0.1.1 --comment "Cached image test" --skip-pull
```

For a different published image: add `--image owner/image:tag`. Prefer an immutable digest when distributing tested releases. The script refuses to overwrite an existing release version. Large image archives stay in the local `releases/` folder; add `releases/` to `.gitignore`.

## Test *as an end user* (clean first-time Pi)

1. Copy the complete `releases/HILTester-v0.1.0/` directory to a separate location, simulating an extracted download. Do not run it against your developer source tree.
2. Use a fresh target GitHub repository, a Pi 5 with removable boot media, and appropriate GitHub/Tailscale authentication. Have the Pico/STM32 attached before firmware testing.
3. Open PowerShell in the extracted release folder and run:

   ```powershell
   python launch.py deploy-test
   ```

4. Select a new project name, fresh Pi and empty GitHub repository when prompted. The existing flow should create one Git commit with the starter files, flash Pi OS, establish Tailscale/SSH, install Docker, stream the bundled image, run `docker load`, start Compose **without pulling the runner image**, register the runner and dispatch `main.yml`.
5. The firmware workflow is allowed one GitHub **Re-run all jobs** if the first completed attempt fails. A workflow that remains queued is not automatically canceled/rerun.
6. Confirm Docker image and runner state on the Pi:

   ```bash
   sudo docker images
   sudo docker ps
   ```

7. A successful test has the Pi runner online and the final firmware workflow concluded successfully. For evidence that it did not fetch GHCR, confirm the deploy logs say the bundled image was loaded and the Compose command uses `--pull never`.

## Developer cleanup and repeat tests

Run from the *extracted release* folder:

```powershell
python interface/backend/clean.py --project demo --repo dprime999/HILStarter --pi-all --reset-progress
python launch.py deploy-test
```

When prompted, answer **yes** for an already provisioned Pi and populated GitHub repository. The normal Pi cleanup preserves Docker and its image cache, so the subsequent deployment should skip image streaming when the release image is still present. `--github-clean` is destructive and is not included above.

To simulate another first-time user, use a truly fresh Pi boot drive and a fresh GitHub repository. Normal cleanup does **not** simulate that condition.

## What is still online / what is not yet complete

- No Windows `.exe`/installer, VS Code extension, GUI or agent features are included.
- No image build is performed by `release.py`.
- Docker may need to be installed on the Pi using the existing network-based bootstrap.
- First-boot Raspberry Pi OS and Tailscale provisioning remain network-based.
- Runner self-update may need GitHub connectivity after release creation; a bundled release cannot guarantee indefinite runner protocol compatibility.
- Only the Docker image transfer is GHCR-independent on the end-user Pi.
- The image archive is transferred over SSH and loaded by Docker; this is not guaranteed faster than a registry pull. Compare clean-device runs to measure.

## Project size

`release.py` prints the actual release folder size upon success. Most of the size is `runner-image.tar.gz`; without an exported Docker image, an exact total cannot be predicted. For a quick Windows measurement after building:

```powershell
(Get-ChildItem -Recurse -File releases/HILTester-v0.1.0 | Measure-Object Length -Sum).Sum / 1MB
```

The image archive is already compressed; do not expect a second ZIP download to shrink it much further.
