#!/usr/bin/env python3
"""Build a self-contained, first-time-provisioning HILTester release locally.

Run at the HILTester repository root. Requires Docker with Linux ARM64 support.
Does not build images: optionally pulls a prebuilt image then docker-save's it.
"""
from __future__ import annotations
import argparse
import fnmatch
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXCLUDE_DIRS = {'.git', '.venv', 'venv', 'releases', '__pycache__', '.pytest_cache',
                '.mypy_cache', '.ruff_cache', 'build', 'dist', 'node_modules', '.hiltester',
                '.idea', '.vscode', 'starter-extracted'}
EXCLUDE_FILES = {'.env', '.env.local', 'credentials.json', 'secrets.json', 'id_ed25519',
                 'id_rsa', 'known_hosts', 'runner.env', 'starter.tar.gz'}
EXCLUDE_PATTERNS = ('*.pem', '*.key', '*.p12', '*.pfx', '*.pyc', '*.log', '*.uf2',
                    '*.elf', '*.bin', '*.img', '*.img.xz', '*.db', '*.sqlite', '.env.*')


def safe_file(rel: Path) -> bool:
    return not (any(part in EXCLUDE_DIRS for part in rel.parts)
                or rel.name in EXCLUDE_FILES
                or any(fnmatch.fnmatch(rel.name, pat) for pat in EXCLUDE_PATTERNS))


def add_tree(source: Path, dest: Path) -> int:
    count = 0
    for file in sorted(source.rglob('*')):
        rel = file.relative_to(source)
        if not safe_file(rel) or file.is_symlink() or not file.is_file():
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, out)
        count += 1
    return count


def invoke(*command: str) -> str:
    p = subprocess.run(list(command), text=True, capture_output=True)
    if p.returncode:
        raise RuntimeError(f"Command failed: {' '.join(command[:3])}: {p.stderr[-1500:]}")
    return p.stdout.strip()


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as f:
        for buf in iter(lambda: f.read(1024 * 1024), b''):
            result.update(buf)
    return result.hexdigest()


def make_starter(tmp: Path) -> Path:
    stage = tmp / 'starter-stage'
    stage.mkdir()
    existing = ROOT / 'starter'
    if existing.is_dir() and any(p.is_file() for p in existing.rglob('*')):
        add_tree(existing, stage)
    else:
        # Build only the user-facing starter, not HILTester developer backends.
        for name in ('blink', 'blink2'):
            if (ROOT / name).is_dir():
                add_tree(ROOT / name, stage / name)
        for name in ('Dockerfile', 'entrypoint.sh', 'compose.yaml', 'docker-compose.yml',
                     'docker-compose.yaml', 'compose.yml', 'README.md', '.dockerignore'):
            f = ROOT / name
            if f.is_file() and not f.is_symlink():
                (stage / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, stage / name)
        flow = ROOT / '.github/workflows/main.yml'
        if flow.is_file():
            target = stage / '.github/workflows/main.yml'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(flow, target)
    # The approved flash trigger overlay is always included.
    overlay = ROOT / 'starter_overrides/.github/workflows/main.yml'
    if overlay.is_file():
        dst = stage / '.github/workflows/main.yml'
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(overlay, dst)
    if not (stage / '.github/workflows/main.yml').exists():
        raise RuntimeError('starter main.yml missing; add the starter workflow first')
    if not any(stage.glob('*')):
        raise RuntimeError('starter is empty')
    archive = tmp / 'starter.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for f in sorted(stage.rglob('*')):
            if f.is_file():
                tar.add(f, f.relative_to(stage).as_posix(), recursive=False)
    return archive


def make_image(tmp: Path, tag: str, source: str, skip_pull: bool) -> Path:
    if not skip_pull:
        print(f'[release] Pulling existing ARM64 image {source}', flush=True)
        invoke('docker', 'pull', '--platform', 'linux/arm64', source)
    info = json.loads(invoke('docker', 'image', 'inspect', source))[0]
    if info.get('Architecture') != 'arm64' or info.get('Os') != 'linux':
        raise RuntimeError(f'Expected linux/arm64 image, got {info.get("Os")}/{info.get("Architecture")}')
    invoke('docker', 'tag', source, tag)
    raw = tmp / 'image.tar'
    invoke('docker', 'save', '--platform', 'linux/arm64', '-o', str(raw), tag)
    dest = tmp / 'runner-image.tar.gz'
    print('[release] Compressing Docker image archive (this can take time)', flush=True)
    with raw.open('rb') as inp, dest.open('wb') as output:
        with gzip.GzipFile(filename='', fileobj=output, mode='wb', compresslevel=3, mtime=0) as gz:
            shutil.copyfileobj(inp, gz, length=1024*1024)
    raw.unlink()
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description='Create a complete HILTester provisioning release folder')
    parser.add_argument('--version', required=True)
    parser.add_argument('--comment', default='')
    parser.add_argument('--image', default='ghcr.io/dprime999/hiltester-runner:latest')
    parser.add_argument('--skip-pull', action='store_true', help='use already downloaded Docker image (no GHCR lookup)')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', args.version):
        parser.error('version must be alphanumeric with dots, hyphens, underscores')
    releases = ROOT / 'releases'
    releases.mkdir(exist_ok=True)
    dest = releases / ('HILTester-v' + args.version)
    if dest.exists():
        raise SystemExit(f'Release folder already exists, refusing overwrite: {dest}')
    stage = Path(tempfile.mkdtemp(prefix='.hiltester-release-', dir=releases))
    try:
        runtime = stage / 'interface'
        if not (ROOT / 'interface/backend/run_all.py').is_file():
            raise RuntimeError('run_all.py missing: apply the complete combined patch first')
        add_tree(ROOT / 'interface', runtime)
        if (ROOT / 'installation').is_dir():
            add_tree(ROOT / 'installation', stage / 'installation')
        # Include required runtime assets outside interface if present.
        starter = make_starter(stage)
        tag = 'hiltester-runner:release-' + args.version.lower()
        image_archive = make_image(stage, tag, args.image, args.skip_pull)
        shutil.copy2(ROOT / 'release_launcher.py', stage / 'launch.py')
        manifest = {
            'schema_version': 1, 'version': args.version, 'comment': args.comment,
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'starter': {'archive': 'starter.tar.gz', 'sha256': sha256(starter)},
            'runner_image': {'reference': tag, 'archive': 'runner-image.tar.gz',
                             'sha256': sha256(image_archive), 'platform': 'linux/arm64'},
        }
        (stage / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        (stage / 'README.md').write_text(
            '# HILTester release ' + args.version + '\n\n' +
            'Unzip/extract this folder on a PC with Python 3.10+, git, SSH and Tailscale.\n\n' +
            'Run `python launch.py deploy-test` to provision a new Pi and run the firmware workflow.\n' +
            'Normal `deploy` is available for provisioning without flashing firmware.\n' +
            'For a repeat dev test use `python interface/backend/clean.py --project demo --repo OWNER/REPO --pi-all --reset-progress` first.\n' +
            'Requires network for Raspberry Pi OS, GitHub, Tailscale and Docker installation. The runner image does not need GHCR.\n',
            encoding='utf-8')
        total = sum(f.stat().st_size for f in stage.rglob('*') if f.is_file())
        stage.rename(dest)
        print(f'[release] Created: {dest}\n[release] Size: {total / 1024**2:.2f} MiB ({total:,} bytes)')
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


if __name__ == '__main__':
    main()
