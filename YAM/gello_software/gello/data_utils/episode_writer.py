"""Streaming, crash-tolerant HDF5 episode recording."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import h5py
import numpy as np

from gello.data_utils.episode_validation import (
    CONTROL_HZ,
    CURRENT_SCHEMA_VERSION,
    EPISODE_PATTERN,
    joint_names_for_arms,
    validate_episode_file,
)
from gello.data_utils.episode_validation import (
    JOINT_NAMES as JOINT_NAMES,
)

TASK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class CameraSample:
    """One complete synchronized camera row for all configured views."""

    rgb: dict[str, np.ndarray]
    depth: dict[str, np.ndarray]
    timestamps: dict[str, float]
    frame_numbers: dict[str, int]
    skew_s: float


def validate_task_name(task_name: str) -> str:
    if not TASK_NAME_PATTERN.fullmatch(task_name):
        raise ValueError(
            "task_name must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}"
        )
    return task_name


def ensure_data_link(workspace_root: Path, expected_target: Path) -> Path:
    """Create or validate the workspace data link without local fallback."""
    workspace_root = Path(workspace_root)
    expected_target = Path(expected_target)
    if not expected_target.is_dir():
        raise RuntimeError(f"data target is not mounted: {expected_target}")
    if not os.access(expected_target, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError(f"data target is not writable: {expected_target}")

    link = workspace_root / "data"
    if not link.exists() and not link.is_symlink():
        link.symlink_to(expected_target.resolve(), target_is_directory=True)
    if not link.is_symlink():
        raise RuntimeError(f"data path must be a symbolic link: {link}")
    if link.resolve() != expected_target.resolve():
        raise RuntimeError(
            f"data link {link} must resolve to {expected_target}, got {link.resolve()}"
        )
    return link


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class EpisodeWriter:
    """Append samples to a temporary episode and atomically classify it."""

    def __init__(
        self,
        data_root: Path,
        task_name: str,
        control_hz: float,
        *,
        arm_names: tuple[str, ...] = ("left", "right"),
    ):
        self.data_root = Path(data_root)
        self.task_name = validate_task_name(task_name)
        self.control_hz = float(control_hz)
        if len(arm_names) not in {2, 4} or len(set(arm_names)) != len(arm_names):
            raise ValueError("arm_names must contain two or four unique names")
        self.arm_names = tuple(arm_names)
        self.num_joints = 7 * len(self.arm_names)
        self.joint_names = joint_names_for_arms(self.arm_names)
        if not np.isclose(
            self.control_hz, CONTROL_HZ, rtol=0.0, atol=1e-9
        ):
            raise ValueError("YAM episode collection requires control_hz=30")
        self.task_dir = self.data_root / self.task_name
        self.success_dir = self.task_dir / "success"
        self.fail_dir = self.task_dir / "fail"
        self.in_progress_dir = self.task_dir / ".in_progress"
        for directory in (self.success_dir, self.fail_dir, self.in_progress_dir):
            directory.mkdir(parents=True, exist_ok=True)

        self.next_episode_index = self._find_next_index()
        self.incomplete_recordings = sorted(self.in_progress_dir.glob("*.hdf5"))
        self._file: Optional[h5py.File] = None
        self._temp_path: Optional[Path] = None
        self._steps = 0
        self._camera_metadata: dict[str, dict[str, Any]] = {}
        self._camera_names: tuple[str, ...] = ()
        self._depth_enabled = False

    def _numbered_files(self) -> Dict[int, Path]:
        numbered: Dict[int, Path] = {}
        for directory in (self.success_dir, self.fail_dir):
            for path in directory.glob("*.hdf5"):
                match = EPISODE_PATTERN.fullmatch(path.name)
                if match is None:
                    continue
                index = int(match.group(1))
                if index in numbered:
                    raise RuntimeError(
                        f"duplicate episode {index:04d}: {numbered[index]} and {path}"
                    )
                numbered[index] = path
        return numbered

    def _find_next_index(self) -> int:
        numbered = self._numbered_files()
        return max(numbered, default=0) + 1

    def start(
        self,
        *,
        camera_metadata: Mapping[str, Mapping[str, Any]] | None = None,
        depth_enabled: bool = False,
        sync_policy: Mapping[str, Any] | None = None,
    ) -> Path:
        if self._file is not None:
            raise RuntimeError("an episode is already recording")
        metadata = self._validate_camera_metadata(
            {} if camera_metadata is None else camera_metadata
        )
        if type(depth_enabled) is not bool:
            raise ValueError("depth_enabled must be a boolean")
        if depth_enabled and not metadata:
            raise ValueError("depth requires configured cameras")
        self._camera_metadata = metadata
        self._camera_names = tuple(metadata)
        self._depth_enabled = depth_enabled
        index = self.next_episode_index
        self._temp_path = self.in_progress_dir / (
            f"{index:04d}-{uuid.uuid4().hex}.hdf5"
        )
        self._file = h5py.File(self._temp_path, "w")
        self._steps = 0
        attrs = self._file.attrs
        attrs["schema_version"] = CURRENT_SCHEMA_VERSION
        attrs["task_name"] = self.task_name
        attrs["episode_index"] = index
        attrs["outcome"] = "in_progress"
        attrs["control_hz"] = self.control_hz
        attrs["num_steps"] = 0
        attrs["created_at_utc"] = _utc_now()
        attrs["finished_at_utc"] = ""
        attrs["joint_names"] = json.dumps(self.joint_names)
        attrs["arm_names"] = json.dumps(list(self.arm_names))
        attrs["camera_names"] = json.dumps(list(self._camera_names))
        attrs["camera_metadata"] = json.dumps(metadata)
        attrs["depth_enabled"] = depth_enabled
        attrs["camera_sync_policy"] = json.dumps(dict(sync_policy or {}))
        attrs["camera_sync_total_misses"] = 0
        attrs["camera_sync_max_consecutive_misses"] = 0
        attrs["camera_sync_max_observed_skew_s"] = 0.0
        attrs["sim"] = False
        attrs["complete"] = False

        observations = self._file.create_group("observations")
        observations.create_group("images")
        observations.create_group("depth")
        observations.create_group("camera_timestamp")
        observations.create_group("camera_frame_number")
        self._create_resizable("observations/qpos", (self.num_joints,), np.float32)
        self._create_resizable("observations/qvel", (self.num_joints,), np.float32)
        self._create_resizable("observations/effort", (self.num_joints,), np.float32)
        self._create_resizable("action", (self.num_joints,), np.float32)
        self._create_resizable("leader_action", (self.num_joints,), np.float32)
        self._create_resizable("timestamp", (), np.float64)
        self._create_resizable("camera_sync_skew", (), np.float32)
        for name, camera in metadata.items():
            height = int(camera["height"])
            width = int(camera["width"])
            self._create_resizable(
                f"observations/images/{name}",
                (height, width, 3),
                np.uint8,
                compression="lzf",
            )
            if depth_enabled:
                self._create_resizable(
                    f"observations/depth/{name}",
                    (height, width),
                    np.uint16,
                    compression="lzf",
                    shuffle=True,
                )
            self._create_resizable(
                f"observations/camera_timestamp/{name}", (), np.float64
            )
            self._create_resizable(
                f"observations/camera_frame_number/{name}", (), np.uint64
            )
        self._file.flush()
        return self._temp_path

    def _create_resizable(
        self,
        name: str,
        shape: tuple[int, ...],
        dtype,
        *,
        compression: str | None = None,
        shuffle: bool = False,
    ):
        assert self._file is not None
        return self._file.create_dataset(
            name,
            shape=(0, *shape),
            maxshape=(None, *shape),
            chunks=(1, *shape),
            dtype=dtype,
            compression=compression,
            shuffle=shuffle,
        )

    @staticmethod
    def _validate_camera_metadata(
        metadata: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(metadata, Mapping):
            raise ValueError("camera_metadata must be a mapping")
        result: dict[str, dict[str, Any]] = {}
        serials: set[str] = set()
        required = {"serial", "model", "width", "height", "fps", "rotation"}
        for name, value in metadata.items():
            if not isinstance(name, str) or not name:
                raise ValueError("camera names must be non-empty strings")
            if not isinstance(value, Mapping) or set(value) != required:
                raise ValueError(f"camera metadata for {name} has invalid fields")
            camera = dict(value)
            if not isinstance(camera["serial"], str) or not camera["serial"]:
                raise ValueError(f"camera {name} serial must be a non-empty string")
            if camera["serial"] in serials:
                raise ValueError(f"duplicate camera serial: {camera['serial']}")
            serials.add(camera["serial"])
            if not isinstance(camera["model"], str) or not camera["model"]:
                raise ValueError(f"camera {name} model must be a non-empty string")
            for field in ("width", "height", "fps"):
                if type(camera[field]) is not int or camera[field] <= 0:
                    raise ValueError(f"camera {name} {field} must be a positive integer")
            if type(camera["rotation"]) is not int or camera["rotation"] not in {
                0,
                90,
                180,
                270,
            }:
                raise ValueError(f"camera {name} rotation is invalid")
            result[name] = camera
        return result

    def _append_dataset(self, name: str, value: np.ndarray) -> None:
        assert self._file is not None
        dataset = self._file[name]
        dataset.resize(self._steps + 1, axis=0)
        dataset[self._steps] = value

    def _append_camera(self, key: str, value: np.ndarray) -> None:
        assert self._file is not None
        if key.endswith("_rgb"):
            camera_name = key[: -len("_rgb")]
            group_name = "observations/images"
            dtype = np.uint8
        elif key.endswith("_depth"):
            camera_name = key[: -len("_depth")]
            group_name = "observations/depth"
            dtype = np.uint16
        else:
            return

        dataset_name = f"{group_name}/{camera_name}"
        array = np.asarray(value, dtype=dtype)
        if dataset_name not in self._file:
            self._create_resizable(dataset_name, array.shape, dtype)
        dataset = self._file[dataset_name]
        if dataset.shape[1:] != array.shape:
            raise ValueError(
                f"camera {camera_name} shape changed from {dataset.shape[1:]} to {array.shape}"
            )
        self._append_dataset(dataset_name, array)

        camera_names = set(json.loads(self._file.attrs["camera_names"]))
        camera_names.add(camera_name)
        self._file.attrs["camera_names"] = json.dumps(sorted(camera_names))

    def append(
        self,
        observation: Dict[str, Any],
        action: np.ndarray,
        timestamp: float,
        leader_action: np.ndarray | None = None,
        camera_sample: CameraSample | None = None,
    ) -> None:
        if self._file is None:
            raise RuntimeError("no episode is recording")
        qpos = np.asarray(observation["joint_positions"], dtype=np.float32)
        qvel = np.asarray(observation["joint_velocities"], dtype=np.float32)
        effort = np.asarray(observation["joint_efforts"], dtype=np.float32)
        action_array = np.asarray(action, dtype=np.float32)
        leader_array = np.asarray(
            action if leader_action is None else leader_action, dtype=np.float32
        )
        for label, value in (
            ("qpos", qpos),
            ("qvel", qvel),
            ("effort", effort),
            ("action", action_array),
            ("leader_action", leader_array),
        ):
            if value.shape != (self.num_joints,):
                raise ValueError(
                    f"{label} must have shape ({self.num_joints},), got {value.shape}"
                )
        if not np.isfinite(timestamp):
            raise ValueError("timestamp must be finite")
        if self._camera_names:
            if camera_sample is None:
                raise ValueError("configured cameras require a complete camera sample")
            self._validate_camera_sample(camera_sample)
        elif camera_sample is not None:
            raise ValueError("camera sample requires configured camera metadata")

        self._append_dataset("observations/qpos", qpos)
        self._append_dataset("observations/qvel", qvel)
        self._append_dataset("observations/effort", effort)
        self._append_dataset("action", action_array)
        self._append_dataset("leader_action", leader_array)
        self._append_dataset("timestamp", np.asarray(timestamp, dtype=np.float64))
        if camera_sample is not None:
            for name in self._camera_names:
                self._append_dataset(
                    f"observations/images/{name}", camera_sample.rgb[name]
                )
                if self._depth_enabled:
                    self._append_dataset(
                        f"observations/depth/{name}", camera_sample.depth[name]
                    )
                self._append_dataset(
                    f"observations/camera_timestamp/{name}",
                    np.asarray(camera_sample.timestamps[name], dtype=np.float64),
                )
                self._append_dataset(
                    f"observations/camera_frame_number/{name}",
                    np.asarray(camera_sample.frame_numbers[name], dtype=np.uint64),
                )
            self._append_dataset(
                "camera_sync_skew",
                np.asarray(camera_sample.skew_s, dtype=np.float32),
            )
            self._file.attrs["camera_sync_max_observed_skew_s"] = max(
                float(self._file.attrs["camera_sync_max_observed_skew_s"]),
                float(camera_sample.skew_s),
            )
        else:
            for key, value in observation.items():
                self._append_camera(key, value)
            self._append_dataset("camera_sync_skew", np.asarray(0.0, dtype=np.float32))
        self._steps += 1
        self._file.attrs["num_steps"] = self._steps
        if self._steps % max(1, round(self.control_hz)) == 0:
            self._file.flush()

    def _validate_camera_sample(self, sample: CameraSample) -> None:
        names = set(self._camera_names)
        for label, values in (
            ("RGB", sample.rgb),
            ("timestamp", sample.timestamps),
            ("frame number", sample.frame_numbers),
        ):
            if set(values) != names:
                raise ValueError(f"camera {label} keys do not match configured cameras")
        expected_depth = names if self._depth_enabled else set()
        if set(sample.depth) != expected_depth:
            raise ValueError("camera depth keys do not match depth configuration")
        if not np.isfinite(sample.skew_s) or sample.skew_s < 0:
            raise ValueError("camera skew must be finite and non-negative")
        for name in self._camera_names:
            camera = self._camera_metadata[name]
            rgb = np.asarray(sample.rgb[name])
            if rgb.dtype != np.uint8 or rgb.shape != (
                camera["height"],
                camera["width"],
                3,
            ):
                raise ValueError(
                    f"camera {name} RGB must be uint8 "
                    f"[{camera['height']},{camera['width']},3]"
                )
            if self._depth_enabled:
                depth = np.asarray(sample.depth[name])
                if depth.dtype != np.uint16 or depth.shape != (
                    camera["height"],
                    camera["width"],
                ):
                    raise ValueError(
                        f"camera {name} depth must be uint16 "
                        f"[{camera['height']},{camera['width']}]"
                    )
            camera_timestamp = sample.timestamps[name]
            if not np.isfinite(camera_timestamp):
                raise ValueError(f"camera {name} timestamp must be finite")
            frame_number = sample.frame_numbers[name]
            if type(frame_number) is not int or frame_number < 0:
                raise ValueError(f"camera {name} frame number must be non-negative")

    def update_camera_stats(
        self,
        *,
        total_misses: int,
        max_consecutive_misses: int,
        max_observed_skew_s: float,
    ) -> None:
        if self._file is None:
            raise RuntimeError("no episode is recording")
        if (
            type(total_misses) is not int
            or total_misses < 0
            or type(max_consecutive_misses) is not int
            or max_consecutive_misses < 0
            or not np.isfinite(max_observed_skew_s)
            or max_observed_skew_s < 0
        ):
            raise ValueError("camera statistics are invalid")
        self._file.attrs["camera_sync_total_misses"] = total_misses
        self._file.attrs[
            "camera_sync_max_consecutive_misses"
        ] = max_consecutive_misses
        self._file.attrs["camera_sync_max_observed_skew_s"] = max(
            float(self._file.attrs["camera_sync_max_observed_skew_s"]),
            float(max_observed_skew_s),
        )

    def commit(self, outcome: str) -> Path:
        if outcome not in {"success", "fail"}:
            raise ValueError("outcome must be success or fail")
        if self._temp_path is None:
            raise RuntimeError("no episode is recording")
        if self._file is not None:
            index = int(self._file.attrs["episode_index"])
            self._file.flush()
            validate_episode_file(
                self._file,
                require_complete=False,
                expected_outcome="in_progress",
                require_current_schema=True,
            )
            self._file.attrs["outcome"] = outcome
            self._file.attrs["complete"] = True
            self._file.attrs["finished_at_utc"] = _utc_now()
            try:
                validate_episode_file(
                    self._file,
                    require_complete=True,
                    expected_outcome=outcome,
                    require_current_schema=True,
                )
            except Exception:
                self._file.attrs["outcome"] = "in_progress"
                self._file.attrs["complete"] = False
                self._file.attrs["finished_at_utc"] = ""
                self._file.flush()
                raise
            self._file.flush()
            self._file.close()
            self._file = None
        else:
            with h5py.File(self._temp_path, "r") as episode:
                index = int(episode.attrs["episode_index"])
        destination = (self.success_dir if outcome == "success" else self.fail_dir) / f"{index:04d}.hdf5"
        os.replace(self._temp_path, destination)
        self._temp_path = None
        self.next_episode_index = self._find_next_index()
        self.incomplete_recordings = sorted(self.in_progress_dir.glob("*.hdf5"))
        return destination

    def discard(self) -> None:
        if self._temp_path is None:
            raise RuntimeError("no episode is recording")
        path = self._temp_path
        if self._file is not None:
            self._file.close()
            self._file = None
        self._temp_path = None
        path.unlink(missing_ok=True)
        self.incomplete_recordings = sorted(self.in_progress_dir.glob("*.hdf5"))

    def close_incomplete(self) -> None:
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None
        self._temp_path = None
        self.incomplete_recordings = sorted(self.in_progress_dir.glob("*.hdf5"))

    def recover_incomplete(self, path: Path, outcome: str) -> Optional[Path]:
        path = Path(path)
        if path.parent != self.in_progress_dir or not path.exists():
            raise ValueError("recording is not an incomplete file for this task")
        if outcome == "discard":
            path.unlink()
            self.incomplete_recordings = sorted(self.in_progress_dir.glob("*.hdf5"))
            return None
        if outcome != "fail":
            raise ValueError("incomplete recordings may only be failed or discarded")
        with h5py.File(path, "r+") as episode:
            index = int(episode.attrs["episode_index"])
            destination = self.fail_dir / f"{index:04d}.hdf5"
            if destination.exists() or (self.success_dir / destination.name).exists():
                raise RuntimeError(f"episode {index:04d} already exists")
            lengths: dict[str, int] = {}

            def collect(name, item):
                if isinstance(item, h5py.Dataset) and item.ndim >= 1:
                    lengths[name] = int(item.shape[0])

            episode.visititems(collect)
            if not lengths:
                raise RuntimeError("incomplete recording contains no sample datasets")
            common_steps = min(lengths.values())
            for name in lengths:
                dataset = episode[name]
                if dataset.shape[0] != common_steps:
                    dataset.resize(common_steps, axis=0)
            episode.attrs["outcome"] = "fail"
            episode.attrs["complete"] = True
            episode.attrs["num_steps"] = common_steps
            episode.attrs["recovered_from_incomplete"] = True
            episode.attrs["recovery_reason"] = "operator classified interrupted episode as fail"
            episode.attrs["recovery_original_lengths"] = json.dumps(lengths, sort_keys=True)
            episode.attrs["recovered_at_utc"] = _utc_now()
            episode.attrs["finished_at_utc"] = _utc_now()
            episode.flush()
        os.replace(path, destination)
        self.next_episode_index = self._find_next_index()
        self.incomplete_recordings = sorted(self.in_progress_dir.glob("*.hdf5"))
        return destination
