from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import base64
import urllib.error
import urllib.request
from urllib.parse import quote
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from common import BackendError, print_json, run, save_state

# Local files and generated output are NEVER distributed in the starter package.
EXCLUDED_DIRS = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache',
                 '.ruff_cache', 'node_modules', '.tox', '.idea', '.vscode', 'build',
                 'dist', '.hiltester', '.terraform', 'starter', 'starter-extracted'}
EXCLUDED_FILES = {'.env', '.env.local', '.env.production', 'id_rsa', 'id_ed25519',
                  'known_hosts', 'credentials', 'credentials.json', 'secrets.json',
                  'starter.tar.gz'}
EXCLUDED_GLOBS = ('*.pem', '*.key', '*.p12', '*.pfx', '*.pyc', '*.swp', '*.log',
                  '*.uf2', '*.elf', '*.bin', '*.img', '*.img.xz', '*.zip',
                  '*.tar', '*.tar.gz', '*.sqlite', '*.db', '.env.*')


def progress(stage: str, done: int, total: int) -> None:
    percent = round(done * 100 / total) if total else 100
    print(f'[starter:{stage}] {done}/{total} ({percent}%)', file=sys.stderr, flush=True)


def normalize_repo(value: str) -> str:
    value = value.strip()
    if value.startswith('git@') or value.startswith('https://') or value.startswith('http://'):
        return value
    if value.count('/') == 1:
        return f'https://github.com/{value}.git'
    raise BackendError('repo must be owner/name or a git URL')


def repo_label(url: str) -> str:
    if url.startswith('git@'):
        path = url.split(':', 1)[1]
    else:
        path = urlparse(url).path.lstrip('/')
    return path.removesuffix('.git')


def discover_default_branch(repo: str) -> str:
    result = run(['git', 'ls-remote', '--symref', repo, 'HEAD'], timeout=45)
    if not result.ok:
        raise BackendError(result.stderr or 'could not read repository HEAD')
    for line in result.stdout.splitlines():
        match = re.match(r'ref:\s+refs/heads/([^\s]+)\s+HEAD$', line)
        if match:
            return match.group(1)
    raise BackendError('could not determine repository default branch (empty repositories have no HEAD yet)')


def verify_branch(repo: str, branch: str) -> tuple[bool, str]:
    result = run(['git', 'ls-remote', '--exit-code', '--heads', repo, branch], timeout=45)
    return result.ok and bool(result.stdout.strip()), result.stderr


def _excluded(rel: Path) -> bool:
    name = rel.name
    return (any(part in EXCLUDED_DIRS for part in rel.parts)
            or name in EXCLUDED_FILES
            or any(fnmatch.fnmatch(name, pat) for pat in EXCLUDED_GLOBS))


def _files_to_copy(source: Path, starter: Path, archive: Path) -> list[tuple[Path, Path]]:
    if not source.is_dir():
        raise BackendError(f'source project directory not found: {source}')
    output = []
    for root, dirs, files in os.walk(source, followlinks=False):
        base = Path(root)
        dirs[:] = sorted(d for d in dirs if not _excluded((base / d).relative_to(source))
                         and not (base / d).is_symlink()
                         and (base / d).resolve() != starter)
        for name in sorted(files):
            path = base / name
            rel = path.relative_to(source)
            if path.is_symlink() or _excluded(rel) or path.resolve() == archive:
                continue
            if path.is_file():
                output.append((path, rel))
    return output


def copy_starter(source: Path, starter: Path, archive: Path) -> dict:
    source, starter, archive = source.resolve(), starter.resolve(), archive.resolve()
    if starter == source or not starter.is_relative_to(source):
        raise BackendError('starter directory must be a child of the source directory')
    if archive.is_relative_to(starter):
        raise BackendError('archive must be outside starter/ to prevent self-inclusion')
    files = _files_to_copy(source, starter, archive)
    if not files:
        raise BackendError('no eligible source files found')
    # Build in a temporary sibling directory and swap only once copying completes.
    with tempfile.TemporaryDirectory(prefix='.starter-stage-', dir=starter.parent) as temp:
        stage = Path(temp) / 'starter'
        stage.mkdir()
        for i, (path, rel) in enumerate(files, 1):
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if i == 1 or i % 25 == 0 or i == len(files):
                progress('copy', i, len(files))
        backup = starter.parent / '.starter-backup'
        if backup.exists():
            raise BackendError(f'remove stale backup before retrying: {backup}')
        if starter.exists():
            starter.rename(backup)
        try:
            stage.rename(starter)
        except Exception:
            if backup.exists():
                backup.rename(starter)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    return {'status': 'copied', 'starter_dir': str(starter), 'files': len(files)}


def compress_starter(starter: Path, archive: Path) -> dict:
    if not starter.is_dir():
        raise BackendError(f'starter not found; run --copy-starter first: {starter}')
    if archive.resolve().is_relative_to(starter.resolve()):
        raise BackendError('archive must be outside starter/')
    files = sorted(p for p in starter.rglob('*') if p.is_file() and not p.is_symlink())
    if not files:
        raise BackendError('starter is empty')
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.starter-archive-', suffix='.tar.gz',
                                     dir=archive.parent, delete=False) as tmp:
        temp_archive = Path(tmp.name)
    try:
        with tarfile.open(temp_archive, 'w:gz') as tar:
            for i, path in enumerate(files, 1):
                tar.add(path, arcname=path.relative_to(starter).as_posix(), recursive=False)
                if i == 1 or i % 25 == 0 or i == len(files):
                    progress('compress', i, len(files))
        temp_archive.replace(archive)
    finally:
        temp_archive.unlink(missing_ok=True)
    return {'status': 'compressed', 'archive': str(archive), 'files': len(files),
            'size_bytes': archive.stat().st_size}


def extract_starter(archive: Path, destination: Path) -> dict:
    """Extract a trusted starter safely; no links, traversal, or overwritten files."""
    if not archive.is_file():
        raise BackendError(f'archive not found: {archive}')
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        regular = [m for m in members if m.isfile()]
        for m in members:
            parts = PurePosixPath(m.name)
            if (parts.is_absolute() or '..' in parts.parts or not parts.parts
                    or not (m.isfile() or m.isdir())):
                raise BackendError(f'unsafe archive entry: {m.name}')
            target = destination.joinpath(*parts.parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise BackendError(f'archive entry leaves destination: {m.name}')
            if target.exists() and m.isfile():
                raise BackendError(f'refusing to overwrite file: {target}')
        for i, m in enumerate(regular, 1):
            target = destination.joinpath(*PurePosixPath(m.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(m) as source, target.open('xb') as out:
                shutil.copyfileobj(source, out)
            if i == 1 or i % 25 == 0 or i == len(regular):
                progress('extract', i, len(regular))
    return {'status': 'extracted', 'destination': str(destination), 'files': len(regular)}


def _github_request(method: str, endpoint: str, token: str, payload: dict | None = None):
    url = 'https://api.github.com' + endpoint
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'HILTester-starter/1.0',
        **({'Content-Type': 'application/json'} if data is not None else {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode('utf-8', errors='replace')
        raise BackendError(f'GitHub API {method} {endpoint} returned HTTP {exc.code}: {detail}') from exc
    except urllib.error.URLError as exc:
        raise BackendError(f'GitHub API connection failed: {exc.reason}') from exc


def _github_repo_parts(repo: str) -> tuple[str, str]:
    label = repo_label(normalize_repo(repo))
    parts = label.split('/')
    if len(parts) != 2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]+', x) for x in parts):
        raise BackendError('GitHub repository must be an owner/name on github.com')
    if not normalize_repo(repo).startswith(('https://github.com/', 'git@github.com:')):
        raise BackendError('only github.com repositories are supported for initialization')
    return parts[0], parts[1]


def _archive_files(archive: Path) -> list[tuple[str, bytes]]:
    if not archive.is_file():
        raise BackendError(f'starter archive not found: {archive}')
    files = []
    names = set()
    total_size = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            name = member.name
            path = PurePosixPath(name)
            if (not name or name.startswith('/') or name.startswith('./') or '\\' in name
                    or '..' in path.parts or not path.parts or ':' in path.parts[0]
                    or any(part in ('', '.') for part in name.split('/'))
                    or not (member.isfile() or member.isdir())):
                raise BackendError(f'unsafe starter archive entry: {name}')
            if member.isdir():
                continue
            if name in names:
                raise BackendError(f'duplicate archive path: {name}')
            names.add(name)
            if _excluded(Path(*path.parts)):
                raise BackendError(f'archive contains excluded/sensitive path: {name}')
            if member.size > 90_000_000 or total_size + member.size > 250_000_000:
                raise BackendError('starter archive exceeds upload safety limits')
            source = tar.extractfile(member)
            if source is None:
                raise BackendError(f'cannot read archive entry: {name}')
            content = source.read(member.size + 1)
            if len(content) != member.size:
                raise BackendError(f'archive entry has incorrect size: {name}')
            total_size += member.size
            files.append((name, content))
    if not files:
        raise BackendError('starter archive has no files')
    return sorted(files)


def init_repository(repo: str, branch: str | None, archive: Path, token: str,
                    dry_run: bool = False) -> dict:
    """Upload starter files via GitHub REST API; does not clone or use local git."""
    owner, name = _github_repo_parts(repo)
    prefix = f'/repos/{quote(owner)}/{quote(name)}'
    progress('upload', 0, 1)
    info = _github_request('GET', prefix, token)
    if info.get('archived') or info.get('disabled'):
        raise BackendError('repository is archived or disabled')
    default_branch = info.get('default_branch') or 'main'
    selected = branch or default_branch
    if selected != default_branch:
        raise BackendError('initializing an empty repository requires its default branch: ' + default_branch)
    branches = _github_request('GET', prefix + '/branches?per_page=100', token)
    if not isinstance(branches, list):
        raise BackendError('GitHub returned an unexpected branches response')
    branch_exists = any(item.get('name') == selected for item in branches)
    if branches and not branch_exists:
        raise BackendError(f'default branch {selected!r} was not found')
    files = _archive_files(archive)

    # Preserve existing files except a root README.md, which the starter may replace.
    # Contents API requires the existing blob SHA for that replacement.
    conflicts = []
    replace_sha = {}
    if branch_exists:
        # Git Trees API returns paths recursively in one call (no local clone).
        ref = _github_request('GET', prefix + '/git/ref/heads/' + quote(selected, safe=''), token)
        sha = ref['object']['sha']
        commit = _github_request('GET', prefix + '/git/commits/' + sha, token)
        tree_sha = commit['tree']['sha']
        tree = _github_request('GET', prefix + '/git/trees/' + tree_sha + '?recursive=1', token)
        if tree.get('truncated'):
            raise BackendError('repository tree is too large to check safely; refusing initialization')
        existing = {item['path']: item for item in tree.get('tree', [])}
        for path, _ in files:
            parts = path.split('/')
            if path == 'README.md' and path in existing and existing[path].get('type') == 'blob':
                replace_sha[path] = existing[path]['sha']
                continue
            if path in existing or any((existing.get('/'.join(parts[:i])) is not None and existing['/'.join(parts[:i])].get('type') != 'tree') for i in range(1, len(parts))):
                conflicts.append(path)
        # A starter file must not replace an existing directory either.
        if conflicts:
            raise BackendError('starter conflicts with existing repository paths (no files changed): '
                               + ', '.join(conflicts[:12])
                               + (' ...' if len(conflicts) > 12 else ''))
    if dry_run:
        progress('upload', 1, 1)
        return {'status': 'ready_to_upload', 'repo': f'{owner}/{name}', 'branch': selected,
                'files': len(files), 'archive': str(archive), 'no_clone': True,
                'branch_exists': branch_exists, 'conflicts': [],
                'readme_replacements': list(replace_sha)}
    # First upload creates the initial branch on completely empty GitHub repos.
    # When main exists, every upload explicitly targets it.
    # Put README last so a failure on other files leaves the original README intact.
    files.sort(key=lambda item: item[0] == 'README.md')
    for i, (path, content) in enumerate(files, 1):
        endpoint = prefix + '/contents/' + '/'.join(quote(part, safe='') for part in path.split('/'))
        payload = {'message': f'Initialize HILTester starter: {path}',
                   'content': base64.b64encode(content).decode('ascii')}
        if branch_exists or i > 1:
            payload['branch'] = selected
        if path in replace_sha:
            payload['sha'] = replace_sha[path]
        _github_request('PUT', endpoint, token, payload)
        if i == 1 or i % 5 == 0 or i == len(files):
            progress('upload', i, len(files))
    return {'status': 'uploaded', 'repo': f'{owner}/{name}', 'branch': selected,
            'files': len(files), 'no_clone': True, 'previous_branch_exists': branch_exists,
            'readme_replacements': list(replace_sha),
            'note': 'Direct GitHub API upload; existing non-README files preserved'}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Verify GitHub, refresh a starter snapshot, archive it, or initialize a blank repo.')
    p.add_argument('--project', required=True)
    p.add_argument('--repo', help='owner/name or clone URL; required for repository checks and --init-starter')
    p.add_argument('--branch', help='branch to verify or create (default: main for initialization)')
    p.add_argument('--token-env', default='GITHUB_TOKEN', help='environment variable containing GitHub API token (not persisted)')
    p.add_argument('--skip-check', action='store_true')
    tasks = p.add_mutually_exclusive_group()
    tasks.add_argument('--copy-starter', action='store_true', help='refresh starter/ with a filtered copy of the project')
    tasks.add_argument('--compress-starter', action='store_true', help='create starter.tar.gz from starter/')
    tasks.add_argument('--extract-starter', action='store_true', help='uncompress starter.tar.gz into a local directory (no GitHub upload)')
    tasks.add_argument('--init-starter', action='store_true', help='upload starter to GitHub; replace existing root README.md, preserve all other files')
    p.add_argument('--source', type=Path, default=Path.cwd(), help='top-level source project directory (default: working directory)')
    p.add_argument('--archive', type=Path, help='starter archive path (default: <source>/starter.tar.gz)')
    p.add_argument('--destination', type=Path, help='destination for --extract-starter (default: <source>/starter-extracted)')
    p.add_argument('--dry-run', action='store_true', help='check conflicts and planned README replacement without uploading')
    return p


def main() -> int:
    args = parser().parse_args()
    source = args.source.expanduser().resolve()
    archive = (args.archive.expanduser().resolve() if args.archive else source / 'starter.tar.gz')
    starter = source / 'starter'
    if args.copy_starter:
        print_json(copy_starter(source, starter, archive))
        return 0
    if args.compress_starter:
        print_json(compress_starter(starter, archive))
        return 0
    if args.extract_starter:
        destination = (args.destination or source / 'starter-extracted').expanduser().resolve()
        print_json(extract_starter(archive, destination))
        return 0
    if args.init_starter:
        if not args.repo:
            raise BackendError('--init-starter requires --repo')
        token = os.environ.get(args.token_env)
        if not token:
            raise BackendError(f'GitHub API token missing from environment variable {args.token_env}')
        result = init_repository(args.repo, args.branch, archive, token, args.dry_run)
        save_state(args.project, 'starter_state', result)
        print_json(result)
        return 0
    if not args.repo:
        raise BackendError('--repo is required when verifying GitHub access')
    repo = normalize_repo(args.repo)
    token_present = bool(os.environ.get(args.token_env))
    branch = args.branch
    check_error = ''
    if not args.skip_check:
        try:
            branch = branch or discover_default_branch(repo)
            ok, check_error = verify_branch(repo, branch)
        except BackendError as exc:
            ok = False
            check_error = str(exc)
    else:
        branch = branch or 'HEAD'
        ok = True
    state = {
        'step': 'github', 'status': 'confirmed' if ok else 'access_failed',
        'repo': repo, 'repo_label': repo_label(repo), 'branch': branch,
        'branch_was_user_supplied': bool(args.branch), 'token_env': args.token_env,
        'token_present_at_check': token_present, 'credential_persisted': False,
        'check_error': check_error if not ok else '',
    }
    save_state(args.project, 'github_state', state)
    print_json(state)
    return 0 if ok else 3


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (BackendError, OSError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        print_json({'status': 'error', 'error': str(exc)})
        raise SystemExit(2)
