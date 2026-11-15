"""Pick a small, well-conditioned set of calibration poses.

Hand-eye calibration needs *rotation diversity*: two or more relative motions
whose rotation axes are not parallel, with non-trivial angles.  Taking the first
N frames, or N random frames, routinely produces a badly conditioned or plain
degenerate problem.  The greedy farthest-point selection here spreads the
chosen poses over SE(3), and ``axis_conditioning`` reports how well the chosen
motions constrain the rotation.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

import numpy as np


def invert(T: np.ndarray) -> np.ndarray:
    T = np.asarray(T, dtype=np.float64).reshape(4, 4)
    R = T[:3, :3]
    t = T[:3, 3]
    out = np.eye(4, dtype=np.float64)
    out[:3, :3] = R.T
    out[:3, 3] = -(R.T @ t)
    return out


def rotation_angle(R: np.ndarray) -> float:
    trace = float(np.trace(np.asarray(R, dtype=np.float64)[:3, :3]))
    return float(np.arccos(np.clip((trace - 1.0) * 0.5, -1.0, 1.0)))


def _log_rotation(R: np.ndarray) -> np.ndarray:
    """Rotation vector (axis * angle) of a rotation matrix."""
    R = np.asarray(R, dtype=np.float64)[:3, :3]
    cos_angle = np.clip((np.trace(R) - 1.0) * 0.5, -1.0, 1.0)
    angle = float(np.arccos(cos_angle))
    if angle < 1e-9:
        return np.zeros(3, dtype=np.float64)
    if abs(angle - np.pi) < 1e-6:
        # Near pi the skew part vanishes; recover the axis from R + I.
        M = (R + np.eye(3)) * 0.5
        axis = np.sqrt(np.clip(np.diag(M), 0.0, None))
        if axis[0] < 1e-9:
            axis[0] = 0.0
        elif R[0, 1] < 0:
            axis[0] = -axis[0]
        if axis[1] < 1e-9:
            axis[1] = 0.0
        elif R[0, 2] < 0:
            axis[1] = -axis[1]
        if axis[2] < 1e-9:
            axis[2] = 0.0
        elif R[1, 2] < 0:
            axis[2] = -axis[2]
        norm = float(np.linalg.norm(axis))
        if norm < 1e-12:
            return np.array([np.pi, 0.0, 0.0], dtype=np.float64)
        return axis / norm * angle
    axis = np.array(
        [R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]],
        dtype=np.float64,
    ) / (2.0 * np.sin(angle))
    return axis * angle


def pose_distance(T_a: np.ndarray, T_b: np.ndarray, translation_scale: float = 5.0) -> float:
    """A metric in SE(3): rotation (rad) plus scaled translation (m)."""
    T_a = np.asarray(T_a, dtype=np.float64)
    T_b = np.asarray(T_b, dtype=np.float64)
    angle = rotation_angle(T_a[:3, :3].T @ T_b[:3, :3])
    offset = float(np.linalg.norm(T_a[:3, 3] - T_b[:3, 3]))
    return float(angle) + float(translation_scale) * offset


def greedy_diverse_poses(
    poses: Sequence[np.ndarray],
    count: int,
    translation_scale: float = 5.0,
    must_include: Iterable[int] = (),
) -> List[int]:
    """Farthest-point sampling over a list of candidate poses.

    Returns indices into ``poses``.  The first seed is the pose with the largest
    total distance to the others (most "central" is avoided on purpose: we want
    spread, not a median).
    """
    if count <= 0:
        return []
    poses = [np.asarray(pose, dtype=np.float64) for pose in poses]
    if not poses:
        return []
    count = min(int(count), len(poses))

    if len(poses) == 1:
        return [0]

    # Seed with the pair that is furthest apart, then keep adding the candidate
    # that maximises its distance to the already chosen set.
    distance_matrix = np.zeros((len(poses), len(poses)), dtype=np.float64)
    for i in range(len(poses)):
        for j in range(i + 1, len(poses)):
            distance = pose_distance(poses[i], poses[j], translation_scale)
            distance_matrix[i, j] = distance
            distance_matrix[j, i] = distance

    seed = int(np.argmax(distance_matrix.max(axis=1)))
    selected = [seed]
    for index in must_include:
        index = int(index)
        if 0 <= index < len(poses) and index not in selected:
            selected.append(index)
    if len(selected) >= count:
        return selected[:count]

    best_distance = distance_matrix[selected[0]].copy()
    for index in selected[1:]:
        best_distance = np.minimum(best_distance, distance_matrix[index])
    np.put(best_distance, selected, -1.0)

    while len(selected) < count:
        candidate = int(np.argmax(best_distance))
        if best_distance[candidate] < 0.0:
            break
        selected.append(candidate)
        best_distance = np.minimum(best_distance, distance_matrix[candidate])
        np.put(best_distance, selected, -1.0)
    return selected


def axis_conditioning(poses: Sequence[np.ndarray]) -> Tuple[np.ndarray, float]:
    """Conditioning of the rotation excitation, plus the missing direction.

    Returns ``(singular_values, weakest_axis)`` where ``singular_values`` come
    from the stacked relative rotation vectors (axis * angle) of ``poses``.  The
    first value is always non-zero; a small third value means the data hardly
    twists about some direction, which is what makes ``T_base_cam`` loosely
    constrained.

    ``weakest_axis`` is that under-excited direction.  The relative rotations
    are formed as ``R_i @ R_j^T`` so that *every* row lives in the frame the
    poses are given in -- the arm base frame when fed with ``T_base_link``.
    (Using ``R_i^T R_j`` instead would express each row in a different link
    frame, and the singular vectors would then have no single physical meaning.)
    "Rotate the board about roughly this axis" is the concrete instruction for
    the next recording session.
    """
    axes: List[np.ndarray] = []
    for i in range(len(poses)):
        for j in range(i + 1, len(poses)):
            # Base-frame relative rotation; see the docstring.
            relative_rotation = (
                np.asarray(poses[i], dtype=np.float64)[:3, :3]
                @ np.asarray(poses[j], dtype=np.float64)[:3, :3].T
            )
            rotation_vector = _log_rotation(relative_rotation)
            if np.linalg.norm(rotation_vector) > np.deg2rad(2.0):
                axes.append(rotation_vector)
    if not axes:
        return np.zeros(3, dtype=np.float64), np.zeros(3, dtype=np.float64)
    matrix = np.stack(axes, axis=0)
    # full_matrices=False: with n poses this matrix has one row per pair (up to
    # n(n-1)/2 of them), and the default full U would be that many rows *squared*.
    # A thousand poses asked for 2 TiB before this was fixed.
    _u, singular_values, vt = np.linalg.svd(matrix, full_matrices=False)
    weakest = np.asarray(vt[-1], dtype=np.float64)
    norm = float(np.linalg.norm(weakest))
    if norm > 0.0:
        weakest = weakest / norm
    return singular_values, weakest
