#!/usr/bin/env python3
"""Prepare a private-repository source snapshot; never authenticates or uploads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

SOURCE = Path(__file__).resolve().parents[1]
SKIP_PARTS = {'.git', '.venv', '__pycache__', '.pytest_cache', '.ruff_cache', 'node_modules', '.env'}


def prepare(destination):
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f'Refusing to overwrite existing path: {destination}')
    destination.mkdir(parents=True)
    skipped = []

    def copy(path, relative):
        if any(part in SKIP_PARTS for part in relative.parts) or path.name == '.gitmodules':
            return
        public_ca = relative == Path('integrations/inspect_robots/runtime-deps/certifi/cacert.pem')
        if (path.suffix in {'.pyc', '.pem', '.key', '.crt'} and not public_ca) or path.name.startswith('.env'):
            skipped.append(str(relative))
            return
        target = destination / relative
        if path.is_symlink():
            # Do not package links to another computer's data or credentials.
            skipped.append(str(relative) + ' (symlink)')
            return
        if not path.is_file():
            return
        if path.stat().st_size > 90_000_000:
            raise ValueError(f'File too large for normal Git: {relative}')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    def tracked(repo, prefix):
        raw = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '--stage', '-z'])
        for entry in raw.split(b'\0'):
            if not entry:
                continue
            meta, name = entry.decode().split('\t', 1)
            path = repo / name
            relative = prefix / name
            if meta.split()[0] == '160000':
                if (path / '.git').exists():
                    tracked(path, relative)
                else:
                    skipped.append(str(relative) + ' (uninitialized optional submodule)')
            else:
                copy(path, relative)

    tracked(SOURCE / 'YAM', Path('YAM'))
    integration = SOURCE / 'integrations/inspect_robots'
    for name in ('vendor', 'vendor-agent', 'runtime-deps', 'replay_assets'):
        for path in (integration / name).rglob('*'):
            copy(path, path.relative_to(SOURCE))
    for path in integration.iterdir():
        if path.is_file() and (path.suffix in {'.py', '.md', '.html'} or path.name in {
            'astra_exploration.txt', 'upstream-lock.json', 'runtime-deps-lock.json'}):
            copy(path, path.relative_to(SOURCE))
    for name in ('preview_cameras.py', 'ASTRA_PICK_AND_PLACE_GUIDE.md',
                 'yam3-sunday/capture_observations.py', 'lab-transfer/prepare_source.py',
                 'lab-transfer/README.md'):
        copy(SOURCE / name, Path(name))
    for name in ('configure_local_station.py', 'test_transfer.py', 'stage_source.py'):
        copy(SOURCE / 'lab-transfer' / name, Path('lab-transfer') / name)
    copy(SOURCE / 'profiles/shurui-mcap.yaml', Path('profiles/shurui-mcap.yaml.example'))
    for path in (SOURCE / 'YAM/docs').glob('PD_GAINS_*.md'):
        copy(path, path.relative_to(SOURCE))
    (destination / '.gitignore').write_text('''# Local credentials, configuration and generated evidence
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
.env.*
!.env.example
*.pem
!integrations/inspect_robots/runtime-deps/certifi/cacert.pem
*.key
*.crt
profiles/*.yaml
*.local.json
**/astra-yam3-run-*/
**/.replay-render-*/
recordings/
data/
*.mp4
*.mcap
*.bag
*.bak
''')
    (destination / 'README.md').write_text('''# Shared YAM Astra experiments

Start with [the lab transfer guide](lab-transfer/README.md).
This is a flattened source snapshot, including local tracked changes. It is not
a clone of the upstream YAM repository. Optional uninitialized submodules,
credentials, station profiles, virtual environments and run data are excluded.
The original YAM documentation includes commands for its original submodule
layout; use the installation commands in the transfer guide for this snapshot.

Each station must use its own arm calibration and camera configuration. The
included motor defaults reflect the source workstation: review
`YAM/docs/PD_GAINS_20261112.md` against the destination robot before enabling it.
Historical startup-guide paths are examples for YAM3, not destination settings.
''')
    hashes = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in destination.rglob('*') if p.is_file()}
    manifest = dict(source=str(SOURCE), yam_commit=subprocess.check_output(
        ['git', '-C', str(SOURCE/'YAM'), 'rev-parse', 'HEAD'], text=True).strip(),
        note='Working-tree bytes copied, including uncommitted tracked changes; not just upstream HEAD.',
        skipped=skipped, files=hashes)
    (destination/'SOURCE_SNAPSHOT.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(dict(destination=str(destination), files=len(hashes),
                         bytes=sum(p.stat().st_size for p in destination.rglob('*') if p.is_file()), skipped=skipped), indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    prepare(parser.parse_args().out)
