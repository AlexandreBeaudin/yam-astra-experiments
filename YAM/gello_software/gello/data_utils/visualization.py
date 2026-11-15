"""Lazy HDF5 episode visualization and streaming MP4 composition."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import h5py
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
ARM_COLORS = (
    (86, 180, 233),
    (230, 159, 0),
    (0, 158, 115),
    (240, 228, 66),
    (0, 114, 178),
    (213, 94, 0),
)
GRIPPER_COLOR = (204, 121, 167)
BACKGROUND = (16, 19, 24)
PANEL_BACKGROUND = (25, 29, 36)
GRID_COLOR = (62, 68, 78)
TEXT_COLOR = (232, 235, 240)
MUTED_TEXT = (160, 168, 180)
CURSOR_COLOR = (255, 90, 90)


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", max(8, int(size)))
    except OSError:
        return ImageFont.load_default()


def _json_string_list(value, label: str) -> tuple[str, ...]:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be a JSON string list") from exc
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) and item for item in decoded
    ):
        raise ValueError(f"{label} must be a JSON string list")
    if len(set(decoded)) != len(decoded):
        raise ValueError(f"{label} contains duplicate names")
    return tuple(decoded)


@dataclass
class VisualizationEpisode:
    path: Path
    timestamps: np.ndarray
    qpos: np.ndarray
    joint_names: tuple[str, ...]
    camera_names: tuple[str, ...]
    control_hz: float
    task_name: str
    outcome: str
    episode_index: int
    _file: h5py.File
    _cameras: dict[str, h5py.Dataset]

    @property
    def duration(self) -> float:
        return float(self.timestamps[-1])

    def camera_frame(self, name: str, index: int) -> np.ndarray:
        if name not in self._cameras:
            raise KeyError(f"unknown camera: {name}")
        return np.asarray(self._cameras[name][index], dtype=np.uint8)

    def close(self) -> None:
        if self._file:
            self._file.close()

    def __enter__(self) -> "VisualizationEpisode":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.close()
        return False


def load_visualization_episode(
    path: Path,
    *,
    selected_cameras: Sequence[str] | None = None,
) -> VisualizationEpisode:
    source = Path(path)
    try:
        episode = h5py.File(source, "r")
    except OSError as exc:
        raise ValueError(f"cannot read HDF5 episode: {exc}") from exc
    try:
        if "timestamp" not in episode or "observations/qpos" not in episode:
            raise ValueError("episode requires timestamp and observations/qpos")
        timestamps = np.asarray(episode["timestamp"], dtype=float)
        qpos = np.asarray(episode["observations/qpos"], dtype=float)
        if timestamps.ndim != 1 or timestamps.size < 1:
            raise ValueError("timestamp must be a non-empty vector")
        if qpos.shape not in {(timestamps.size, 14), (timestamps.size, 28)}:
            raise ValueError(
                f"observations/qpos must have shape [{timestamps.size},14 or 28], "
                f"got {qpos.shape}"
            )
        if not np.all(np.isfinite(timestamps)) or not np.all(np.isfinite(qpos)):
            raise ValueError("timestamp and observations/qpos must be finite")
        if timestamps.size > 1 and not np.all(np.diff(timestamps) > 0):
            raise ValueError("timestamps must be strictly increasing")
        timestamps = timestamps - timestamps[0]

        joint_names = _json_string_list(
            episode.attrs.get("joint_names", ""), "joint_names"
        )
        if len(joint_names) != qpos.shape[1]:
            raise ValueError(
                f"joint_names must contain exactly {qpos.shape[1]} names"
            )
        try:
            control_hz = float(episode.attrs.get("control_hz", np.nan))
        except (TypeError, ValueError) as exc:
            raise ValueError("control_hz must be positive and finite") from exc
        if not np.isfinite(control_hz) or control_hz <= 0:
            raise ValueError("control_hz must be positive and finite")

        declared = _json_string_list(
            episode.attrs.get("camera_names", "[]"), "camera_names"
        )
        images = episode.get("observations/images")
        available = tuple(images.keys()) if isinstance(images, h5py.Group) else ()
        if set(declared) != set(available):
            raise ValueError(
                "camera_names does not match observations/images datasets"
            )
        if selected_cameras is not None:
            chosen = tuple(selected_cameras)
            if len(chosen) != 3 or len(set(chosen)) != 3:
                raise ValueError("--cameras must select exactly three unique cameras")
            missing = [name for name in chosen if name not in available]
            if missing:
                raise ValueError(f"unknown camera selection: {', '.join(missing)}")
        elif len(declared) in {0, 3}:
            chosen = declared
        else:
            raise ValueError(
                f"episode has {len(declared)} cameras; expected zero or exactly three"
            )

        cameras: dict[str, h5py.Dataset] = {}
        for name in chosen:
            dataset = images[name]
            if (
                dataset.shape[:1] != (timestamps.size,)
                or dataset.ndim != 4
                or dataset.shape[-1] != 3
                or dataset.dtype != np.uint8
            ):
                raise ValueError(
                    f"camera {name} must be uint8 [T,H,W,3] with T={timestamps.size}, "
                    f"got {dataset.dtype} {dataset.shape}"
                )
            cameras[name] = dataset

        stem = source.stem
        try:
            episode_index = int(episode.attrs.get("episode_index", stem))
        except (TypeError, ValueError) as exc:
            raise ValueError("episode_index must be an integer") from exc
        return VisualizationEpisode(
            path=source,
            timestamps=timestamps,
            qpos=qpos,
            joint_names=joint_names,
            camera_names=chosen,
            control_hz=control_hz,
            task_name=str(episode.attrs.get("task_name", source.parent.parent.name)),
            outcome=str(episode.attrs.get("outcome", source.parent.name)),
            episode_index=episode_index,
            _file=episode,
            _cameras=cameras,
        )
    except BaseException:
        episode.close()
        raise


def output_timeline(timestamps: np.ndarray, fps: float) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(timestamps, dtype=float)
    rate = float(fps)
    if values.ndim != 1 or values.size < 1 or not np.all(np.isfinite(values)):
        raise ValueError("timestamps must be a non-empty finite vector")
    if values.size > 1 and not np.all(np.diff(values) > 0):
        raise ValueError("timestamps must be strictly increasing")
    if not np.isfinite(rate) or rate <= 0:
        raise ValueError("fps must be positive and finite")
    normalized = values - values[0]
    duration = float(normalized[-1])
    frame_count = max(1, int(math.ceil(duration * rate)) + 1)
    output_times = np.minimum(np.arange(frame_count, dtype=float) / rate, duration)
    right = np.searchsorted(normalized, output_times, side="left")
    right = np.clip(right, 0, normalized.size - 1)
    left = np.maximum(right - 1, 0)
    use_left = np.abs(output_times - normalized[left]) <= np.abs(
        normalized[right] - output_times
    )
    indices = np.where(use_left, left, right).astype(int)
    return output_times, indices


def default_output_path(source: Path) -> Path:
    path = Path(source)
    if path.parent.name not in {"success", "fail"}:
        raise ValueError(
            "default output requires data/<task>/<success|fail>/<episode>.hdf5"
        )
    return path.parent.parent / "visualizations" / path.parent.name / f"{path.stem}.mp4"


@dataclass(frozen=True)
class _ChartTemplate:
    image: Image.Image
    plot_left: int
    plot_right: int
    arm_top: int
    arm_bottom: int
    grip_top: int
    grip_bottom: int


class EpisodeRenderer:
    """Render synchronized camera frames and precomputed joint timelines."""

    def __init__(
        self,
        episode: VisualizationEpisode,
        *,
        width: int = FRAME_WIDTH,
        height: int = FRAME_HEIGHT,
    ) -> None:
        if width < 320 or height < 240 or width % 2 or height % 2:
            raise ValueError("video dimensions must be even and at least 320x240")
        self.episode = episode
        self.width = int(width)
        self.height = int(height)
        self.scale = min(self.width / FRAME_WIDTH, self.height / FRAME_HEIGHT)
        self.small_font = _font(max(8, round(16 * self.scale)))
        self.body_font = _font(max(9, round(19 * self.scale)))
        self.title_font = _font(max(11, round(26 * self.scale)))
        self.camera_height = self.height // 2 if episode.camera_names else 0
        self.chart_top = self.camera_height if episode.camera_names else max(
            24, round(38 * self.scale)
        )
        chart_area_height = self.height - self.chart_top
        self.chart_width = self.width // 2
        self.arm_count = episode.qpos.shape[1] // 7
        self.chart_rows = self.arm_count // 2
        self.chart_height = chart_area_height // self.chart_rows
        arm_titles = ("LEFT", "RIGHT", "THIRD", "FOURTH")
        self.templates = tuple(
            self._build_chart(
                arm * 7,
                self.chart_width if arm % 2 == 0 else self.width - self.chart_width,
                (
                    chart_area_height - self.chart_height * (self.chart_rows - 1)
                    if arm // 2 == self.chart_rows - 1
                    else self.chart_height
                ),
                f"{arm_titles[arm]} FOLLOWER",
            )
            for arm in range(self.arm_count)
        )

    def _build_chart(
        self, start_index: int, width: int, height: int, title: str
    ) -> _ChartTemplate:
        image = Image.new("RGB", (width, height), PANEL_BACKGROUND)
        draw = ImageDraw.Draw(image)
        margin_left = max(30, round(66 * self.scale))
        margin_right = max(8, round(18 * self.scale))
        title_height = max(25, round(52 * self.scale))
        bottom = max(20, round(42 * self.scale))
        gap = max(12, round(24 * self.scale))
        available = max(80, height - title_height - bottom - gap)
        arm_height = max(48, int(available * 0.72))
        grip_height = max(28, available - arm_height)
        arm_top = title_height
        arm_bottom = min(height - bottom - gap - grip_height, arm_top + arm_height)
        grip_top = arm_bottom + gap
        grip_bottom = min(height - bottom, grip_top + grip_height)
        plot_left = margin_left
        plot_right = max(plot_left + 20, width - margin_right)

        draw.text((margin_left, max(2, round(7 * self.scale))), title, fill=TEXT_COLOR, font=self.title_font)
        for grid in range(6):
            x = int(plot_left + (plot_right - plot_left) * grid / 5)
            draw.line((x, arm_top, x, arm_bottom), fill=GRID_COLOR, width=1)
            draw.line((x, grip_top, x, grip_bottom), fill=GRID_COLOR, width=1)
        for grid in range(5):
            y = int(arm_top + (arm_bottom - arm_top) * grid / 4)
            draw.line((plot_left, y, plot_right, y), fill=GRID_COLOR, width=1)
        for grid in range(3):
            y = int(grip_top + (grip_bottom - grip_top) * grid / 2)
            draw.line((plot_left, y, plot_right, y), fill=GRID_COLOR, width=1)

        arms = self.episode.qpos[:, start_index : start_index + 6]
        low = float(np.min(arms))
        high = float(np.max(arms))
        padding = max(0.05, (high - low) * 0.08)
        low -= padding
        high += padding
        duration = max(self.episode.duration, 1e-9)

        def x_of(timestamp: float) -> float:
            return plot_left + (plot_right - plot_left) * timestamp / duration

        def arm_y(value: float) -> float:
            return arm_bottom - (arm_bottom - arm_top) * (value - low) / (high - low)

        for joint in range(6):
            points = [
                (x_of(float(timestamp)), arm_y(float(value)))
                for timestamp, value in zip(
                    self.episode.timestamps, arms[:, joint]
                )
            ]
            if len(points) == 1:
                draw.ellipse((*points[0], *points[0]), fill=ARM_COLORS[joint])
            else:
                draw.line(points, fill=ARM_COLORS[joint], width=max(1, round(2 * self.scale)))
            label_x = margin_left + joint * max(32, (plot_right - margin_left) // 7)
            draw.text(
                (label_x, max(2, round(30 * self.scale))),
                f"J{joint + 1}",
                fill=ARM_COLORS[joint],
                font=self.small_font,
            )

        gripper = self.episode.qpos[:, start_index + 6]
        grip_points = [
            (
                x_of(float(timestamp)),
                grip_bottom - (grip_bottom - grip_top) * float(np.clip(value, 0.0, 1.0)),
            )
            for timestamp, value in zip(self.episode.timestamps, gripper)
        ]
        if len(grip_points) > 1:
            draw.line(
                grip_points,
                fill=GRIPPER_COLOR,
                width=max(1, round(2 * self.scale)),
            )
        draw.text((4, arm_top), "rad", fill=MUTED_TEXT, font=self.small_font)
        draw.text((4, grip_top), "grip", fill=GRIPPER_COLOR, font=self.small_font)
        draw.text(
            (plot_left, max(0, height - bottom + 2)),
            "0.00s",
            fill=MUTED_TEXT,
            font=self.small_font,
        )
        end_label = f"{self.episode.duration:.2f}s"
        end_box = draw.textbbox((0, 0), end_label, font=self.small_font)
        draw.text(
            (plot_right - (end_box[2] - end_box[0]), max(0, height - bottom + 2)),
            end_label,
            fill=MUTED_TEXT,
            font=self.small_font,
        )
        return _ChartTemplate(
            image=image,
            plot_left=plot_left,
            plot_right=plot_right,
            arm_top=arm_top,
            arm_bottom=arm_bottom,
            grip_top=grip_top,
            grip_bottom=grip_bottom,
        )

    def _render_chart(
        self, template: _ChartTemplate, side: int, sample_index: int, timestamp: float
    ) -> Image.Image:
        image = template.image.copy()
        draw = ImageDraw.Draw(image)
        fraction = 0.0 if self.episode.duration <= 0 else timestamp / self.episode.duration
        cursor_x = int(
            template.plot_left
            + (template.plot_right - template.plot_left) * np.clip(fraction, 0.0, 1.0)
        )
        draw.line(
            (cursor_x, template.arm_top, cursor_x, template.grip_bottom),
            fill=CURSOR_COLOR,
            width=max(1, round(2 * self.scale)),
        )
        start = side * 7
        values = self.episode.qpos[sample_index, start : start + 7]
        text = "  ".join(
            [f"J{i + 1} {values[i]:+.3f}" for i in range(6)]
            + [f"G {values[6]:.3f}"]
        )
        draw.text(
            (template.plot_left, max(template.arm_top, template.grip_bottom + 2)),
            text,
            fill=TEXT_COLOR,
            font=self.small_font,
        )
        return image

    def _paste_camera(
        self, canvas: Image.Image, name: str, sample_index: int, slot: int
    ) -> None:
        slot_width = self.width // 3
        x0 = slot * slot_width
        x1 = self.width if slot == 2 else x0 + slot_width
        title_height = max(22, round(36 * self.scale))
        available_width = x1 - x0
        available_height = self.camera_height - title_height
        frame = Image.fromarray(self.episode.camera_frame(name, sample_index), "RGB")
        ratio = min(available_width / frame.width, available_height / frame.height)
        size = (max(1, round(frame.width * ratio)), max(1, round(frame.height * ratio)))
        frame = frame.resize(size, Image.Resampling.BILINEAR)
        left = x0 + (available_width - frame.width) // 2
        top = title_height + (available_height - frame.height) // 2
        canvas.paste(frame, (left, top))
        draw = ImageDraw.Draw(canvas)
        label_box = draw.textbbox((0, 0), name, font=self.title_font)
        draw.text(
            (x0 + (available_width - (label_box[2] - label_box[0])) // 2, 4),
            name,
            fill=TEXT_COLOR,
            font=self.title_font,
        )
        if slot < 2:
            draw.line((x1 - 1, 0, x1 - 1, self.camera_height), fill=GRID_COLOR)

    def render(self, sample_index: int, timestamp: float) -> np.ndarray:
        if not 0 <= sample_index < self.episode.qpos.shape[0]:
            raise IndexError("sample index is outside the episode")
        canvas = Image.new("RGB", (self.width, self.height), BACKGROUND)
        if self.episode.camera_names:
            for slot, name in enumerate(self.episode.camera_names):
                self._paste_camera(canvas, name, sample_index, slot)
        else:
            banner = "JOINT DATA ONLY  ·  NO CAMERA FRAMES"
            draw = ImageDraw.Draw(canvas)
            box = draw.textbbox((0, 0), banner, font=self.title_font)
            draw.text(
                ((self.width - (box[2] - box[0])) // 2, 4),
                banner,
                fill=(245, 185, 66),
                font=self.title_font,
            )

        for arm, template in enumerate(self.templates):
            chart = self._render_chart(template, arm, sample_index, timestamp)
            x = 0 if arm % 2 == 0 else self.chart_width
            y = self.chart_top + (arm // 2) * self.chart_height
            canvas.paste(chart, (x, y))
        draw = ImageDraw.Draw(canvas)
        draw.line(
            (self.chart_width - 1, self.chart_top, self.chart_width - 1, self.height),
            fill=GRID_COLOR,
        )
        if self.chart_rows > 1:
            split_y = self.chart_top + self.chart_height
            draw.line((0, split_y - 1, self.width, split_y - 1), fill=GRID_COLOR)
        status = (
            f"{self.episode.task_name}  ·  {self.episode.outcome.upper()} "
            f"{self.episode.episode_index:04d}  ·  {timestamp:06.2f}s"
        )
        draw.text((10, self.height - max(18, round(24 * self.scale))), status, fill=MUTED_TEXT, font=self.small_font)
        return np.asarray(canvas, dtype=np.uint8)


class FFmpegVideoWriter:
    """Stream RGB frames to FFmpeg and atomically publish the final MP4."""

    def __init__(
        self,
        output: Path,
        *,
        width: int,
        height: int,
        fps: float,
        force: bool = False,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self.output = Path(output)
        if self.output.suffix.lower() != ".mp4":
            raise ValueError("video output must use the .mp4 extension")
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.force = bool(force)
        executable = shutil.which(ffmpeg)
        if executable is None:
            raise RuntimeError("ffmpeg executable is required to create MP4 files")
        self.ffmpeg = executable
        if self.width <= 0 or self.height <= 0 or self.width % 2 or self.height % 2:
            raise ValueError("video dimensions must be positive even numbers")
        if not np.isfinite(self.fps) or self.fps <= 0:
            raise ValueError("fps must be positive and finite")
        if self.output.exists() and not self.force:
            raise FileExistsError(f"output already exists: {self.output}")
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.temp_path = self.output.with_name(
            f".{self.output.stem}-{uuid.uuid4().hex}.tmp.mp4"
        )
        self.process: subprocess.Popen | None = None
        self._stderr = None

    def __enter__(self) -> "FFmpegVideoWriter":
        self._stderr = tempfile.TemporaryFile()
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{self.width}x{self.height}",
            "-r",
            f"{self.fps:.8g}",
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(self.temp_path),
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr,
        )
        return self

    def _error_text(self) -> str:
        if self._stderr is None:
            return ""
        self._stderr.flush()
        self._stderr.seek(0)
        return self._stderr.read().decode("utf-8", errors="replace").strip()

    def write(self, frame: np.ndarray) -> None:
        value = np.asarray(frame)
        if value.shape != (self.height, self.width, 3) or value.dtype != np.uint8:
            raise ValueError(
                f"frame must be uint8 [{self.height},{self.width},3], "
                f"got {value.dtype} {value.shape}"
            )
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("FFmpeg writer is not open")
        try:
            self.process.stdin.write(value.tobytes(order="C"))
        except BrokenPipeError as exc:
            self.process.wait()
            raise RuntimeError(
                f"ffmpeg stopped while encoding: {self._error_text()}"
            ) from exc

    def close(self) -> None:
        if self.process is None:
            return
        process = self.process
        self.process = None
        if process.stdin is not None:
            process.stdin.close()
        returncode = process.wait()
        error = self._error_text()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None
        if returncode != 0:
            self.temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg failed with exit {returncode}: {error}")
        if self.output.exists() and not self.force:
            self.temp_path.unlink(missing_ok=True)
            raise FileExistsError(f"output already exists: {self.output}")
        try:
            os.replace(self.temp_path, self.output)
        except BaseException:
            self.temp_path.unlink(missing_ok=True)
            raise

    def abort(self) -> None:
        if self.process is not None:
            process = self.process
            self.process = None
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None
        self.temp_path.unlink(missing_ok=True)

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        if exc_type is None:
            self.close()
        else:
            self.abort()
        return False


def render_episode_video(
    episode: VisualizationEpisode,
    output: Path,
    *,
    fps: float | None = None,
    width: int = FRAME_WIDTH,
    height: int = FRAME_HEIGHT,
    force: bool = False,
    progress: Callable[[int, int, float], None] | None = None,
) -> int:
    output = Path(output)
    if output.resolve() == episode.path.resolve() or (
        output.exists() and os.path.samefile(output, episode.path)
    ):
        raise ValueError("video output must not overwrite the source episode")
    rate = episode.control_hz if fps is None else float(fps)
    times, indices = output_timeline(episode.timestamps, rate)
    renderer = EpisodeRenderer(episode, width=width, height=height)
    notify = progress or (lambda _index, _total, _timestamp: None)
    with FFmpegVideoWriter(
        output,
        width=width,
        height=height,
        fps=rate,
        force=force,
    ) as writer:
        for output_index, (timestamp, sample_index) in enumerate(
            zip(times, indices), start=1
        ):
            writer.write(renderer.render(int(sample_index), float(timestamp)))
            notify(output_index, len(times), float(timestamp))
    return len(times)
