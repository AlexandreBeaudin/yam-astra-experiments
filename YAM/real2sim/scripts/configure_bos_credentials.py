#!/usr/bin/env python3
"""Interactively save BOS credentials without placing secrets in shell history."""
import getpass
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from real2sim.storage.bos_auth import save_credentials

if __name__ == '__main__':
    if not sys.stdin.isatty():
        raise SystemExit('Run in a local interactive terminal; do not pass secrets as arguments.')
    ak = getpass.getpass('BOS Access Key ID (hidden): ')
    sk = getpass.getpass('BOS Secret Access Key (hidden): ')
    try:
        print(f'Saved credentials: {save_credentials(ak, sk)} (mode 600)')
    except ValueError as error:
        raise SystemExit(str(error)) from None
