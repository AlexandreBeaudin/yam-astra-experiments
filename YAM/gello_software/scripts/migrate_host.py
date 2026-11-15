#!/usr/bin/env python3
"""Migrate this workstation's calibrated profile and shared-device permissions.

Preview is the default. Apply with sudo and the rebuilt project Python.
No robot, CAN, camera, or daemon control commands are issued.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gello.command_profile import validate_motion_configuration
from gello.profile import load_profile


def acl_with_access(text: str, users: tuple[str, ...], *, directory: bool) -> str:
    """Keep existing effective permissions when widening the POSIX ACL mask."""
    entries: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        entry, _, comment = raw.partition("#")
        key, perms = entry.strip().rsplit(":", 1)
        if "effective:" in comment:
            perms = comment.split("effective:", 1)[1].strip()
        entries[key] = perms
    if directory and not any(key.startswith("default:") for key in entries):
        entries.update({"default:" + key: value for key, value in list(entries.items())})
    for user in users:
        entries[f"user:{user}"] = "rwx" if directory else "rw-"
        if directory:
            entries[f"default:user:{user}"] = "rwx"
    entries["mask:"] = "rwx" if directory else "rw-"
    # Preserve execute permission on pre-existing executable files/groups.
    if not directory and any("x" in value for key, value in entries.items() if key.startswith(("user:", "group:"))):
        entries["mask:"] = "rwx"
    if directory:
        entries["default:mask:"] = "rwx"
    return "\n".join(f"{key}:{value}" for key, value in entries.items()) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-user", required=True)
    parser.add_argument("--target-user", required=True)
    parser.add_argument("--apply", action="store_true", help="apply host changes (requires root)")
    args = parser.parse_args(argv)
    source, target = pwd.getpwnam(args.source_user), pwd.getpwnam(args.target_user)
    if source.pw_uid == target.pw_uid or target.pw_uid == 0:
        parser.error("source and target must be distinct; target must not be root")
    repo = Path(__file__).resolve().parents[2]
    source_config = Path(source.pw_dir) / ".config/yam"
    target_config = Path(target.pw_dir) / ".config/yam"
    data = Path("/mnt/sdb/YAM/data")
    print(f"Profile: {source_config} -> {target_config} (default: two arms, three cameras)")
    print(f"Permissions: add {target.pw_name} to dialout; shared data ACL: {data}")
    print("Preserve original owners, effective ACL permissions, and original configurations.")
    if not args.apply:
        print("Preview only. Add --apply under sudo to execute.")
        return 0
    if os.geteuid() != 0:
        parser.error("--apply requires sudo; do not run the teleop program as root")
    for command in ("getfacl", "setfacl", "usermod"):
        if not shutil.which(command):
            parser.error(f"required system command is missing: {command}")
    if not data.is_dir() or data.is_symlink():
        parser.error(f"shared data directory must exist and not be a symlink: {data}")
    profile_path = source_config / "profiles/default.yaml"
    profile = load_profile(profile_path)
    original = yaml.safe_load(profile_path.read_text())
    if profile.cameras is None:
        parser.error("source profile has no calibrated camera configuration")
    camera = yaml.safe_load(profile.cameras.config_path.read_text())
    if not {"main", "left", "right"} <= camera["cameras"].keys():
        parser.error("source camera config must contain main, left and right")
    if profile.storage.root.resolve() != data.resolve():
        parser.error("source profile storage differs from the agreed shared data directory")
    validate_motion_configuration(
        parser, {side: arm.config_path for side, arm in profile.arms.items()},
        hardware_port=profile.network.hardware_port, safety_port=profile.network.safety_port,
        camera_path=profile.cameras.config_path,
    )
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = Path(target.pw_dir) / ".local/state/yam-migration" / f"host-{stamp}"
    backup.mkdir(parents=True, mode=0o700)
    shutil.copytree(source_config, backup / "source-config", symlinks=True)
    if target_config.exists():
        shutil.copytree(target_config, backup / "previous-target-config", symlinks=True)
    with (backup / "data-acl.txt").open("w") as handle:
        subprocess.run(["getfacl", "-R", "-P", "-p", str(data)], stdout=handle, check=True)
    (backup / "groups.txt").write_text(subprocess.check_output(["id", target.pw_name], text=True))
    target_config.parent.mkdir(parents=True, exist_ok=True)
    os.chown(target_config.parent, target.pw_uid, target.pw_gid)
    stage = Path(tempfile.mkdtemp(prefix=".yam-migration-", dir=target_config.parent))
    (stage / "arms").mkdir()
    (stage / "profiles").mkdir()
    migrated = copy.deepcopy(original)
    for side, arm in profile.arms.items():
        shutil.copyfile(arm.config_path, stage / "arms" / f"yam_{side}.yaml")
        migrated["arms"][side] = f"../arms/yam_{side}.yaml"
    for name, pair in profile.auxiliary_pairs.items():
        if pair.config_path is not None:
            filename = f"yam_aux_{len(list((stage / 'arms').iterdir()))}.yaml"
            shutil.copyfile(pair.config_path, stage / "arms" / filename)
            migrated["auxiliary_pairs"][name]["config"] = f"../arms/{filename}"
    migrated["storage"]["root"] = str(data)
    migrated["storage"]["workspace_link"] = str(repo / "data")
    migrated["cameras"]["config"] = "../realsense_all.yaml"
    (stage / "realsense_all.yaml").write_text(yaml.safe_dump(camera, sort_keys=False))
    (stage / "profiles/original-rig.yaml").write_text(yaml.safe_dump(migrated, sort_keys=False))
    migrated.pop("auxiliary_pairs", None)
    migrated["cameras"]["config"] = "../realsense_bimanual.yaml"
    camera["cameras"] = {name: camera["cameras"][name] for name in ("main", "left", "right")}
    (stage / "realsense_bimanual.yaml").write_text(yaml.safe_dump(camera, sort_keys=False))
    (stage / "profiles/default.yaml").write_text(yaml.safe_dump(migrated, sort_keys=False))
    load_profile(stage / "profiles/default.yaml")
    load_profile(stage / "profiles/original-rig.yaml")
    subprocess.run(["usermod", "-aG", "dialout", target.pw_name], check=True)
    # Walk without following links; only ACL metadata is changed, never episode contents.
    for base, dirs, files in os.walk(data, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
        for path in [Path(base), *(Path(base) / name for name in files)]:
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                continue
            acl = subprocess.check_output(["getfacl", "-cp", str(path)], text=True)
            updated = acl_with_access(acl, (source.pw_name, target.pw_name), directory=path.is_dir())
            subprocess.run(["setfacl", "--no-mask", "--set-file=-", str(path)], input=updated, text=True, check=True)
    if target_config.exists():
        target_config.rename(target_config.with_name(f"yam-before-{stamp}"))
    stage.rename(target_config)
    for tree in (target_config, backup):
        for base, dirs, files in os.walk(tree, followlinks=False):
            os.chown(base, target.pw_uid, target.pw_gid)
            for name in dirs + files:
                os.chown(Path(base) / name, target.pw_uid, target.pw_gid, follow_symlinks=False)
    print(f"Completed. Backup and ACL rollback file: {backup}")
    print(f"Log in again as {target.pw_name}, then run yam doctor and scripts/teleop --check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
