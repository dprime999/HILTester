# HILTester release + minimal reliability patch

This is a **patch overlay**, not the complete HILTester source tree. Copy the included files into your existing HILTester repository, preserving relative paths.

## Includes

- `release.py`: new developer-side local release generator. Creates `releases/` beside `interface/` if missing and refuses to overwrite an existing version.
- `release_launcher.py`: copied as `launch.py` into each generated release.
- `interface/backend/github_backend.py`: initialize the entire starter via a single Git commit.
- `interface/backend/run_all.py`: one GitHub **Re-run all jobs** retry when a completed workflow attempt fails, and automatic bundled-image detection when invoked from a release folder.
- `interface/backend/deploy_backend.py`: when given a bundled image archive, checks Pi cache, loads the archived image over SSH if absent, and starts Compose with `pull_policy: never` and `--pull never`.
- `starter_overrides/.github/workflows/main.yml`: on push, firmware test only when `blink/blink.c` or `blink2/Core/Src/main.c` changes; manual dispatch still available.
- `RELEASE_USAGE.md`: developer build instructions, first-time user-style acceptance test, and repeat `clean → deploy-test` commands.

## Important limitations

- The release is **self-contained for the prebuilt runner image**, not entirely offline provisioning: Docker/OS/Tailscale/GitHub may still require Internet.
- The release creator can pull from GHCR *on the developer machine*; the user's Pi does not need GHCR to load the bundled image.
- Users are not required to install Docker on Windows: only the developer needs Docker for `release.py`. Users do need a suitable Python installation and SSH/Tailscale/Imager prerequisites on the host.
- No firmware toolchain image build takes place at release time or on the user's Pi.
- **Only GitHub runner image caching and offline load are implemented; no benchmarking or first-boot speed guarantee.**
- Existing project files beyond the patch must be present in the developer source tree when running `release.py`.
- Release will include only filtered files from the developer's existing `starter/` if available. Check the tar archive contents before distributing.
- No real hardware or GitHub run was executed during preparation of this patch.
