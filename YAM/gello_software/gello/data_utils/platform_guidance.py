"""Immutable platform identity and reference image snapshot at episode start."""
import json
from pathlib import Path


class PlatformGuidance:
    def __init__(self, path):
        self.path = Path(path)

    def snapshot(self):
        data = json.loads(self.path.read_text())
        if data.get('source') != 'rdp' or data.get('guidance_kind') != 'reference':
            raise ValueError('Invalid RDP reference snapshot')
        return {k: v for k, v in data.items() if not k.startswith("_")}
