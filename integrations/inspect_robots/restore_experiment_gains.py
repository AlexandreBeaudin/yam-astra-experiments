#!/usr/bin/env python3
"""Verify backups, or restore on-disk gains after normal station shutdown."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', choices=['initial', 'prior'], default='initial',
                        help='initial: before all tuning; prior: supplied 250/400 gains')
    parser.add_argument('--restore', action='store_true',
                        help='Write verified backups; use after normal hardware-owner shutdown')
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name('gain-backups-20261112.json').read_text())
    # Verify every backup before writing either active configuration.
    for relative, expected in manifest.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Backup checksum mismatch: {relative}')
    suffix = '.before-20261112' if args.baseline == 'initial' else '.before-large-motion-20261112'
    config = ROOT / 'YAM/i2rt/i2rt/robots/config'
    for name in ('yam_pro.yml', 'linear_4310.yml'):
        source, target = config / (name + suffix), config / name
        if args.restore:
            shutil.copyfile(source, target)
        print(f'{"Restored" if args.restore else "Verified"}: {source.relative_to(ROOT)}')
    print('Files only: a running daemon retains its loaded gains. Use normal station startup after restoration.')


if __name__ == '__main__':
    main()
