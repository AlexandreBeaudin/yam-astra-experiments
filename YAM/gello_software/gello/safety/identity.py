"""Startup-time identity of the code and motor configuration owning the CAN buses."""

import hashlib
import json
import os
from pathlib import Path

from omegaconf import OmegaConf


def daemon_identity(repo_root, config_paths):
    root = Path(repo_root).resolve()
    digest = hashlib.sha256()
    sources = [root / "gello", root / "experiments/yam_safety_daemon.py",
               root.parent / "i2rt/i2rt"]
    for source in sources:
        paths = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in paths:
            if path.is_file() and path.suffix in {".py", ".xml", ".yml", ".yaml"} and not any(
                part in {"tests", "__pycache__"} for part in path.parts
            ):
                digest.update(str(path.relative_to(root.parent)).encode())
                digest.update(b"\0" + path.read_bytes() + b"\0")
    motors = {}
    for side, path in sorted(config_paths.items()):
        config = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
        motors[side] = config["robot"]
        nested = motors[side].get("config")
        if isinstance(nested, str):
            motors[side]["config"] = OmegaConf.to_container(OmegaConf.load(nested), resolve=True)
    return {"owner_uid": os.getuid(), "repo_root": str(root),
            "code_fingerprint": digest.hexdigest(),
            "config_fingerprint": hashlib.sha256(json.dumps(motors, sort_keys=True).encode()).hexdigest()}


def validate_daemon_identity(status, expected):
    for field, value in expected.items():
        if status.get(field) != value:
            raise RuntimeError(f"daemon {field} mismatch; safely stop the old daemon and restart (do not kill or take over)")
