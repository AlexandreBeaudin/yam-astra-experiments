"""Profile policy for operator commands, before any hardware is opened."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from gello.cameras.realsense_rig import load_camera_rig_config
from gello.profile import (
    MachineProfile,
    ProfileError,
    _load_arm,
    load_profile,
    resolve_profile_path,
)


def command_profile(
    parser: argparse.ArgumentParser, path: Path | None, *, required: bool = True
) -> MachineProfile | None:
    candidate = resolve_profile_path(path)
    if not required and path is None and not os.environ.get("YAM_PROFILE") and not candidate.exists():
        return None
    try:
        return load_profile(candidate)
    except (OSError, ValueError) as exc:
        parser.error(f"machine profile {candidate}: {exc}. Migrate ~/.config/yam or supply --profile.")


def validate_motion_configuration(
    parser: argparse.ArgumentParser,
    configs: dict[str, Path],
    *,
    hardware_port: int,
    safety_port: int,
    camera_path: Path | None = None,
) -> None:
    """Validate effective overrides, not just the profile's original bindings."""
    try:
        arms = [_load_arm(name, path.expanduser().resolve()) for name, path in configs.items()]
        for label, values in (
            ("CAN channels", [arm.can_channel for arm in arms]),
            ("leader ports", [str(arm.leader_port.resolve()) for arm in arms]),
        ):
            if len(set(values)) != len(values):
                raise ProfileError(f"all active arms must use different {label}")
            if any("REPLACE" in value.upper() for value in values):
                raise ProfileError(f"replace example placeholders in {label} with calibrated device identities")
        if len({arm.control_hz for arm in arms}) != 1:
            raise ProfileError("all active arms must use the same control rate")
        if any(not 1 <= port <= 65535 for port in (hardware_port, safety_port)):
            raise ProfileError("control ports must be between 1 and 65535")
        if hardware_port == safety_port:
            raise ProfileError("hardware_port and safety_port must differ")
        if camera_path is not None:
            rig = load_camera_rig_config(camera_path.expanduser().resolve())
            for name, camera in rig.cameras.items():
                if "REPLACE" in camera.serial.upper():
                    raise ProfileError(f"{name} camera serial is an example placeholder: {camera_path}")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
