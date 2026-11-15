"""Bounded leader-to-follower synchronization outside episode recording."""

from __future__ import annotations

import time
from typing import Any, Callable

import numpy as np

SYNC_ARM_SPEED = 0.20
ARM_ALIGNMENT_TOLERANCE_RAD = 0.08
TRIGGER_PRESSED_THRESHOLD = 0.05
TRIGGER_RELEASED_THRESHOLD = 0.95
TRIGGER_HOLD_SECONDS = 0.15


def wait_for_recording_trigger(env, agent, commands, emit, clock=time.monotonic):
    """Require released -> pressed -> released on all leaders before recording."""
    observation = env.get_obs()
    commanded = _checked(observation["joint_positions"], "follower").copy()
    arm_indices, gripper_indices = _indices(commanded.size)
    stable_since = None
    stage = "release_first"
    last_emit = float("-inf")
    emit({"type": "waiting_for_trigger"})
    while True:
        if _pending_exit(commands):
            return {"ok": False, "observation": observation, "exit": True}
        target = _checked(agent.act(observation), "leader").copy()
        current = _checked(observation["joint_positions"], "follower")
        if target.shape != commanded.shape or current.shape != commanded.shape:
            raise ValueError("leader/follower shapes changed while waiting for triggers")
        trigger_values = target[gripper_indices]
        if np.any(trigger_values < 0) or np.any(trigger_values > 1):
            raise ValueError("leader triggers must be normalized to 0..1")
        now = clock()
        error = np.abs(target - current)
        aligned = (np.all(error[arm_indices] <= ARM_ALIGNMENT_TOLERANCE_RAD)
                   and np.all(error[gripper_indices] <= 0.10))
        # The installed GELLO mapping is 1 = released/open, 0 = squeezed/closed.
        # A level at startup is never a complete operator readiness gesture.
        condition = (np.all(trigger_values <= TRIGGER_PRESSED_THRESHOLD) if stage == "press"
                     else np.all(trigger_values >= TRIGGER_RELEASED_THRESHOLD))
        if stage == "release":
            condition = condition and aligned
        stable_since = (now if stable_since is None else stable_since) if condition else None
        complete = stable_since is not None and now - stable_since >= TRIGGER_HOLD_SECONDS
        if complete and stage != "release":
            stage = "press" if stage == "release_first" else "release"
            stable_since = None
            last_emit = float("-inf")
            complete = False
        if now - last_emit >= 0.2:
            emit({"type": "trigger_progress", "values": trigger_values.tolist(),
                  "stage": stage, "aligned": bool(aligned), "leader_target": target.tolist()})
            last_emit = now
        if complete:
            return {"ok": True, "observation": observation, "exit": False}
        commanded = sync_step(commanded, target,
                              rate_hz=getattr(env, "control_rate_hz", 30),
                              arm_speed=SYNC_ARM_SPEED)
        observation = env.step(commanded)


def _checked(value: Any, name: str) -> np.ndarray:
    out = np.asarray(value, dtype=float)
    if out.ndim != 1 or out.size == 0 or out.size % 7 or not np.all(np.isfinite(out)):
        raise ValueError(f"{name} must contain 7 values per arm")
    return out


def _indices(size: int) -> tuple[np.ndarray, np.ndarray]:
    grippers = np.arange(6, size, 7, dtype=int)
    arms = np.array([index for index in range(size) if index not in set(grippers)], dtype=int)
    return arms, grippers


def sync_step(
    current: Any,
    target: Any,
    rate_hz: float = 30.0,
    arm_speed: float = 0.10,
    gripper_speed: float = 0.5,
) -> np.ndarray:
    """Move one control tick toward the latest leader pose."""
    if rate_hz <= 0 or arm_speed <= 0 or gripper_speed <= 0:
        raise ValueError("sync rate and speed limits must be positive")
    current_array = _checked(current, "current")
    target_array = _checked(target, "target").copy()
    if target_array.shape != current_array.shape:
        raise ValueError("current and target must have identical shapes")
    _, gripper_indices = _indices(target_array.size)
    target_array[gripper_indices] = np.clip(target_array[gripper_indices], 0.0, 1.0)
    limits = np.full(target_array.size, arm_speed / rate_hz)
    limits[gripper_indices] = gripper_speed / rate_hz
    return current_array + np.clip(target_array - current_array, -limits, limits)


def _pending_exit(commands: Any) -> bool:
    """Consume an exit request without consuming future state-machine commands."""
    # Queue has no public peek operation. Holding its mutex makes this inspection
    # atomic; get_nowait() is deliberately called after releasing the mutex.
    with commands.mutex:
        command = commands.queue[0] if commands.queue else None
    if command is None or command.get("type") not in {"exit_task", "shutdown"}:
        return False
    commands.get_nowait()
    return True


def _arm_joint(index: int) -> tuple[str, int]:
    names = ("LEFT", "RIGHT", "THIRD", "FOURTH")
    arm = index // 7
    joint = index % 7
    if arm < len(names) and joint < 6:
        return names[arm], joint + 1
    raise ValueError(f"index {index} is not an arm joint")


def synchronize_followers(
    env: Any,
    agent: Any,
    commands: Any,
    emit: Callable[[dict[str, Any]], None],
    timeout_s: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Slowly track the live leader pose until both arms are stably aligned."""
    started = clock()
    stable_samples = 0
    next_diagnostic = started
    observation = env.get_obs()
    commanded = _checked(observation["joint_positions"], "follower").copy()
    arm_indices, gripper_indices = _indices(commanded.size)
    emit({"type": "sync_started"})
    while clock() - started < timeout_s:
        if _pending_exit(commands):
            return {"ok": False, "observation": observation, "exit": True}
        current = _checked(observation["joint_positions"], "follower")
        target = _checked(agent.act(observation), "leader")
        target[gripper_indices] = np.clip(target[gripper_indices], 0.0, 1.0)
        error = np.abs(target - current)
        if clock() >= next_diagnostic:
            emit({"type": "alignment_target", "leader_target": target.tolist(),
                  "position": current.tolist(), "error": (target - current).tolist()})
            next_diagnostic = clock() + 0.2
        worst_index = int(arm_indices[int(np.argmax(error[arm_indices]))])
        worst_side, worst_joint = _arm_joint(worst_index)
        aligned = np.all(error[arm_indices] <= ARM_ALIGNMENT_TOLERANCE_RAD) and np.all(
            error[gripper_indices] <= 0.10
        )
        stable_samples = stable_samples + 1 if aligned else 0
        emit(
            {
                "type": "sync_progress",
                "max_arm_error": float(error[arm_indices].max()),
                "max_gripper_error": float(error[gripper_indices].max()),
                "worst_side": worst_side,
                "worst_joint": worst_joint,
                "worst_target": float(target[worst_index]),
                "worst_measured": float(current[worst_index]),
                "elapsed": clock() - started,
            }
        )
        if stable_samples >= 3:
            emit({"type": "sync_completed", "elapsed": clock() - started})
            return {"ok": True, "observation": observation, "exit": False}

        commanded = sync_step(commanded, target, rate_hz=getattr(env, "control_rate_hz", 30), arm_speed=SYNC_ARM_SPEED)
        observation = env.step(commanded)
    emit({"type": "sync_failed", "message": "synchronization timed out"})
    return {"ok": False, "observation": observation, "exit": False}
