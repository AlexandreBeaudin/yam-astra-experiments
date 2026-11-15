#!/usr/bin/env python3
"""Stage only the prepared source manifest, preserving upstream tracked/ignored files."""
import json
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[1]
manifest=root/'SOURCE_SNAPSHOT.json'
files=list(json.loads(manifest.read_text())['files'])+['SOURCE_SNAPSHOT.json']
for name in files:
    path=(root/name).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError(f'Invalid or missing source-manifest path: {name}')
# Some files were tracked upstream despite upstream .gitignore patterns.
# Force only this explicit source allowlist, never the whole working directory.
for offset in range(0,len(files),100):
    subprocess.run(['git','-C',str(root),'add','-f','--',*files[offset:offset+100]],check=True)
print(f'Staged {len(files)} manifest-listed source files. Review git diff --cached --stat before committing.')
