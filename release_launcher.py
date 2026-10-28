#!/usr/bin/env python3
"""Entry point included in every HILTester standalone release."""
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    if not (root / 'release.json').is_file():
        raise SystemExit('release.json missing; launch from an extracted HILTester release folder')
    run_all = root / 'interface/backend/run_all.py'
    if not run_all.is_file():
        raise SystemExit('Bundled run_all.py missing')
    raise SystemExit(subprocess.call([sys.executable, str(run_all), *sys.argv[1:]], cwd=root))

if __name__ == '__main__':
    main()
