"""Shared schema definitions and validation for recorded YAM episodes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

CURRENT_SCHEMA_VERSION = "1.4"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.1", "1.2", "1.3", CURRENT_SCHEMA_VERSION})
CONTROL_HZ = 30.0
EPISODE_PATTERN = re.compile(r"^(\d{4,})\.hdf5$")
JOINT_NAMES = [
    *(f"left_joint{i}" for i in range(1, 7)),
    "left_gripper",
    *(f"right_joint{i}" for i in range(1, 7)),
    "right_gripper",
]
GRIPPER_INDICES = np.asarray([6, 13])


def joint_names_for_arms(arm_names: tuple[str, ...]) -> list[str]:
    return [
        item
        for arm in arm_names
        for item in (*[f"{arm}_joint{i}" for i in range(1, 7)], f"{arm}_gripper")
    ]


@dataclass(frozen=True)
class EpisodeValidation:
    """Validated core metadata shared by replay and dataset tooling."""

    schema_version: str
    num_steps: int
    control_hz: float
    camera_names: tuple[str, ...]
    num_joints: int


def _integer_attribute(attrs, name: str) -> int:
    value = attrs.get(name)
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value, (int, np.integer)
    ):
        raise ValueError(f"{name} must be an integer")
    return int(value)


def _boolean_attribute(attrs, name: str) -> bool:
    value = attrs.get(name)
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be a boolean")
    return bool(value)


def _json_list_attribute(attrs, name: str) -> list[str]:
    value = attrs.get(name, "")
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} must be valid JSON") from exc
    if (
        not isinstance(decoded, list)
        or any(not isinstance(item, str) or not item for item in decoded)
        or len(set(decoded)) != len(decoded)
    ):
        raise ValueError(f"{name} must be a list of unique non-empty strings")
    return decoded


def _json_mapping_attribute(attrs, name: str) -> dict[str, Any]:
    value = attrs.get(name, "{}")
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must be a JSON object")
    return decoded


def _dataset(
    episode: h5py.File,
    name: str,
    *,
    shape: tuple[int, ...],
    required: bool,
) -> h5py.Dataset | None:
    if name not in episode:
        if required:
            raise ValueError(f"episode is missing {name}")
        return None
    dataset = episode[name]
    if not isinstance(dataset, h5py.Dataset):
        raise ValueError(f"{name} must be a dataset")
    if dataset.shape != shape:
        raise ValueError(f"{name} must have shape {shape}, got {dataset.shape}")
    return dataset


def _finite(dataset: h5py.Dataset, name: str) -> np.ndarray:
    values = np.asarray(dataset, dtype=float)
    if not np.all(np.isfinite(values)):
        raise ValueError(f"{name} must contain only finite values")
    return values


def _validate_camera_data(
    episode: h5py.File,
    *,
    schema_version: str,
    num_steps: int,
    camera_names: tuple[str, ...],
) -> None:
    metadata = _json_mapping_attribute(episode.attrs, "camera_metadata")
    depth_enabled = _boolean_attribute(episode.attrs, "depth_enabled")
    if schema_version in {"1.2", "1.3", CURRENT_SCHEMA_VERSION} and set(metadata) != set(
        camera_names
    ):
        raise ValueError("camera_metadata keys must match camera_names")

    for name in camera_names:
        spec = metadata.get(name, {})
        try:
            height = int(spec["height"])
            width = int(spec["width"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"camera_metadata for {name} lacks valid dimensions") from exc
        rgb = _dataset(
            episode,
            f"observations/images/{name}",
            shape=(num_steps, height, width, 3),
            required=True,
        )
        assert rgb is not None
        if rgb.dtype != np.uint8:
            raise ValueError(f"camera {name} RGB must use uint8")
        depth = _dataset(
            episode,
            f"observations/depth/{name}",
            shape=(num_steps, height, width),
            required=depth_enabled,
        )
        if depth is not None and depth.dtype != np.uint16:
            raise ValueError(f"camera {name} depth must use uint16")

        camera_time = _dataset(
            episode,
            f"observations/camera_timestamp/{name}",
            shape=(num_steps,),
            required=schema_version in {"1.2", "1.3", CURRENT_SCHEMA_VERSION},
        )
        frame_number = _dataset(
            episode,
            f"observations/camera_frame_number/{name}",
            shape=(num_steps,),
            required=schema_version in {"1.2", "1.3", CURRENT_SCHEMA_VERSION},
        )
        if camera_time is not None:
            times = _finite(camera_time, f"camera timestamp {name}")
            if times.size > 1 and not np.all(np.diff(times) > 0):
                raise ValueError(f"camera timestamp {name} must be strictly increasing")
        if frame_number is not None:
            frames = np.asarray(frame_number)
            if not np.issubdtype(frames.dtype, np.integer):
                raise ValueError(f"camera frame number {name} must be an integer")
            if frames.size > 1 and not np.all(np.diff(frames.astype(np.int64)) > 0):
                raise ValueError(
                    f"camera frame number {name} must be strictly increasing"
                )


def validate_episode_file(
    episode: h5py.File,
    *,
    path: Path | None = None,
    require_complete: bool = True,
    expected_outcome: str | None = None,
    require_current_schema: bool = False,
) -> EpisodeValidation:
    """Validate one open HDF5 episode without loading image arrays."""
    attrs = episode.attrs
    schema_version = str(attrs.get("schema_version", ""))
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
        raise ValueError(
            f"unsupported schema_version {schema_version!r}; expected one of {supported}"
        )
    if require_current_schema and schema_version != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {CURRENT_SCHEMA_VERSION} for new recordings"
        )

    joint_names = _json_list_attribute(attrs, "joint_names")
    num_joints = len(joint_names)
    if num_joints not in {14, 28}:
        raise ValueError("joint_names must describe two or four seven-DOF YAM arms")
    if num_joints == 14:
        expected_joint_names = JOINT_NAMES
    else:
        arm_names = tuple(_json_list_attribute(attrs, "arm_names"))
        if len(arm_names) != 4:
            raise ValueError("four-pair episodes require four arm_names")
        expected_joint_names = joint_names_for_arms(arm_names)
    if joint_names != expected_joint_names:
        raise ValueError("joint_names do not match the declared YAM arm layout")
    if _boolean_attribute(attrs, "sim"):
        raise ValueError("sim must be the boolean false for hardware episodes")
    complete = _boolean_attribute(attrs, "complete")
    if require_complete and not complete:
        raise ValueError("episode is not marked complete")
    if not require_complete and complete:
        raise ValueError("in-progress episode must not be marked complete")

    task_name = str(attrs.get("task_name", ""))
    if not task_name:
        raise ValueError("task_name must not be empty")
    episode_index = _integer_attribute(attrs, "episode_index")
    if episode_index < 1:
        raise ValueError("episode_index must be positive")
    outcome = str(attrs.get("outcome", ""))
    if expected_outcome is not None and outcome != expected_outcome:
        raise ValueError(f"outcome must be {expected_outcome}")
    if path is not None:
        path = Path(path)
        match = EPISODE_PATTERN.fullmatch(path.name)
        if match is None or int(match.group(1)) != episode_index:
            raise ValueError("episode_index does not match the episode filename")
        if path.parent.parent.name != task_name:
            raise ValueError("task_name does not match the episode path")
        if path.parent.name != outcome:
            raise ValueError("outcome does not match the episode directory")

    num_steps = _integer_attribute(attrs, "num_steps")
    if num_steps < 1:
        raise ValueError("num_steps must be at least one")
    try:
        control_hz = float(attrs.get("control_hz", np.nan))
    except (TypeError, ValueError) as exc:
        raise ValueError("control_hz must be a positive finite number") from exc
    if not np.isfinite(control_hz) or control_hz <= 0:
        raise ValueError("control_hz must be a positive finite number")
    if schema_version == CURRENT_SCHEMA_VERSION and not np.isclose(
        control_hz, CONTROL_HZ, rtol=0.0, atol=1e-9
    ):
        raise ValueError(f"schema {CURRENT_SCHEMA_VERSION} requires control_hz=30")

    current = schema_version == CURRENT_SCHEMA_VERSION
    if "action" in episode:
        action_shape = episode["action"].shape
        if not action_shape or action_shape[0] != num_steps:
            raise ValueError("num_steps does not match the datasets")
    qpos_ds = _dataset(
        episode, "observations/qpos", shape=(num_steps, num_joints), required=True
    )
    action_ds = _dataset(episode, "action", shape=(num_steps, num_joints), required=True)
    timestamp_ds = _dataset(
        episode, "timestamp", shape=(num_steps,), required=True
    )
    qvel_ds = _dataset(
        episode,
        "observations/qvel",
        shape=(num_steps, num_joints),
        required=current,
    )
    effort_ds = _dataset(
        episode,
        "observations/effort",
        shape=(num_steps, num_joints),
        required=current,
    )
    leader_ds = _dataset(
        episode, "leader_action", shape=(num_steps, num_joints), required=current
    )
    assert qpos_ds is not None and action_ds is not None and timestamp_ds is not None
    qpos = _finite(qpos_ds, "observations/qpos")
    action = _finite(action_ds, "action")
    timestamps = _finite(timestamp_ds, "timestamp")
    for name, dataset in (
        ("observations/qvel", qvel_ds),
        ("observations/effort", effort_ds),
        ("leader_action", leader_ds),
    ):
        if dataset is not None:
            _finite(dataset, name)
    if timestamps.size > 1 and not np.all(np.diff(timestamps) > 0):
        raise ValueError("timestamp must be strictly increasing")
    gripper_indices = np.arange(6, num_joints, 7, dtype=int)
    grippers = np.concatenate((qpos[:, gripper_indices], action[:, gripper_indices]), axis=0)
    if np.any(grippers < 0.0) or np.any(grippers > 1.0):
        raise ValueError("action and qpos grippers must stay within [0,1]")

    if "camera_names" in attrs:
        camera_names = tuple(_json_list_attribute(attrs, "camera_names"))
    elif schema_version == "1.1":
        camera_names = ()
    else:
        raise ValueError("camera_names must be valid JSON")
    if camera_names or schema_version in {"1.2", "1.3", CURRENT_SCHEMA_VERSION}:
        _validate_camera_data(
            episode,
            schema_version=schema_version,
            num_steps=num_steps,
            camera_names=camera_names,
        )
    if schema_version in {"1.2", "1.3", CURRENT_SCHEMA_VERSION}:
        skew = _dataset(
            episode, "camera_sync_skew", shape=(num_steps,), required=True
        )
        assert skew is not None
        skew_values = _finite(skew, "camera_sync_skew")
        if np.any(skew_values < 0):
            raise ValueError("camera_sync_skew must be non-negative")

    return EpisodeValidation(
        schema_version=schema_version,
        num_steps=num_steps,
        control_hz=control_hz,
        camera_names=camera_names,
        num_joints=num_joints,
    )
