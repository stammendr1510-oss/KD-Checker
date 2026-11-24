"""Offline build identity; evaluated for each check, stored with its result."""
import os
from pathlib import Path
import subprocess


def build_metadata(version):
    def git(*args):
        try:
            return subprocess.check_output(['git', 'rev-parse', *args],
                cwd=Path(__file__).resolve().parent, stderr=subprocess.DEVNULL,
                timeout=2, text=True).strip()
        except (OSError, subprocess.SubprocessError):
            return ''
    return {'version': version,
            'branch': git('--abbrev-ref', 'HEAD') or os.getenv('KD_BUILD_BRANCH') or 'не определена',
            'build': git('--short', 'HEAD') or os.getenv('KD_BUILD_REF') or 'локальная сборка'}
