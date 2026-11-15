"""Read-only duration statistics for completed YAM episodes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import h5py
import numpy as np

from gello.data_utils.episode_validation import (
    CONTROL_HZ,
    CURRENT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
)

VALID_OUTCOMES = {"success", "fail"}


@dataclass(frozen=True)
class EpisodeDuration:
    path: Path
    outcome: str
    num_steps: int
    control_hz: float
    usable_seconds: float
    recording_span_seconds: float


@dataclass(frozen=True)
class DurationSummary:
    episode_count: int
    success_count: int
    fail_count: int
    total_steps: int
    usable_seconds: float
    recording_span_seconds: float


@dataclass(frozen=True)
class DurationError:
    path: Path
    message: str


@dataclass(frozen=True)
class DurationScan:
    episodes: tuple[EpisodeDuration, ...]
    summary: DurationSummary
    errors: tuple[DurationError, ...]


def _text_attribute(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def read_episode_duration(path: Path) -> EpisodeDuration:
    """Validate duration metadata without reading interleaved frame datasets."""
    episode_path = Path(path)
    try:
        with h5py.File(episode_path, "r") as episode:
            schema_version = _text_attribute(
                episode.attrs.get("schema_version", "")
            )
            if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
                raise ValueError(
                    f"unsupported schema_version {schema_version!r}"
                )
            complete = episode.attrs.get("complete")
            if not isinstance(complete, (bool, np.bool_)) or not bool(complete):
                raise ValueError("episode is not marked complete")
            outcome = _text_attribute(episode.attrs.get("outcome", ""))
            if outcome not in VALID_OUTCOMES:
                raise ValueError(
                    "completed episode outcome must be success or fail"
                )
            raw_steps = episode.attrs.get("num_steps")
            if (
                isinstance(raw_steps, (bool, np.bool_))
                or not isinstance(raw_steps, (int, np.integer))
                or int(raw_steps) < 1
            ):
                raise ValueError("num_steps must be a positive integer")
            num_steps = int(raw_steps)
            try:
                control_hz = float(episode.attrs.get("control_hz", np.nan))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "control_hz must be a positive finite number"
                ) from exc
            if not np.isfinite(control_hz) or control_hz <= 0:
                raise ValueError("control_hz must be a positive finite number")
            if schema_version == CURRENT_SCHEMA_VERSION and not np.isclose(
                control_hz, CONTROL_HZ, rtol=0.0, atol=1e-9
            ):
                raise ValueError(
                    f"schema {CURRENT_SCHEMA_VERSION} requires control_hz=30"
                )
            if "timestamp" not in episode:
                raise ValueError("episode is missing timestamp")
            timestamps = episode["timestamp"]
            if not isinstance(timestamps, h5py.Dataset):
                raise ValueError("timestamp must be a dataset")
            if timestamps.shape != (num_steps,):
                raise ValueError(
                    f"timestamp must have shape ({num_steps},), got {timestamps.shape}"
                )
            first = float(timestamps[0])
            last = float(timestamps[-1])
            if not np.isfinite(first) or not np.isfinite(last) or last < first:
                raise ValueError(
                    "timestamp endpoints must be finite and non-decreasing"
                )
    except OSError as exc:
        raise ValueError(f"cannot read HDF5 episode: {exc}") from exc
    return EpisodeDuration(
        path=episode_path,
        outcome=outcome,
        num_steps=num_steps,
        control_hz=control_hz,
        usable_seconds=num_steps / control_hz,
        recording_span_seconds=last - first,
    )


def summarize_durations(
    episodes: Iterable[EpisodeDuration],
) -> DurationSummary:
    values = tuple(episodes)
    return DurationSummary(
        episode_count=len(values),
        success_count=sum(item.outcome == "success" for item in values),
        fail_count=sum(item.outcome == "fail" for item in values),
        total_steps=sum(item.num_steps for item in values),
        usable_seconds=sum(item.usable_seconds for item in values),
        recording_span_seconds=sum(
            item.recording_span_seconds for item in values
        ),
    )


def inspect_episode_paths(paths: Iterable[Path]) -> DurationScan:
    """Inspect explicit episode paths, retaining errors alongside valid totals."""
    episodes: list[EpisodeDuration] = []
    errors: list[DurationError] = []
    seen: set[Path] = set()
    for path in paths:
        episode_path = Path(path)
        identity = episode_path.resolve(strict=False)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            episodes.append(read_episode_duration(episode_path))
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(DurationError(episode_path, str(exc)))
    return DurationScan(
        episodes=tuple(episodes),
        summary=summarize_durations(episodes),
        errors=tuple(errors),
    )


def scan_episode_directory(directory: Path) -> DurationScan:
    """Recursively inspect every HDF5 file below one directory."""
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"duration scan path is not a directory: {root}")
    return inspect_episode_paths(sorted(root.rglob("*.hdf5")))


def format_duration(seconds: float) -> str:
    """Format a non-negative duration as HH:MM:SS.s."""
    value = max(0.0, float(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{seconds:04.1f}"
