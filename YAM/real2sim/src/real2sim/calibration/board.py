"""calib.io ChArUco board definition, detection and pose estimation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


# --- calib.io board: 15 mm squares, 11 mm markers, DICT_4X4 ----------------
#
# calib.io quotes the board as "8 x 11".  OpenCV wants (squares_x, squares_y)
# and the physical board must be described as 11 x 8 in that order --
# ``board_probe.py`` on this rig reports 21/24 detections for (11, 8) and 0/24
# for (8, 11).
DEFAULT_SQUARES_X = 11
DEFAULT_SQUARES_Y = 8
DEFAULT_SQUARE_LENGTH_M = 0.015
DEFAULT_MARKER_LENGTH_M = 0.011

# This board was printed with the pre-OpenCV-4.6 ChArUco layout: the probe gets
# 21/24 frames with legacyPattern=True versus 5/24 with it off.
DEFAULT_LEGACY_PATTERN = True

# calib.io prints DICT_4X4 boards; the exact variant is not printed on the
# board, so ``board_probe.py`` decides it empirically and we default to the
# most likely one.
DEFAULT_DICTIONARY = "DICT_4X4_1000"
DICTIONARY_CANDIDATES: Tuple[str, ...] = (
    "DICT_4X4_1000",
    "DICT_4X4_250",
    "DICT_4X4_100",
    "DICT_4X4_50",
)
# calib.io quotes the size as "8x11"; OpenCV wants (squares_x, squares_y).  Both
# orientations are cheap to try, so the probe tries both.
SQUARES_CANDIDATES: Tuple[Tuple[int, int], ...] = ((8, 11), (11, 8))


class BoardError(RuntimeError):
    """Raised when the board specification or the OpenCV build is unusable."""


@dataclass(frozen=True)
class BoardSpec:
    """Geometry of the printed ChArUco target."""

    squares_x: int = DEFAULT_SQUARES_X
    squares_y: int = DEFAULT_SQUARES_Y
    square_length_m: float = DEFAULT_SQUARE_LENGTH_M
    marker_length_m: float = DEFAULT_MARKER_LENGTH_M
    dictionary_name: str = DEFAULT_DICTIONARY
    legacy_pattern: bool = DEFAULT_LEGACY_PATTERN

    def __post_init__(self) -> None:
        if int(self.squares_x) < 2 or int(self.squares_y) < 2:
            raise BoardError(
                "squares_x and squares_y must both be >= 2, got "
                f"{self.squares_x} x {self.squares_y}"
            )
        if not (0.0 < float(self.marker_length_m) < float(self.square_length_m)):
            raise BoardError(
                "marker_length_m must be positive and strictly smaller than "
                f"square_length_m, got marker={self.marker_length_m} "
                f"square={self.square_length_m}"
            )

    @property
    def label(self) -> str:
        return (
            f"charuco {self.squares_x}x{self.squares_y} "
            f"square={self.square_length_m * 1000:.1f}mm "
            f"marker={self.marker_length_m * 1000:.1f}mm "
            f"{self.dictionary_name}"
            f"{' legacy' if self.legacy_pattern else ''}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "BoardSpec":
        return cls(**payload)


def _aruco():
    if not hasattr(cv2, "aruco"):
        raise BoardError(
            "This OpenCV build has no 'aruco' module (opencv-contrib-python "
            "is required)."
        )
    return cv2.aruco


def get_dictionary(dictionary_name: str):
    aruco = _aruco()
    if not hasattr(aruco, dictionary_name):
        raise BoardError(f"Unsupported ArUco dictionary: {dictionary_name}")
    return aruco.getPredefinedDictionary(getattr(aruco, dictionary_name))


def make_board(spec: BoardSpec):
    """Build the OpenCV ``CharucoBoard`` for ``spec``."""
    aruco = _aruco()
    board = aruco.CharucoBoard(
        (int(spec.squares_x), int(spec.squares_y)),
        float(spec.square_length_m),
        float(spec.marker_length_m),
        get_dictionary(spec.dictionary_name),
    )
    if spec.legacy_pattern:
        # OpenCV >= 4.6 changed the generated marker layout; calib.io boards
        # printed from the older generator need this flag.
        if not hasattr(board, "setLegacyPattern"):
            raise BoardError(
                "legacy_pattern=True was requested but this OpenCV build has "
                "no CharucoBoard.setLegacyPattern()."
            )
        board.setLegacyPattern(True)
    return board


def make_detector(board):
    aruco = _aruco()
    return aruco.CharucoDetector(board)


@dataclass
class BoardObservation:
    """One successful board detection with its 6-DoF pose in the camera frame."""

    T_cam_target: np.ndarray
    rvec: np.ndarray
    tvec: np.ndarray
    object_points: np.ndarray
    image_points: np.ndarray
    charuco_ids: np.ndarray
    num_markers: int
    reprojection_rmse_px: float

    @property
    def num_corners(self) -> int:
        return int(self.image_points.reshape(-1, 2).shape[0])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "T_cam_target": np.asarray(self.T_cam_target, dtype=float).tolist(),
            "rvec": np.asarray(self.rvec, dtype=float).reshape(3).tolist(),
            "tvec": np.asarray(self.tvec, dtype=float).reshape(3).tolist(),
            "charuco_ids": np.asarray(self.charuco_ids, dtype=int).reshape(-1).tolist(),
            "num_corners": self.num_corners,
            "num_markers": int(self.num_markers),
            "reprojection_rmse_px": float(self.reprojection_rmse_px),
        }


def rodrigues_to_matrix(rvec: np.ndarray) -> np.ndarray:
    R, _ = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64).reshape(3, 1))
    return np.asarray(R, dtype=np.float64)


def make_transform(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = np.asarray(R, dtype=np.float64).reshape(3, 3)
    T[:3, 3] = np.asarray(t, dtype=np.float64).reshape(3)
    return T


class BoardPoseEstimator:
    """Detect the ChArUco board and estimate ``T_cam_target`` with solvePnP."""

    def __init__(
        self,
        spec: BoardSpec,
        camera_matrix: np.ndarray,
        dist_coeffs: Optional[np.ndarray] = None,
        min_corners: int = 6,
        refine: bool = True,
    ) -> None:
        self.spec = spec
        self.board = make_board(spec)
        self.detector = make_detector(self.board)
        self.camera_matrix = np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3)
        if dist_coeffs is None:
            dist_coeffs = np.zeros(5, dtype=np.float64)
        self.dist_coeffs = np.asarray(dist_coeffs, dtype=np.float64).reshape(-1)
        self.min_corners = int(min_corners)
        self.refine = bool(refine)

    def estimate(self, image_bgr: np.ndarray) -> Optional[BoardObservation]:
        """Return the board pose, or ``None`` when the board is not usable."""
        aruco = _aruco()
        image = np.asarray(image_bgr)
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        charuco_corners, charuco_ids, _marker_corners, marker_ids = (
            self.detector.detectBoard(gray)
        )
        if charuco_corners is None or charuco_ids is None:
            return None
        if len(charuco_ids) < self.min_corners:
            return None

        object_points, image_points = self.board.matchImagePoints(
            charuco_corners, charuco_ids
        )
        if object_points is None or image_points is None:
            return None
        object_points = np.asarray(object_points, dtype=np.float32).reshape(-1, 1, 3)
        image_points = np.asarray(image_points, dtype=np.float32).reshape(-1, 1, 2)
        if object_points.shape[0] < 4:
            return None

        rvec, tvec = None, None
        # IPPE is the dedicated planar solver; fall back if the build lacks it.
        for flag in (getattr(aruco, "SOLVEPNP_IPPE", None), cv2.SOLVEPNP_ITERATIVE):
            if flag is None:
                continue
            try:
                ok, rvec_try, tvec_try = cv2.solvePnP(
                    object_points,
                    image_points,
                    self.camera_matrix,
                    self.dist_coeffs,
                    flags=flag,
                )
            except cv2.error:
                continue
            if ok:
                rvec, tvec = rvec_try, tvec_try
                break
        if rvec is None or tvec is None:
            return None

        if self.refine:
            try:
                rvec, tvec = cv2.solvePnPRefineLM(
                    object_points,
                    image_points,
                    self.camera_matrix,
                    self.dist_coeffs,
                    rvec,
                    tvec,
                )
            except cv2.error:
                pass

        projected, _ = cv2.projectPoints(
            object_points, rvec, tvec, self.camera_matrix, self.dist_coeffs
        )
        projected = np.asarray(projected, dtype=np.float64).reshape(-1, 2)
        measured = np.asarray(image_points, dtype=np.float64).reshape(-1, 2)
        rmse = float(np.sqrt(np.mean(np.sum((projected - measured) ** 2, axis=1))))

        num_markers = 0 if marker_ids is None else int(len(marker_ids))
        return BoardObservation(
            T_cam_target=make_transform(
                rodrigues_to_matrix(rvec), np.asarray(tvec, dtype=np.float64).reshape(3)
            ),
            rvec=np.asarray(rvec, dtype=np.float64).reshape(3),
            tvec=np.asarray(tvec, dtype=np.float64).reshape(3),
            object_points=object_points,
            image_points=image_points,
            charuco_ids=np.asarray(charuco_ids, dtype=np.int32).reshape(-1),
            num_markers=num_markers,
            reprojection_rmse_px=rmse,
        )


def draw_overlay(
    image_bgr: np.ndarray,
    observation: BoardObservation,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    axis_length_m: float = 0.04,
    label: str = "",
) -> np.ndarray:
    """Draw detected corners plus the reprojected board frame for review."""
    preview = np.asarray(image_bgr).copy()
    points = np.asarray(observation.image_points, dtype=np.float64).reshape(-1, 2)
    for x, y in points:
        cv2.circle(
            preview,
            (int(round(x)), int(round(y))),
            3,
            (0, 255, 0),
            thickness=-1,
            lineType=cv2.LINE_AA,
        )
    try:
        cv2.drawFrameAxes(
            preview,
            np.asarray(camera_matrix, dtype=np.float64),
            np.asarray(dist_coeffs, dtype=np.float64),
            observation.rvec.reshape(3, 1),
            observation.tvec.reshape(3, 1),
            float(axis_length_m),
            2,
        )
    except cv2.error:
        pass
    text = label or (
        f"err={observation.reprojection_rmse_px:.2f}px "
        f"corners={observation.num_corners}"
    )
    cv2.putText(
        preview,
        text,
        (10, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 0, 0),
        3,
        cv2.LINE_AA,
    )
    cv2.putText(
        preview,
        text,
        (10, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return preview


def contact_sheet(
    tiles: List[np.ndarray], columns: int = 5, pad: int = 6
) -> np.ndarray:
    """Tile a list of equally sized BGR images into one review image."""
    if not tiles:
        raise BoardError("contact_sheet() needs at least one tile")
    columns = max(1, min(int(columns), len(tiles)))
    rows = int(np.ceil(len(tiles) / float(columns)))
    height, width = tiles[0].shape[:2]
    canvas = np.full(
        (rows * height + (rows + 1) * pad, columns * width + (columns + 1) * pad, 3),
        32,
        dtype=np.uint8,
    )
    for index, tile in enumerate(tiles):
        row, column = divmod(index, columns)
        top = pad + row * (height + pad)
        left = pad + column * (width + pad)
        canvas[top : top + height, left : left + width] = tile[:height, :width]
    return canvas
