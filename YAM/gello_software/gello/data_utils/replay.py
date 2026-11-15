"""Discovery, validation, and timing helpers for recorded YAM replay."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TypeVar

import h5py
import numpy as np

from gello.data_utils.episode_validation import (
    EPISODE_PATTERN,
    validate_episode_file,
)
from gello.data_utils.episode_writer import TASK_NAME_PATTERN
from gello.safety.motion_session import SupervisedMotionSession
from gello.safety.return_zero import (
    arm_indices_for_size,
    gripper_indices_for_size,
    joint_trajectory,
    validate_feedback,
    zero_target_for_size,
)

REPLAY_START_ARM_TOLERANCE_RAD = 0.08
REPLAY_ZERO_ARM_TOLERANCE_RAD = 0.05
REPLAY_TRACKING_TOLERANCE_RAD = 0.25
REPLAY_TRACKING_CONSECUTIVE_FRAMES = 5
MAX_ARM_COMMAND_STEP_RAD = 0.20


@dataclass(frozen=True)
class TaskSummary:
    name: str
    success_count: int
    fail_count: int
    latest_episode: int


@dataclass(frozen=True)
class EpisodeSummary:
    task_name: str
    episode_index: int
    outcome: str
    path: Path
    num_steps: int | None
    duration: float | None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class ReplayEpisode:
    summary: EpisodeSummary
    actions: np.ndarray
    timestamps: np.ndarray
    qpos: np.ndarray

    @property
    def start_qpos(self) -> np.ndarray:
        return self.qpos[0].copy()


def _numbered_paths(task_dir: Path, outcome: str) -> list[tuple[int, Path]]:
    found = []
    directory = task_dir / outcome
    if not directory.is_dir():
        return found
    for path in directory.glob("*.hdf5"):
        match = EPISODE_PATTERN.fullmatch(path.name)
        if match is not None:
            found.append((int(match.group(1)), path))
    return found


def discover_tasks(data_root: Path) -> list[TaskSummary]:
    root = Path(data_root)
    if not root.is_dir():
        raise ValueError(f"data directory does not exist: {root}")
    tasks = []
    for task_dir in root.iterdir():
        if not task_dir.is_dir() or TASK_NAME_PATTERN.fullmatch(task_dir.name) is None:
            continue
        success = _numbered_paths(task_dir, "success")
        fail = _numbered_paths(task_dir, "fail")
        indices = [index for index, _path in (*success, *fail)]
        if not indices:
            continue
        tasks.append(
            TaskSummary(
                name=task_dir.name,
                success_count=len(success),
                fail_count=len(fail),
                latest_episode=max(indices),
            )
        )
    return sorted(tasks, key=lambda task: task.name)


def resolve_task_choice(tasks: Sequence[TaskSummary], choice: str) -> TaskSummary:
    value = choice.strip()
    if value.isdigit():
        index = int(value) - 1
        if 0 <= index < len(tasks):
            return tasks[index]
    for task in tasks:
        if task.name == value:
            return task
    raise ValueError(f"unknown task selection: {choice}")


def _read_episode_arrays(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    path = Path(path)
    if path.parent.name not in {"success", "fail"}:
        raise ValueError("only completed success/fail episodes may be replayed")
    try:
        with h5py.File(path, "r") as episode:
            validation = validate_episode_file(episode, path=path)
            actions = np.asarray(episode["action"], dtype=float)
            timestamps = np.asarray(episode["timestamp"], dtype=float)
            qpos = np.asarray(episode["observations/qpos"], dtype=float)
            control_hz = validation.control_hz
    except OSError as exc:
        raise ValueError(f"cannot read HDF5 episode: {exc}") from exc
    num_joints = validation.num_joints
    if actions.shape != (validation.num_steps, num_joints):
        raise ValueError(
            f"action must have shape [{validation.num_steps},{num_joints}], "
            f"got {actions.shape}"
        )
    if timestamps.shape != (actions.shape[0],):
        raise ValueError(
            f"timestamp must have shape [{actions.shape[0]}], got {timestamps.shape}"
        )
    if qpos.shape != actions.shape:
        raise ValueError(
            f"observations/qpos must have shape {actions.shape}, got {qpos.shape}"
        )
    if (
        not np.all(np.isfinite(actions))
        or not np.all(np.isfinite(timestamps))
        or not np.all(np.isfinite(qpos))
    ):
        raise ValueError(
            "action, timestamp, and observations/qpos must contain only finite values"
        )
    arm_indices = arm_indices_for_size(num_joints)
    gripper_indices = gripper_indices_for_size(num_joints)
    grippers = np.concatenate(
        (actions[:, gripper_indices], qpos[:, gripper_indices]), axis=0
    )
    if np.any(grippers < 0.0) or np.any(grippers > 1.0):
        raise ValueError("action and qpos grippers must stay within [0,1]")
    initial_delta = np.abs(actions[0, arm_indices] - qpos[0, arm_indices])
    if np.any(initial_delta > MAX_ARM_COMMAND_STEP_RAD + 1e-6):
        raise ValueError("initial arm action exceeds the 0.20 rad safety limit")
    if actions.shape[0] > 1:
        arm_delta = np.abs(np.diff(actions[:, arm_indices], axis=0))
        if np.any(arm_delta > MAX_ARM_COMMAND_STEP_RAD + 1e-6):
            raise ValueError("consecutive arm actions exceed the 0.20 rad safety limit")
    if timestamps.size > 1:
        intervals = np.diff(timestamps)
        if not np.all(intervals > 0):
            raise ValueError("timestamps must be strictly increasing")
        if np.any(intervals < (0.4 / control_hz) - 1e-9):
            raise ValueError(
                "timestamp interval is too short for the recorded control_hz"
            )
    return actions, timestamps - timestamps[0], qpos


def _inspect_episode_file(path: Path) -> tuple[int, float]:
    """Read only metadata, shapes, and timestamp endpoints for menu display."""
    try:
        with h5py.File(path, "r") as episode:
            validation = validate_episode_file(episode, path=path)
            action = episode["action"]
            timestamps = episode["timestamp"]
            qpos = episode["observations/qpos"]
            if action.shape != (validation.num_steps, validation.num_joints):
                raise ValueError(
                    f"action must have shape "
                    f"[{validation.num_steps},{validation.num_joints}], got {action.shape}"
                )
            if qpos.shape != action.shape:
                raise ValueError(
                    f"observations/qpos must have shape {action.shape}, got {qpos.shape}"
                )
            if timestamps.shape != (action.shape[0],):
                raise ValueError(
                    f"timestamp must have shape [{action.shape[0]}], got {timestamps.shape}"
                )
            first = float(timestamps[0])
            last = float(timestamps[-1])
            if not np.isfinite(first) or not np.isfinite(last):
                raise ValueError("timestamp endpoints must be finite")
            if action.shape[0] > 1 and last <= first:
                raise ValueError("timestamp endpoints must be increasing")
            return int(action.shape[0]), last - first
    except OSError as exc:
        raise ValueError(f"cannot read HDF5 episode: {exc}") from exc


def inspect_episode(
    path: Path, task_name: str, episode_index: int, outcome: str
) -> EpisodeSummary:
    try:
        num_steps, duration = _inspect_episode_file(path)
        return EpisodeSummary(
            task_name=task_name,
            episode_index=episode_index,
            outcome=outcome,
            path=Path(path),
            num_steps=num_steps,
            duration=duration,
        )
    except ValueError as exc:
        return EpisodeSummary(
            task_name=task_name,
            episode_index=episode_index,
            outcome=outcome,
            path=Path(path),
            num_steps=None,
            duration=None,
            error=str(exc),
        )


def discover_episodes(data_root: Path, task_name: str) -> list[EpisodeSummary]:
    if TASK_NAME_PATTERN.fullmatch(task_name) is None:
        raise ValueError(f"invalid task name: {task_name}")
    task_dir = Path(data_root) / task_name
    if not task_dir.is_dir():
        raise ValueError(f"task does not exist: {task_name}")
    by_index: dict[int, EpisodeSummary] = {}
    for outcome in ("success", "fail"):
        for episode_index, path in _numbered_paths(task_dir, outcome):
            if episode_index in by_index:
                raise ValueError(f"duplicate episode number: {episode_index:04d}")
            by_index[episode_index] = inspect_episode(
                path, task_name, episode_index, outcome
            )
    return [by_index[index] for index in sorted(by_index, reverse=True)]


def resolve_episode_choice(
    episodes: Sequence[EpisodeSummary], choice: str
) -> EpisodeSummary:
    value = choice.strip()
    if value.isdigit() and len(value) >= 4:
        episode_index = int(value)
        for episode in episodes:
            if episode.episode_index == episode_index:
                return episode
    elif value.isdigit():
        index = int(value) - 1
        if 0 <= index < len(episodes):
            return episodes[index]
    raise ValueError(f"unknown episode selection: {choice}")


T = TypeVar("T")


def page_items(
    values: Sequence[T], page: int, page_size: int = 20
) -> tuple[list[T], int, int]:
    if page_size <= 0:
        raise ValueError("page size must be positive")
    pages = max(1, (len(values) + page_size - 1) // page_size)
    if page < 1 or page > pages:
        raise ValueError(f"page must be between 1 and {pages}")
    start = (page - 1) * page_size
    return list(values[start : start + page_size]), page, pages


def load_episode(path: Path) -> ReplayEpisode:
    path = Path(path)
    match = EPISODE_PATTERN.fullmatch(path.name)
    if match is None:
        raise ValueError(f"invalid episode filename: {path.name}")
    actions, timestamps, qpos = _read_episode_arrays(path)
    summary = EpisodeSummary(
        task_name=path.parent.parent.name,
        episode_index=int(match.group(1)),
        outcome=path.parent.name,
        path=path,
        num_steps=int(actions.shape[0]),
        duration=float(timestamps[-1]),
    )
    return ReplayEpisode(
        summary=summary,
        actions=actions,
        timestamps=timestamps,
        qpos=qpos,
    )


def validate_episode_joint_limits(episode: ReplayEpisode, status: dict) -> np.ndarray:
    if "joint_limits" not in status:
        raise RuntimeError(
            "safety daemon does not publish joint limits; restart the safety daemon"
        )
    limits = np.asarray(status["joint_limits"], dtype=float)
    if (
        limits.shape != (episode.actions.shape[1], 2)
        or not np.all(np.isfinite(limits))
        or np.any(limits[:, 0] >= limits[:, 1])
    ):
        raise RuntimeError("safety daemon returned invalid joint_limits")
    for dataset_name, values in (("action", episode.actions), ("qpos", episode.qpos)):
        outside = (values < limits[:, 0]) | (values > limits[:, 1])
        if np.any(outside):
            frame, index = np.argwhere(outside)[0]
            side, joint = _joint_name(int(index))
            raise ValueError(
                f"{dataset_name} frame {int(frame) + 1} exceeds joint limits at "
                f"{side} {joint}: {values[frame, index]:.3f} not in "
                f"[{limits[index, 0]:.3f}, {limits[index, 1]:.3f}]"
            )
    return limits


def playback_deadlines(timestamps: np.ndarray, speed: float) -> np.ndarray:
    factor = float(speed)
    if not 0.1 <= factor <= 1.0:
        raise ValueError("speed must be between 0.1 and 1.0")
    values = np.asarray(timestamps, dtype=float)
    if values.ndim != 1 or values.size < 1 or not np.all(np.isfinite(values)):
        raise ValueError("timestamps must be a non-empty finite vector")
    normalized = values - values[0]
    if normalized.size > 1 and not np.all(np.diff(normalized) > 0):
        raise ValueError("timestamps must be strictly increasing")
    return normalized / factor


def replay_start_trajectory(current: np.ndarray, target: np.ndarray) -> list[np.ndarray]:
    return joint_trajectory(
        current,
        target,
        arm_step=0.10 / 30.0,
        gripper_step=0.5 / 30.0,
    )


def _arm_joint(index: int) -> tuple[str, int]:
    arm_names = ("LEFT", "RIGHT", "THIRD", "FOURTH")
    arm_number, local_index = divmod(index, 7)
    if arm_number >= len(arm_names) or local_index >= 6:
        raise ValueError(f"index {index} is not an arm joint")
    return arm_names[arm_number], local_index + 1


def _joint_name(index: int) -> tuple[str, str]:
    if index % 7 == 6:
        arm_names = ("LEFT", "RIGHT", "THIRD", "FOURTH")
        arm_number = index // 7
        if arm_number >= len(arm_names):
            raise ValueError(f"index {index} is not a known joint")
        return arm_names[arm_number], "gripper"
    side, joint = _arm_joint(index)
    return side, f"joint {joint}"


def _tracking_errors(actual: np.ndarray, expected: np.ndarray) -> np.ndarray:
    actual = np.asarray(actual, dtype=float)
    if actual.shape != expected.shape or not np.all(np.isfinite(actual)):
        raise RuntimeError("invalid follower feedback during replay")
    return np.abs(actual - expected)


def _lateness_limit(timestamps: np.ndarray, speed: float, rate_hz: float) -> float:
    sample_interval = (
        float(np.median(np.diff(timestamps)))
        if len(timestamps) > 1
        else 1.0 / rate_hz
    )
    return max(0.10, 2.0 * sample_interval / float(speed))


def _verify_target(
    robot,
    target: np.ndarray,
    session: SupervisedMotionSession,
    *,
    label: str,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    rate_hz: float,
    arm_tolerance: float = 0.03,
) -> None:
    valid_samples = 0
    deadline = clock() + 3.0
    arm_indices = arm_indices_for_size(target.size)
    gripper_indices = gripper_indices_for_size(target.size)
    last_error = np.full(target.size, np.inf)
    while clock() < deadline:
        session.check()
        valid, last_error = validate_feedback(
            robot.get_joint_state(),
            target,
            arm_tolerance=arm_tolerance,
        )
        valid_samples = valid_samples + 1 if valid else 0
        if valid_samples >= 3:
            return
        sleep(1.0 / rate_hz)
    worst_index = int(arm_indices[int(np.argmax(last_error[arm_indices]))])
    side, joint = _arm_joint(worst_index)
    raise RuntimeError(
        f"{label} verification timed out; max arm error "
        f"{last_error[worst_index]:.3f} rad at {side} joint {joint}, "
        f"max gripper error "
        f"{last_error[gripper_indices].max():.3f}"
    )


def run_replay(
    robot,
    safety,
    session_id: str,
    episode: ReplayEpisode,
    *,
    speed: float,
    choose_after: Callable[[], str],
    on_progress: Callable[[str, int, int, float], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    rate_hz: float = 30.0,
) -> str:
    """Execute one episode, hold, then either remain held or return to zero."""
    deadlines = playback_deadlines(episode.timestamps, speed)
    validate_episode_joint_limits(episode, safety.status())
    num_joints = episode.actions.shape[1]
    arm_indices = arm_indices_for_size(num_joints)
    zero_target = zero_target_for_size(num_joints)
    progress = on_progress or (lambda _phase, _index, _total, _elapsed: None)
    with SupervisedMotionSession(
        safety,
        session_id,
        operation="replay",
        clock=clock,
        sleep=sleep,
    ) as session:
        start_frames = replay_start_trajectory(
            robot.get_joint_state(), episode.start_qpos
        )
        for index, target in enumerate(start_frames, start=1):
            session.check()
            robot.command_joint_state(target)
            progress("move_to_start", index, len(start_frames), 0.0)
            sleep(1.0 / rate_hz)
        _verify_target(
            robot,
            episode.start_qpos,
            session,
            label="start pose",
            clock=clock,
            sleep=sleep,
            rate_hz=rate_hz,
            arm_tolerance=REPLAY_START_ARM_TOLERANCE_RAD,
        )

        started = clock()
        last_sent_at: float | None = None
        tracking_counts = np.zeros(num_joints, dtype=int)
        lateness_limit = _lateness_limit(episode.timestamps, speed, rate_hz)
        for index, (target, deadline) in enumerate(
            zip(episode.actions, deadlines), start=1
        ):
            interval = (
                0.0
                if index == 1
                else float(episode.timestamps[index - 1] - episode.timestamps[index - 2])
                / float(speed)
            )
            absolute_due = started + float(deadline)
            due = (
                absolute_due
                if last_sent_at is None
                else max(absolute_due, last_sent_at + interval)
            )
            while True:
                session.check()
                remaining = due - clock()
                if remaining <= 0:
                    break
                sleep(min(0.02, remaining))
            now = clock()
            lateness = now - absolute_due
            if lateness > lateness_limit + 1e-9:
                raise RuntimeError(
                    f"replay schedule late by {lateness:.3f}s at frame {index} "
                    f"(limit {lateness_limit:.3f}s)"
                )
            actual = np.asarray(robot.get_joint_state(), dtype=float)
            errors = _tracking_errors(actual, episode.qpos[index - 1])
            exceeded = errors > REPLAY_TRACKING_TOLERANCE_RAD
            tracking_counts[arm_indices] = np.where(
                exceeded[arm_indices],
                tracking_counts[arm_indices] + 1,
                0,
            )
            persistent = (
                tracking_counts[arm_indices] >= REPLAY_TRACKING_CONSECUTIVE_FRAMES
            )
            if np.any(persistent):
                candidates = arm_indices[np.flatnonzero(persistent)]
                worst_index = int(candidates[np.argmax(errors[candidates])])
                side, joint = _arm_joint(worst_index)
                raise RuntimeError(
                    f"replay tracking error at frame {index}: {side} joint {joint}, "
                    f"expected {episode.qpos[index - 1, worst_index]:.3f}, "
                    f"actual {actual[worst_index]:.3f}, "
                    f"error {errors[worst_index]:.3f} rad"
                )
            last_sent_at = now
            robot.command_joint_state(target)
            progress("replay", index, len(episode.actions), now - started)

        sample_period = (
            float(np.median(np.diff(episode.timestamps)))
            if len(episode.timestamps) > 1
            else 1.0 / rate_hz
        ) / float(speed)
        dwell_deadline = clock() + sample_period
        while clock() < dwell_deadline:
            session.check()
            sleep(min(0.02, dwell_deadline - clock()))

        session.hold("replay complete")
        choice = choose_after().strip().lower()
        if choice in {"", "hold"}:
            return "hold"
        if choice != "zero":
            raise ValueError("post-replay choice must be hold or zero")

        session.activate()
        zero_frames = joint_trajectory(robot.get_joint_state(), zero_target)
        for index, target in enumerate(zero_frames, start=1):
            session.check()
            robot.command_joint_state(target)
            progress("return_zero", index, len(zero_frames), 0.0)
            sleep(1.0 / rate_hz)
        robot.command_joint_state(zero_target.copy())
        _verify_target(
            robot,
            zero_target,
            session,
            label="zero",
            clock=clock,
            sleep=sleep,
            rate_hz=rate_hz,
            arm_tolerance=REPLAY_ZERO_ARM_TOLERANCE_RAD,
        )
        session.hold("replay zero complete")
        return "zero"
