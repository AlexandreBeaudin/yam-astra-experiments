"""Eye-to-hand solver: fixed camera, calibration board held by the gripper.

Model
-----
For every sample ``i`` there are two measured rigid transforms::

    A_i = T_base_link     from forward kinematics (joint angles)
    B_i = T_cam_target    from ChArUco detection + solvePnP

and two unknowns::

    X = T_base_cam        what we want
    Y = T_link_target     board pose in the gripper frame (nuisance)

which must satisfy, for every sample::

    A_i @ Y = X @ B_i

Why not ``cv2.calibrateHandEye``
--------------------------------
OpenCV's helper solves the *eye-in-hand* problem ``A_ij X = X B_ij`` with
``A_ij = A_i^-1 A_j`` and ``B_ij = B_i B_j^-1``.  Here the correct relative
motion pairing is ``B_ij = B_i^-1 B_j`` (different!), because the board rides
on the arm instead of sitting in the scene.  Feeding the wrong pairing produces
a solution that looks plausible and is wrong, so the solver is implemented
explicitly and checked in ``selftest.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .selection import _log_rotation, invert, rotation_angle


class HandEyeError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# small SE(3) helpers
# --------------------------------------------------------------------------
def make_transform(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = np.asarray(R, dtype=np.float64).reshape(3, 3)
    T[:3, 3] = np.asarray(t, dtype=np.float64).reshape(3)
    return T


def skew(vector: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(vector, dtype=np.float64).reshape(3)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=np.float64)


def exp_so3(rotvec: np.ndarray) -> np.ndarray:
    rotvec = np.asarray(rotvec, dtype=np.float64).reshape(3)
    angle = float(np.linalg.norm(rotvec))
    if angle < 1e-12:
        return np.eye(3, dtype=np.float64)
    axis = rotvec / angle
    K = skew(axis)
    return (
        np.eye(3, dtype=np.float64)
        + np.sin(angle) * K
        + (1.0 - np.cos(angle)) * (K @ K)
    )


def project_to_so3(matrix: np.ndarray) -> np.ndarray:
    U, _, Vt = np.linalg.svd(np.asarray(matrix, dtype=np.float64).reshape(3, 3))
    R = U @ Vt
    if np.linalg.det(R) < 0.0:
        U = U.copy()
        U[:, -1] *= -1.0
        R = U @ Vt
    return R


def average_transforms(transforms: Sequence[np.ndarray]) -> np.ndarray:
    transforms = [np.asarray(T, dtype=np.float64) for T in transforms]
    if not transforms:
        raise HandEyeError("average_transforms() needs at least one transform")
    if len(transforms) == 1:
        return transforms[0].copy()
    R = project_to_so3(np.sum([T[:3, :3] for T in transforms], axis=0))
    t = np.mean([T[:3, 3] for T in transforms], axis=0)
    return make_transform(R, t)


# --------------------------------------------------------------------------
# closed-form initialisation
# --------------------------------------------------------------------------
def relative_pairs(
    A_list: Sequence[np.ndarray], B_list: Sequence[np.ndarray]
) -> Tuple[List[np.ndarray], List[np.ndarray]]:
    """Build ``(A_i^-1 A_j, B_i^-1 B_j)`` for every i < j.

    With ``A_i Y = X B_i`` this gives ``A_ij Y = Y B_ij``, i.e. the standard
    ``AX = XB`` shape with ``X := Y``.
    """
    if len(A_list) != len(B_list):
        raise HandEyeError("A_list and B_list must have the same length")
    A_rel: List[np.ndarray] = []
    B_rel: List[np.ndarray] = []
    for i in range(len(A_list)):
        A_i_inv = invert(A_list[i])
        B_i_inv = invert(B_list[i])
        for j in range(i + 1, len(A_list)):
            A_rel.append(A_i_inv @ np.asarray(A_list[j], dtype=np.float64))
            B_rel.append(B_i_inv @ np.asarray(B_list[j], dtype=np.float64))
    return A_rel, B_rel


def _rotation_from_kronecker(
    A_rel: Sequence[np.ndarray], B_rel: Sequence[np.ndarray], sign: float = 1.0
) -> Tuple[np.ndarray, np.ndarray]:
    """Least-squares ``R_Y`` for ``R_A R_Y = R_Y R_B`` plus singular values."""
    rows: List[np.ndarray] = []
    identity = np.eye(3, dtype=np.float64)
    for A, B in zip(A_rel, B_rel):
        R_A = np.asarray(A, dtype=np.float64)[:3, :3]
        R_B = np.asarray(B, dtype=np.float64)[:3, :3]
        rows.append(
            np.kron(identity, R_A) - np.kron(R_B.T, identity)
        )
    matrix = np.concatenate(rows, axis=0)
    # full_matrices=False matters: with n frames the stacked Kronecker matrix has
    # 9 * n(n-1)/2 rows, and the default full U would be that squared -- 357 GiB
    # for 221 frames.  Only the last right singular vector is used.
    _, singular_values, Vt = np.linalg.svd(matrix, full_matrices=False)
    vector = float(sign) * Vt[-1]
    # vec() stacks matrix columns, so reshape in Fortran order.
    raw = vector.reshape((3, 3), order="F")
    return project_to_so3(raw), singular_values


def _translation_from_relative(
    A_rel: Sequence[np.ndarray], B_rel: Sequence[np.ndarray], R_Y: np.ndarray
) -> np.ndarray:
    """Solve ``(R_A - I) t_Y = R_Y t_B - t_A`` in a least-squares sense."""
    identity = np.eye(3, dtype=np.float64)
    lhs_rows: List[np.ndarray] = []
    rhs_rows: List[np.ndarray] = []
    for A, B in zip(A_rel, B_rel):
        A = np.asarray(A, dtype=np.float64)
        B = np.asarray(B, dtype=np.float64)
        lhs_rows.append(A[:3, :3] - identity)
        rhs_rows.append(R_Y @ B[:3, 3] - A[:3, 3])
    lhs = np.concatenate(lhs_rows, axis=0)
    rhs = np.concatenate(rhs_rows, axis=0)
    solution, *_ = np.linalg.lstsq(lhs, rhs, rcond=None)
    return solution.reshape(3)


def _X_from_samples(
    A_list: Sequence[np.ndarray], B_list: Sequence[np.ndarray], Y: np.ndarray
) -> np.ndarray:
    """``X = A_i Y B_i^-1`` for every sample, averaged."""
    estimates = [
        np.asarray(A, dtype=np.float64) @ Y @ invert(B)
        for A, B in zip(A_list, B_list)
    ]
    return average_transforms(estimates)


# --------------------------------------------------------------------------
# refinement
# --------------------------------------------------------------------------
def _residual_vector(
    A_list: Sequence[np.ndarray],
    B_list: Sequence[np.ndarray],
    X: np.ndarray,
    Y: np.ndarray,
) -> np.ndarray:
    """Stack ``log(X^-1 A_i Y B_i^-1)``; zero exactly when the model fits."""
    X_inv = invert(X)
    residuals: List[float] = []
    for A, B in zip(A_list, B_list):
        error = X_inv @ np.asarray(A, dtype=np.float64) @ Y @ invert(B)
        residuals.extend(_log_rotation(error[:3, :3]).tolist())
        residuals.extend(error[:3, 3].tolist())
    return np.asarray(residuals, dtype=np.float64)


def _pack(X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    return np.concatenate(
        [
            _log_rotation(X[:3, :3]),
            X[:3, 3],
            _log_rotation(Y[:3, :3]),
            Y[:3, 3],
        ]
    )


def _unpack(params: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    params = np.asarray(params, dtype=np.float64).reshape(12)
    X = make_transform(exp_so3(params[0:3]), params[3:6])
    Y = make_transform(exp_so3(params[6:9]), params[9:12])
    return X, Y


def _refine(
    A_list: Sequence[np.ndarray],
    B_list: Sequence[np.ndarray],
    X0: np.ndarray,
    Y0: np.ndarray,
    max_iterations: int = 200,
) -> Tuple[np.ndarray, np.ndarray, float]:
    start = _pack(X0, Y0)
    cost0 = float(np.sum(_residual_vector(A_list, B_list, X0, Y0) ** 2))
    try:
        from scipy.optimize import least_squares
    except ImportError:
        return X0, Y0, cost0

    def fun(params: np.ndarray) -> np.ndarray:
        X, Y = _unpack(params)
        return _residual_vector(A_list, B_list, X, Y)

    solution = least_squares(
        fun,
        start,
        method="lm",
        max_nfev=int(max_iterations),
        xtol=1e-14,
        ftol=1e-14,
        gtol=1e-14,
    )
    X, Y = _unpack(solution.x)
    cost = float(np.sum(solution.fun ** 2))
    if cost > cost0:
        return X0, Y0, cost0
    return X, Y, cost


@dataclass
class HandEyeResult:
    X_base_cam: np.ndarray
    Y_link_target: np.ndarray
    num_samples: int
    num_relative_pairs: int
    rmse_translation_m: float
    rmse_rotation_rad: float
    max_translation_residual_m: float
    max_rotation_residual_rad: float
    base_target_std_m: np.ndarray
    base_target_range_m: np.ndarray
    base_target_mean_m: np.ndarray
    rotation_drift_rad: np.ndarray
    axis_singular_values: np.ndarray
    kronecker_tail_singular_values: np.ndarray
    per_sample: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def stable(self) -> bool:
        """Whether the model actually explains the measurements.

        Note this uses the *fit residual*, not the spread of
        ``T_base_target``.  In eye-to-hand the target rides on the arm, so its
        pose in the base frame legitimately changes by tens of degrees over the
        episode; the wrist-camera pipeline's "target in base must be constant"
        test is meaningless here.  What must be small is the residual of
        ``A_i Y = X B_i``.
        """
        return bool(
            float(self.rmse_rotation_rad) < np.deg2rad(0.5)
            and float(self.rmse_translation_m) < 0.005
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "T_base_cam": np.asarray(self.X_base_cam, dtype=float).tolist(),
            "T_link_target": np.asarray(self.Y_link_target, dtype=float).tolist(),
            "T_cam_base": np.linalg.inv(np.asarray(self.X_base_cam, dtype=float)).tolist(),
            "num_samples": int(self.num_samples),
            "num_relative_pairs": int(self.num_relative_pairs),
            "rmse_translation_m": float(self.rmse_translation_m),
            "rmse_rotation_rad": float(self.rmse_rotation_rad),
            "rmse_rotation_deg": float(np.rad2deg(self.rmse_rotation_rad)),
            "max_translation_residual_m": float(self.max_translation_residual_m),
            "max_rotation_residual_rad": float(self.max_rotation_residual_rad),
            "base_target_std_m": np.asarray(self.base_target_std_m, dtype=float).tolist(),
            "base_target_range_m": np.asarray(self.base_target_range_m, dtype=float).tolist(),
            "base_target_mean_m": np.asarray(self.base_target_mean_m, dtype=float).tolist(),
            "rotation_drift_rad": np.asarray(self.rotation_drift_rad, dtype=float).tolist(),
            "axis_singular_values": np.asarray(self.axis_singular_values, dtype=float).tolist(),
            "kronecker_tail_singular_values": np.asarray(
                self.kronecker_tail_singular_values, dtype=float
            ).tolist(),
            "stable": self.stable,
            "note_base_target": (
                "T_base_target is expected to vary in eye-to-hand (the board "
                "rides on the arm); judge quality by rmse_* and loo_* instead."
            ),
            "meta": self.meta,
            "samples": self.per_sample,
        }


def leave_one_out(
    A_list: Sequence[np.ndarray], B_list: Sequence[np.ndarray]
) -> np.ndarray:
    """Re-solve with each sample removed and measure how much ``X`` moves.

    This is the honest validation for eye-to-hand: the fit residual can look
    acceptable while a single influential sample drags the answer around.
    Returns an ``(n, 2)`` array of ``[rotation_deg, translation_mm]`` errors
    relative to the full-data solution.
    """
    A_list = [np.asarray(A, dtype=np.float64) for A in A_list]
    B_list = [np.asarray(B, dtype=np.float64) for B in B_list]
    if len(A_list) < 4:
        return np.zeros((len(A_list), 2), dtype=np.float64)
    full = solve_eye_to_hand(A_list, B_list).X_base_cam
    errors = np.zeros((len(A_list), 2), dtype=np.float64)
    for index in range(len(A_list)):
        subset_A = A_list[:index] + A_list[index + 1 :]
        subset_B = B_list[:index] + B_list[index + 1 :]
        try:
            X = solve_eye_to_hand(subset_A, subset_B).X_base_cam
        except HandEyeError:
            errors[index] = [np.inf, np.inf]
            continue
        delta = invert(X) @ full
        errors[index, 0] = float(np.rad2deg(rotation_angle(delta[:3, :3])))
        errors[index, 1] = float(np.linalg.norm(delta[:3, 3]) * 1000.0)
    return errors


def split_half_check(
    A_list: Sequence[np.ndarray], B_list: Sequence[np.ndarray]
) -> Tuple[float, float]:
    """Fit the first and second halves independently and compare ``X``.

    Returns ``(rotation_deg, translation_mm)`` of ``X_first^-1 X_second``.  On a
    rigid rig with a well-excited motion this is a few millimetres and a
    fraction of a degree.  A large value means the answer depends on *which part
    of the episode* you look at -- the signature of a weakly excited rotation
    axis, an inaccurate kinematic model, or the board shifting in the gripper.
    """
    A_list = [np.asarray(A, dtype=np.float64) for A in A_list]
    B_list = [np.asarray(B, dtype=np.float64) for B in B_list]
    if len(A_list) < 8:
        return float("nan"), float("nan")
    middle = len(A_list) // 2
    first = solve_eye_to_hand(A_list[:middle], B_list[:middle]).X_base_cam
    second = solve_eye_to_hand(A_list[middle:], B_list[middle:]).X_base_cam
    delta = invert(first) @ second
    return float(np.rad2deg(rotation_angle(delta[:3, :3]))), float(
        np.linalg.norm(delta[:3, 3]) * 1000.0
    )


def solve_robust(
    A_list: Sequence[np.ndarray],
    B_list: Sequence[np.ndarray],
    meta: Optional[Dict[str, Any]] = None,
    per_sample_extra: Optional[Sequence[Dict[str, Any]]] = None,
    outlier_k: Optional[float] = 3.0,
    max_rounds: int = 3,
    min_samples: int = 6,
) -> Tuple[HandEyeResult, np.ndarray]:
    """Solve, drop samples whose residual is far above the median, repeat.

    Returns ``(result, keep_mask)`` where ``keep_mask`` is over the input
    ordering.  Partially visible boards (few ChArUco corners) produce
    low-reprojection-error but systematically wrong poses, and the diversity
    sampler loves them because they sit at the edge of the view.
    """
    A_list = [np.asarray(A, dtype=np.float64) for A in A_list]
    B_list = [np.asarray(B, dtype=np.float64) for B in B_list]
    extras = list(per_sample_extra) if per_sample_extra is not None else None
    keep = np.ones(len(A_list), dtype=bool)

    for _ in range(max(1, int(max_rounds))):
        current_A = [A for A, flag in zip(A_list, keep) if flag]
        current_B = [B for B, flag in zip(B_list, keep) if flag]
        current_extra = (
            [e for e, flag in zip(extras, keep) if flag] if extras is not None else None
        )
        if len(current_A) < max(6, int(min_samples)):
            break
        result = solve_eye_to_hand(
            current_A, current_B, meta=meta, per_sample_extra=current_extra
        )
        if outlier_k is None:
            break
        residual = np.asarray(
            [
                np.hypot(
                    np.rad2deg(sample["residual_rotation_rad"]),
                    sample["residual_translation_m"] * 1000.0,
                )
                for sample in result.per_sample
            ],
            dtype=np.float64,
        )
        median = float(np.median(residual))
        if median <= 0.0:
            break
        keep_current = residual <= float(outlier_k) * median
        if bool(np.all(keep_current)):
            break
        indices = np.flatnonzero(keep)
        keep = np.zeros_like(keep)
        keep[indices[keep_current]] = True
        if int(np.count_nonzero(keep)) < max(6, int(min_samples)):
            keep[indices] = True
            break

    current_A = [A for A, flag in zip(A_list, keep) if flag]
    current_B = [B for B, flag in zip(B_list, keep) if flag]
    current_extra = (
        [e for e, flag in zip(extras, keep) if flag] if extras is not None else None
    )
    result = solve_eye_to_hand(
        current_A, current_B, meta=meta, per_sample_extra=current_extra
    )
    return result, keep


def solve_with_joint_offsets(
    qpos_list: Sequence[np.ndarray],
    B_list: Sequence[np.ndarray],
    fk,
    offset_indices: Sequence[int],
    X0: np.ndarray,
    Y0: np.ndarray,
    max_iterations: int = 300,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Jointly refine ``(X, Y)`` and per-joint zero offsets.

    ``fk(joint_rad) -> T_base_link``; the offsets are added to the measured
    joint angles before ``fk``.  Joint 1 and the last joint are deliberately
    *not* estimable: a joint-1 offset rotates the whole base frame (absorbed by
    ``X``) and a last-joint offset simply rotates ``Y``.  Including their
    indices makes the problem rank deficient, so the caller must exclude them.

    Returns ``(offsets_rad, X, Y, A_list)``.
    """
    import numpy as _np

    offset_indices = [int(i) for i in offset_indices]
    qpos_list = [_np.asarray(q, dtype=_np.float64).reshape(-1) for q in qpos_list]
    B_list = [_np.asarray(B, dtype=_np.float64) for B in B_list]
    if len(qpos_list) != len(B_list):
        raise HandEyeError("qpos_list and B_list must have the same length")

    free = _np.asarray(offset_indices, dtype=int)

    def poses_for(params_tail: _np.ndarray) -> List[_np.ndarray]:
        offsets = _np.zeros(6, dtype=_np.float64)
        offsets[free] = _np.asarray(params_tail, dtype=_np.float64)
        return [fk(q + offsets) for q in qpos_list]

    def residuals(params: _np.ndarray) -> _np.ndarray:
        X, Y = _unpack(_np.asarray(params[:12], dtype=_np.float64))
        return _residual_vector(poses_for(params[12:]), B_list, X, Y)

    start = _np.concatenate([_pack(X0, Y0), _np.zeros(len(offset_indices))])
    try:
        from scipy.optimize import least_squares
    except ImportError:
        offsets = _np.zeros(len(offset_indices))
        return offsets, X0, Y0, poses_for(offsets)

    solution = least_squares(
        residuals,
        start,
        method="trf",
        max_nfev=int(max_iterations),
        xtol=1e-13,
        ftol=1e-13,
        gtol=1e-13,
    )
    base_cost = float(_np.sum(residuals(start) ** 2))
    if float(_np.sum(solution.fun ** 2)) > base_cost:
        offsets = _np.zeros(len(offset_indices))
        return offsets, X0, Y0, poses_for(offsets)
    offsets = _np.asarray(solution.x[12:], dtype=_np.float64)
    X, Y = _unpack(_np.asarray(solution.x[:12], dtype=_np.float64))
    return offsets, X, Y, poses_for(offsets)


def solve_eye_to_hand(
    A_list: Sequence[np.ndarray],
    B_list: Sequence[np.ndarray],
    meta: Optional[Dict[str, Any]] = None,
    per_sample_extra: Optional[Sequence[Dict[str, Any]]] = None,
) -> HandEyeResult:
    """Recover ``X = T_base_cam`` and ``Y = T_link_target`` from ``A_i Y = X B_i``."""
    if len(A_list) != len(B_list):
        raise HandEyeError("A_list and B_list must have the same length")
    if len(A_list) < 3:
        raise HandEyeError(
            f"Need at least 3 samples for hand-eye calibration, got {len(A_list)}"
        )
    A_list = [np.asarray(A, dtype=np.float64) for A in A_list]
    B_list = [np.asarray(B, dtype=np.float64) for B in B_list]

    A_rel, B_rel = relative_pairs(A_list, B_list)
    best = None
    for sign in (1.0, -1.0):
        R_Y, singular_values = _rotation_from_kronecker(A_rel, B_rel, sign=sign)
        t_Y = _translation_from_relative(A_rel, B_rel, R_Y)
        Y0 = make_transform(R_Y, t_Y)
        X0 = _X_from_samples(A_list, B_list, Y0)
        X, Y, cost = _refine(A_list, B_list, X0, Y0)
        if best is None or cost < best[0]:
            best = (cost, X, Y, singular_values)
    _, X, Y, singular_values = best

    # ---- diagnostics -------------------------------------------------------
    residuals = _residual_vector(A_list, B_list, X, Y).reshape(-1, 6)
    rotation_residuals = np.linalg.norm(residuals[:, :3], axis=1)
    translation_residuals = np.linalg.norm(residuals[:, 3:], axis=1)

    base_target = [
        np.asarray(A, dtype=np.float64) @ Y for A in A_list
    ]
    translations = np.stack([T[:3, 3] for T in base_target], axis=0)
    base_target_mean = translations.mean(axis=0)
    base_target_std = translations.std(axis=0)
    base_target_range = translations.max(axis=0) - translations.min(axis=0)
    R0 = base_target[0][:3, :3]
    drift = np.asarray(
        [rotation_angle(R0.T @ T[:3, :3]) for T in base_target], dtype=np.float64
    )

    from .selection import axis_conditioning

    axis_values, _ = axis_conditioning([np.asarray(A, dtype=np.float64) for A in A_list])

    per_sample: List[Dict[str, Any]] = []
    for index in range(len(A_list)):
        entry: Dict[str, Any] = {
            "index": index,
            "residual_rotation_rad": float(rotation_residuals[index]),
            "residual_translation_m": float(translation_residuals[index]),
            "T_base_target_translation_m": translations[index].tolist(),
            "rot_drift_rad": float(drift[index]),
        }
        if per_sample_extra is not None and index < len(per_sample_extra):
            entry.update(per_sample_extra[index])
        per_sample.append(entry)

    return HandEyeResult(
        X_base_cam=X,
        Y_link_target=Y,
        num_samples=len(A_list),
        num_relative_pairs=len(A_rel),
        rmse_translation_m=float(np.sqrt(np.mean(translation_residuals ** 2))),
        rmse_rotation_rad=float(np.sqrt(np.mean(rotation_residuals ** 2))),
        max_translation_residual_m=float(np.max(translation_residuals)),
        max_rotation_residual_rad=float(np.max(rotation_residuals)),
        base_target_std_m=base_target_std,
        base_target_range_m=base_target_range,
        base_target_mean_m=base_target_mean,
        rotation_drift_rad=drift,
        axis_singular_values=np.asarray(axis_values, dtype=np.float64),
        kronecker_tail_singular_values=np.asarray(singular_values[-4:], dtype=np.float64),
        per_sample=per_sample,
        meta=dict(meta or {}),
    )


def save_result(result: HandEyeResult, path: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return target


def summarize(result: HandEyeResult) -> str:
    """Human readable one-screen report."""
    X = result.X_base_cam
    t = X[:3, 3] * 1000.0
    lines = [
        f"samples={result.num_samples}  relative pairs={result.num_relative_pairs}",
        f"T_base_cam translation (mm): [{t[0]:.1f}, {t[1]:.1f}, {t[2]:.1f}]",
        f"  |t| = {np.linalg.norm(t):.1f} mm",
        "fit residual of A_i Y = X B_i (this is what quality means here):",
        f"  RMSE rotation = {np.rad2deg(result.rmse_rotation_rad):.3f} deg"
        f"   translation = {result.rmse_translation_m * 1000.0:.2f} mm",
        f"  max  rotation = {np.rad2deg(result.max_rotation_residual_rad):.3f} deg"
        f"   translation = {result.max_translation_residual_m * 1000.0:.2f} mm",
        f"rotation-axis singular values = {np.round(result.axis_singular_values, 3).tolist()}",
        f"target-in-base spread (expected to be large in eye-to-hand, "
        f"informational only): std {np.round(result.base_target_std_m * 1000.0, 1).tolist()} mm",
        f"stable (fit RMSE < 0.5 deg and < 5 mm): {result.stable}",
    ]
    return "\n".join(lines)
