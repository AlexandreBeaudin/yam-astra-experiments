"""Versioned machine-profile loading and validation."""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

SCHEMA_VERSION = 1
DEFAULT_PROFILE_ENV = "YAM_PROFILE"
BUILTIN_SAFETY_PROFILE = "yam-v1"


class ProfileError(ValueError):
    """A profile cannot be resolved or validated."""


@dataclass(frozen=True)
class ArmProfile:
    name: str
    config_path: Path
    can_channel: str
    leader_port: Path
    control_hz: int


@dataclass(frozen=True)
class AuxiliaryPairProfile:
    """An additional leader/follower pair with an optional active config.

    Inventory-only entries are checked by ``yam doctor``. The teleop launcher
    activates the third and fourth pairs together when both provide configs.
    """

    name: str
    leader_port: Path
    can_channel: str
    can_adapter_serial: str
    config_path: Path | None = None


@dataclass(frozen=True)
class CameraProfile:
    config_path: Path
    required: bool


@dataclass(frozen=True)
class StorageProfile:
    root: Path
    workspace_link: Path | None
    min_free_gb: float


@dataclass(frozen=True)
class NetworkProfile:
    host: str
    hardware_port: int
    safety_port: int


@dataclass(frozen=True)
class SafetyProfile:
    profile: str
    watchdog_ms: int
    motors_per_arm: int


@dataclass(frozen=True)
class MachineProfile:
    schema_version: int
    name: str
    path: Path
    arms: dict[str, ArmProfile]
    auxiliary_pairs: dict[str, AuxiliaryPairProfile]
    cameras: CameraProfile | None
    storage: StorageProfile
    network: NetworkProfile
    safety: SafetyProfile


def default_profile_path() -> Path:
    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    ).expanduser()
    return config_home / "yam" / "profiles" / "default.yaml"


def resolve_profile_path(cli_path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve CLI > environment > XDG default, without searching the repository."""
    candidate = cli_path or os.environ.get(DEFAULT_PROFILE_ENV)
    return Path(candidate).expanduser() if candidate else default_profile_path()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileError(f"{label} must be a mapping")
    return value


def _keys(
    value: Mapping[str, Any],
    label: str,
    *,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise ProfileError(f"{label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ProfileError(f"{label} has unknown keys: {', '.join(sorted(unknown))}")


def _path(value: Any, base: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{label} must be a non-empty path")
    expanded = Path(os.path.expandvars(value)).expanduser()
    return expanded if expanded.is_absolute() else (base / expanded).resolve()


def _load_yaml(path: Path, label: str) -> Mapping[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            return _mapping(yaml.safe_load(handle), label)
    except OSError as exc:
        raise ProfileError(f"cannot read {label} {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid YAML in {label} {path}: {exc}") from exc


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileError(f"{label} must be a positive integer")
    if value <= 0:
        raise ProfileError(f"{label} must be a positive integer")
    return value


def _port(value: Any, label: str) -> int:
    parsed = _positive_int(value, label)
    if parsed > 65535:
        raise ProfileError(f"{label} must be between 1 and 65535")
    return parsed


def _load_arm(name: str, path: Path) -> ArmProfile:
    cfg = _load_yaml(path, f"{name} arm config")
    robot = _mapping(cfg.get("robot"), f"{name}.robot")
    agent = _mapping(cfg.get("agent"), f"{name}.agent")
    channel = robot.get("channel")
    leader = agent.get("port")
    if not isinstance(channel, str) or not channel:
        raise ProfileError(f"{name} arm robot.channel must be a non-empty string")
    if not isinstance(leader, str) or not leader:
        raise ProfileError(f"{name} arm agent.port must be a non-empty string")
    start = agent.get("start_joints")
    if (
        not isinstance(start, list)
        or len(start) != 7
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            for item in start
        )
    ):
        raise ProfileError(f"{name} arm agent.start_joints must contain 7 values")
    motor = _mapping(agent.get("dynamixel_config"), f"{name}.agent.dynamixel_config")
    for key in ("joint_ids", "joint_offsets", "joint_signs"):
        values = motor.get(key)
        if not isinstance(values, list) or len(values) != 6:
            raise ProfileError(f"{name} arm {key} must contain 6 values")
    if motor["joint_ids"] != list(range(1, 7)):
        raise ProfileError(f"{name} arm joint_ids must equal [1, 2, 3, 4, 5, 6]")
    if any(value not in (-1, 1) for value in motor["joint_signs"]):
        raise ProfileError(f"{name} arm joint_signs must contain only -1 or 1")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in motor["joint_offsets"]
    ):
        raise ProfileError(f"{name} arm joint_offsets must be finite numbers")
    gripper = motor.get("gripper_config")
    if (
        not isinstance(gripper, list)
        or len(gripper) != 3
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            for item in gripper
        )
    ):
        raise ProfileError(f"{name} arm gripper_config must contain 3 values")
    return ArmProfile(
        name=name,
        config_path=path,
        can_channel=channel,
        leader_port=Path(leader),
        control_hz=_positive_int(cfg.get("hz", 30), f"{name}.hz"),
    )


def _load_auxiliary_pair(
    name: str, value: Mapping[str, Any], base: Path
) -> AuxiliaryPairProfile:
    _keys(
        value,
        f"auxiliary_pairs.{name}",
        required={"leader_port", "can_channel", "can_adapter_serial"},
        optional={"config"},
    )
    channel = value["can_channel"]
    serial = value["can_adapter_serial"]
    if not isinstance(channel, str) or not channel:
        raise ProfileError(f"auxiliary_pairs.{name}.can_channel must be a non-empty string")
    if not isinstance(serial, str) or not serial:
        raise ProfileError(
            f"auxiliary_pairs.{name}.can_adapter_serial must be a non-empty string"
        )
    return AuxiliaryPairProfile(
        name=name,
        leader_port=_path(value["leader_port"], base, f"auxiliary_pairs.{name}.leader_port"),
        can_channel=channel,
        can_adapter_serial=serial,
        config_path=(
            _path(value["config"], base, f"auxiliary_pairs.{name}.config")
            if value.get("config") is not None
            else None
        ),
    )


def load_profile(
    path: str | os.PathLike[str] | None = None,
    *,
    require_references: bool = True,
) -> MachineProfile:
    profile_path = resolve_profile_path(path).resolve()
    root = _load_yaml(profile_path, "profile")
    _keys(
        root,
        "profile",
        required={
            "schema_version",
            "name",
            "arms",
            "storage",
            "network",
            "safety",
        },
        optional={"cameras", "auxiliary_pairs"},
    )
    if (
        isinstance(root["schema_version"], bool)
        or not isinstance(root["schema_version"], int)
        or root["schema_version"] != SCHEMA_VERSION
    ):
        raise ProfileError(
            f"unsupported schema_version {root['schema_version']!r}; expected {SCHEMA_VERSION}"
        )
    name = root["name"]
    if not isinstance(name, str) or not name.strip():
        raise ProfileError("profile.name must be a non-empty string")
    base = profile_path.parent

    arms_cfg = _mapping(root["arms"], "profile.arms")
    _keys(arms_cfg, "profile.arms", required={"left", "right"})
    arm_paths = {
        side: _path(arms_cfg[side], base, f"profile.arms.{side}")
        for side in ("left", "right")
    }
    if require_references:
        arms = {side: _load_arm(side, arm_paths[side]) for side in arm_paths}
    else:
        arms = {
            side: ArmProfile(side, arm_paths[side], "", Path(), 0)
            for side in arm_paths
        }

    auxiliary_cfg = root.get("auxiliary_pairs", {})
    auxiliary_mapping = _mapping(auxiliary_cfg, "profile.auxiliary_pairs")
    auxiliary_pairs = {
        pair_name: _load_auxiliary_pair(
            pair_name,
            _mapping(value, f"auxiliary_pairs.{pair_name}"),
            base,
        )
        for pair_name, value in auxiliary_mapping.items()
    }
    if require_references:
        for pair_name, pair in auxiliary_pairs.items():
            if pair.config_path is None:
                continue
            calibrated = _load_arm(pair_name, pair.config_path)
            if calibrated.leader_port != pair.leader_port:
                raise ProfileError(
                    f"auxiliary pair {pair_name} config leader does not match binding"
                )
            if calibrated.can_channel != pair.can_channel:
                raise ProfileError(
                    f"auxiliary pair {pair_name} config CAN does not match binding"
                )

    camera_profile = None
    if "cameras" in root and root["cameras"] is not None:
        camera_cfg = _mapping(root["cameras"], "profile.cameras")
        _keys(
            camera_cfg,
            "profile.cameras",
            required={"config"},
            optional={"required"},
        )
        camera_path = _path(camera_cfg["config"], base, "profile.cameras.config")
        if require_references:
            try:
                from gello.cameras.realsense_rig import load_camera_rig_config

                load_camera_rig_config(camera_path)
            except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                raise ProfileError(f"invalid camera config {camera_path}: {exc}") from exc
        required = camera_cfg.get("required", True)
        if not isinstance(required, bool):
            raise ProfileError("profile.cameras.required must be true or false")
        camera_profile = CameraProfile(
            config_path=camera_path,
            required=required,
        )

    storage_cfg = _mapping(root["storage"], "profile.storage")
    _keys(
        storage_cfg,
        "profile.storage",
        required={"root"},
        optional={"workspace_link", "min_free_gb"},
    )
    min_free_value = storage_cfg.get("min_free_gb", 20)
    if (
        isinstance(min_free_value, bool)
        or not isinstance(min_free_value, (int, float))
        or not math.isfinite(min_free_value)
        or min_free_value < 0
    ):
        raise ProfileError("profile.storage.min_free_gb must be non-negative")
    min_free_gb = float(min_free_value)
    workspace_link = storage_cfg.get("workspace_link")
    storage = StorageProfile(
        root=_path(storage_cfg["root"], base, "profile.storage.root"),
        workspace_link=(
            _path(workspace_link, base, "profile.storage.workspace_link")
            if workspace_link is not None
            else None
        ),
        min_free_gb=min_free_gb,
    )

    network_cfg = _mapping(root["network"], "profile.network")
    _keys(
        network_cfg,
        "profile.network",
        required={"host", "hardware_port", "safety_port"},
    )
    host = network_cfg["host"]
    if not isinstance(host, str) or not host:
        raise ProfileError("profile.network.host must be a non-empty string")
    if host not in {"127.0.0.1", "localhost"}:
        raise ProfileError("yam-v1 currently requires a local network host")
    network = NetworkProfile(
        host=host,
        hardware_port=_port(network_cfg["hardware_port"], "hardware_port"),
        safety_port=_port(network_cfg["safety_port"], "safety_port"),
    )

    safety_cfg = _mapping(root["safety"], "profile.safety")
    _keys(
        safety_cfg,
        "profile.safety",
        required={"profile", "watchdog_ms", "motors_per_arm"},
    )
    if not isinstance(safety_cfg["profile"], str):
        raise ProfileError("profile.safety.profile must be a string")
    safety = SafetyProfile(
        profile=safety_cfg["profile"],
        watchdog_ms=_positive_int(safety_cfg["watchdog_ms"], "watchdog_ms"),
        motors_per_arm=_positive_int(
            safety_cfg["motors_per_arm"], "motors_per_arm"
        ),
    )

    if require_references:
        if arms["left"].can_channel == arms["right"].can_channel:
            raise ProfileError("left and right arms must use different CAN channels")
        if arms["left"].leader_port == arms["right"].leader_port:
            raise ProfileError("left and right arms must use different leader ports")
        if arms["left"].control_hz != arms["right"].control_hz:
            raise ProfileError("left and right arms must use the same control rate")
    active_leaders = {arm.leader_port for arm in arms.values()}
    active_channels = {arm.can_channel for arm in arms.values()}
    for pair_name, pair in auxiliary_pairs.items():
        if pair.leader_port in active_leaders:
            raise ProfileError(
                f"auxiliary pair {pair_name} reuses an active leader port"
            )
        if pair.can_channel in active_channels:
            raise ProfileError(
                f"auxiliary pair {pair_name} reuses an active CAN channel"
            )
        active_leaders.add(pair.leader_port)
        active_channels.add(pair.can_channel)
    if network.hardware_port == network.safety_port:
        raise ProfileError("hardware_port and safety_port must differ")
    if safety.profile != BUILTIN_SAFETY_PROFILE:
        raise ProfileError(
            f"unknown safety profile {safety.profile!r}; expected {BUILTIN_SAFETY_PROFILE!r}"
        )
    if safety.watchdog_ms != 400:
        raise ProfileError("yam-v1 requires watchdog_ms: 400")
    if safety.motors_per_arm != 7:
        raise ProfileError("yam-v1 requires motors_per_arm: 7")

    return MachineProfile(
        schema_version=SCHEMA_VERSION,
        name=name,
        path=profile_path,
        arms=arms,
        auxiliary_pairs=auxiliary_pairs,
        cameras=camera_profile,
        storage=storage,
        network=network,
        safety=safety,
    )


def register_auxiliary_pair(
    profile_path: str | os.PathLike[str] | None,
    *,
    name: str,
    leader_port: str,
    can_channel: str,
    can_adapter_serial: str,
    config_path: str | None = None,
) -> MachineProfile:
    """Atomically add a non-active physical pair to a machine profile."""

    if not name or not name.replace("_", "").isalnum():
        raise ProfileError("auxiliary pair name must use letters, numbers, or underscores")
    path = resolve_profile_path(profile_path).resolve()
    root = dict(_load_yaml(path, "profile"))
    auxiliary = dict(_mapping(root.get("auxiliary_pairs", {}), "profile.auxiliary_pairs"))
    if name in auxiliary:
        raise ProfileError(f"auxiliary pair {name!r} is already registered")
    auxiliary[name] = {
        "leader_port": leader_port,
        "can_channel": can_channel,
        "can_adapter_serial": can_adapter_serial,
        **({"config": config_path} if config_path else {}),
    }
    root["auxiliary_pairs"] = auxiliary

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".yaml", dir=path.parent, delete=False
    ) as handle:
        temporary_path = Path(handle.name)
        yaml.safe_dump(root, handle, sort_keys=False)
    try:
        validated = load_profile(temporary_path)
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return MachineProfile(
        schema_version=validated.schema_version,
        name=validated.name,
        path=path,
        arms=validated.arms,
        auxiliary_pairs=validated.auxiliary_pairs,
        cameras=validated.cameras,
        storage=validated.storage,
        network=validated.network,
        safety=validated.safety,
    )
