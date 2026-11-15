"""Pure trajectory and feedback checks for supervised multi-arm homing."""

from __future__ import annotations

import numpy as np

ARM_INDICES = np.array([0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12])
GRIPPER_INDICES = np.array([6, 13])
ZERO_TARGET = np.array(
    [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1], dtype=float
)


def arm_indices_for_size(num_joints: int) -> np.ndarray:
    if num_joints < 7 or num_joints % 7:
        raise ValueError("joint vector length must be a positive multiple of 7")
    return np.asarray(
        [index for index in range(num_joints) if index % 7 != 6], dtype=int
    )


def gripper_indices_for_size(num_joints: int) -> np.ndarray:
    if num_joints < 7 or num_joints % 7:
        raise ValueError("joint vector length must be a positive multiple of 7")
    return np.arange(6, num_joints, 7, dtype=int)


def zero_target_for_size(num_joints: int) -> np.ndarray:
    target = np.zeros(num_joints, dtype=float)
    target[gripper_indices_for_size(num_joints)] = 1.0
    return target


def checked_joints(value, label: str) -> np.ndarray:
    joints = np.asarray(value, dtype=float)
    if joints.ndim != 1 or joints.size < 7 or joints.size % 7:
        raise ValueError(f"{label} must contain 7N joint values")
    if not np.all(np.isfinite(joints)):
        raise ValueError(f"{label} must contain only finite joint values")
    return joints


def joint_trajectory(
    current,
    target=ZERO_TARGET,
    arm_step: float = 0.01,
    gripper_step: float = 0.02,
) -> list[np.ndarray]:
    start = checked_joints(current, "current")
    goal = checked_joints(target, "target")
    if arm_step <= 0 or gripper_step <= 0:
        raise ValueError("trajectory step limits must be positive")
    if start.shape != goal.shape:
        raise ValueError("current and target must have the same shape")
    limits = np.full(start.size, arm_step, dtype=float)
    limits[gripper_indices_for_size(start.size)] = gripper_step
    steps = max(1, int(np.max(np.ceil(np.abs(goal - start) / limits))))
    return [frame.copy() for frame in np.linspace(start, goal, steps + 1)[1:]]


def validate_feedback(
    feedback,
    target=ZERO_TARGET,
    arm_tolerance: float = 0.03,
    gripper_tolerance: float = 0.05,
) -> tuple[bool, np.ndarray]:
    actual = checked_joints(feedback, "feedback")
    goal = checked_joints(target, "target")
    if actual.shape != goal.shape:
        raise ValueError("feedback and target must have the same shape")
    error = np.abs(actual - goal)
    arm_indices = arm_indices_for_size(actual.size)
    gripper_indices = gripper_indices_for_size(actual.size)
    valid = bool(
        np.all(error[arm_indices] <= arm_tolerance)
        and np.all(error[gripper_indices] <= gripper_tolerance)
    )
    return valid, error
