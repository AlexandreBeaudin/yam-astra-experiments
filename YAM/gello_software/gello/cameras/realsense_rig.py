"""Strict configuration and read-only inventory for the fixed RealSense rig."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import product
import math
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Mapping, Protocol, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from gello.cameras.realsense_camera import RealSenseFrame


_VALID_ROTATIONS = {0, 90, 180, 270}


@dataclass(frozen=True)
class CameraSpec:
    name: str
    serial: str
    model: str
    width: int
    height: int
    fps: int
    rotation: int
    auto_exposure: bool | None = None
    exposure_us: float | None = None


@dataclass(frozen=True)
class SyncPolicy:
    max_skew_s: float
    max_frame_age_s: float
    max_consecutive_failures: int


@dataclass(frozen=True)
class CameraRigConfig:
    cameras: dict[str, CameraSpec]
    depth_default_enabled: bool
    sync: SyncPolicy


@dataclass(frozen=True)
class CameraDeviceInfo:
    serial: str
    name: str
    model: str
    firmware_version: str
    usb_type_descriptor: str


@dataclass(frozen=True)
class CameraBundle:
    """One complete set of new frames accepted for a recording timestep."""

    reference_time: float
    frames: dict[str, "RealSenseFrame"]
    skew_s: float


@dataclass(frozen=True)
class CameraMiss:
    """A structured reason why no complete synchronized bundle was available."""

    reason: str
    detail: str
    consecutive: int


@dataclass(frozen=True)
class CameraHealth:
    """Read-only acquisition status for one logical camera."""

    state: str
    frames_received: int
    measured_fps: float
    last_host_timestamp: float | None
    error_type: str | None
    error_message: str | None


class FrameSource(Protocol):
    def start(self) -> None: ...

    def read(self, timeout_ms: int = 250) -> "RealSenseFrame": ...

    def close(self) -> None: ...


@dataclass
class _MutableHealth:
    state: str = "stopped"
    frames_received: int = 0
    first_host_timestamp: float | None = None
    last_host_timestamp: float | None = None
    error_type: str | None = None
    error_message: str | None = None


class CameraRig:
    """Acquire configured cameras concurrently and select synchronized frames."""

    def __init__(
        self,
        config: CameraRigConfig,
        *,
        depth_enabled: bool | None = None,
        sources: Mapping[str, FrameSource] | None = None,
        source_factory: Callable[[CameraSpec, bool], FrameSource] | None = None,
        buffer_size: int = 4,
    ) -> None:
        if buffer_size < 1:
            raise ValueError("buffer_size must be positive")
        self.config = config
        self.depth_enabled = (
            config.depth_default_enabled if depth_enabled is None else bool(depth_enabled)
        )
        camera_names = tuple(config.cameras)
        if sources is not None:
            if tuple(sources) != camera_names:
                raise ValueError(
                    "camera sources must match configured camera names and order"
                )
            self._sources = dict(sources)
        else:
            if source_factory is None:
                from gello.cameras.realsense_camera import (
                    RealSenseFrameSource,
                )

                source_factory = lambda spec, depth: RealSenseFrameSource(
                    spec, depth_enabled=depth
                )
            self._sources = {
                name: source_factory(spec, self.depth_enabled)
                for name, spec in config.cameras.items()
            }
        self._condition = threading.Condition()
        self._buffers: dict[str, deque["RealSenseFrame"]] = {
            name: deque(maxlen=buffer_size) for name in camera_names
        }
        self._health = {name: _MutableHealth() for name in camera_names}
        self._last_accepted = {name: -1 for name in camera_names}
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._consecutive_misses = 0
        self._started = False
        self._closed = False

    def start(self, warmup_frames: int = 5, timeout_s: float = 5.0) -> None:
        """Start all sources concurrently and wait for validated warmup frames."""
        if self._started:
            return
        if self._closed:
            raise RuntimeError("cannot start a closed CameraRig")
        if warmup_frames < 1 or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("warmup_frames and timeout_s must be positive")

        names = tuple(self.config.cameras)
        start_errors: dict[str, BaseException] = {}
        started: set[str] = set()
        start_lock = threading.Lock()

        def open_source(name: str) -> None:
            try:
                self._sources[name].start()
            except BaseException as exc:
                with start_lock:
                    start_errors[name] = exc
            else:
                with start_lock:
                    started.add(name)

        starters = [
            threading.Thread(
                target=open_source,
                args=(name,),
                name=f"realsense-start-{name}",
                daemon=True,
            )
            for name in names
        ]
        for thread in starters:
            thread.start()
        deadline = time.monotonic() + timeout_s
        for thread in starters:
            thread.join(max(0.0, deadline - time.monotonic()))
        stuck = [thread.name for thread in starters if thread.is_alive()]
        if start_errors or stuck:
            self._close_sources(started)
            if start_errors:
                first_name = next(name for name in names if name in start_errors)
                raise start_errors[first_name]
            raise TimeoutError(f"camera startup timed out: {', '.join(stuck)}")

        with self._condition:
            for name in names:
                self._health[name].state = "warming"
        self._threads = [
            threading.Thread(
                target=self._acquire,
                args=(name,),
                name=f"realsense-acquire-{name}",
                daemon=True,
            )
            for name in names
        ]
        for thread in self._threads:
            thread.start()

        try:
            with self._condition:
                while True:
                    failed = next(
                        (
                            name
                            for name in names
                            if self._health[name].state
                            in {"disconnected", "acquisition_error"}
                        ),
                        None,
                    )
                    if failed is not None:
                        health = self._health[failed]
                        raise RuntimeError(
                            f"camera {failed} warmup failed: {health.error_message}"
                        )
                    if all(
                        self._health[name].frames_received >= warmup_frames
                        for name in names
                    ):
                        for name in names:
                            self._health[name].state = "streaming"
                        self._started = True
                        return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        counts = ", ".join(
                            f"{name}={self._health[name].frames_received}"
                            for name in names
                        )
                        raise TimeoutError(f"camera warmup timed out ({counts})")
                    self._condition.wait(min(remaining, 0.05))
        except BaseException:
            self.close()
            raise

    def recording_frames(self):
        """Copy buffer references for independent recording, without consuming frames."""
        with self._condition:
            return {name: tuple(frames) for name, frames in self._buffers.items()}

    def latest_preview_frames(self) -> dict[str, "RealSenseFrame"]:
        """Borrow latest immutable frames without consuming synchronization state."""
        with self._condition:
            return {name: frames[-1] for name, frames in self._buffers.items() if frames}

    def _acquire(self, name: str) -> None:
        source = self._sources[name]
        first_frame = True
        while not self._stop_event.is_set():
            try:
                frame = source.read(timeout_ms=1000 if first_frame else 250)
                self._validate_frame(name, frame)
            except TimeoutError:
                if self._stop_event.is_set():
                    return
                continue
            except BaseException as exc:
                if self._stop_event.is_set():
                    return
                state = (
                    "disconnected"
                    if isinstance(exc, (ConnectionError, OSError))
                    else "acquisition_error"
                )
                with self._condition:
                    health = self._health[name]
                    health.state = state
                    health.error_type = type(exc).__name__
                    health.error_message = str(exc)
                    self._condition.notify_all()
                return
            first_frame = False
            with self._condition:
                self._buffers[name].append(frame)
                health = self._health[name]
                health.frames_received += 1
                if health.first_host_timestamp is None:
                    health.first_host_timestamp = frame.host_timestamp
                health.last_host_timestamp = frame.host_timestamp
                self._condition.notify_all()

    def _validate_frame(self, name: str, frame: "RealSenseFrame") -> None:
        spec = self.config.cameras[name]
        if frame.camera_name != name or frame.serial != spec.serial:
            raise ValueError(
                f"camera {name} identity mismatch: {frame.camera_name}/{frame.serial}"
            )
        if frame.rgb.shape != (spec.height, spec.width, 3) or frame.rgb.dtype.name != "uint8":
            raise ValueError(
                f"camera {name} RGB must be uint8 "
                f"[{spec.height},{spec.width},3]"
            )
        if self.depth_enabled:
            if (
                frame.depth is None
                or frame.depth.shape != (spec.height, spec.width)
                or frame.depth.dtype.name != "uint16"
            ):
                raise ValueError(
                    f"camera {name} depth must be uint16 "
                    f"[{spec.height},{spec.width}]"
                )
        elif frame.depth is not None:
            raise ValueError(f"camera {name} returned depth while depth is disabled")
        if frame.frame_number < 0 or not math.isfinite(frame.host_timestamp):
            raise ValueError(f"camera {name} has invalid frame metadata")

    def sample(self, reference_time: float) -> CameraBundle | CameraMiss:
        """Return the best complete bundle immediately, or a structured miss."""
        if not math.isfinite(reference_time):
            raise ValueError("reference_time must be finite")
        with self._condition:
            names = tuple(self.config.cameras)
            failed = next(
                (
                    name
                    for name in names
                    if self._health[name].state == "disconnected"
                ),
                None,
            )
            if failed is not None:
                return self._miss(
                    "disconnected",
                    f"{failed}: {self._health[failed].error_message}",
                )
            failed = next(
                (
                    name
                    for name in names
                    if self._health[name].state == "acquisition_error"
                ),
                None,
            )
            if failed is not None:
                return self._miss(
                    "acquisition_error",
                    f"{failed}: {self._health[failed].error_message}",
                )

            eligible: list[list["RealSenseFrame"]] = []
            age_failure = False
            for name in names:
                new_frames = [
                    frame
                    for frame in self._buffers[name]
                    if frame.frame_number > self._last_accepted[name]
                ]
                if not new_frames:
                    return self._miss("no_new_frame", f"{name} has no new frame")
                fresh = [
                    frame
                    for frame in new_frames
                    if reference_time - frame.host_timestamp
                    <= self.config.sync.max_frame_age_s + 1e-12
                ]
                if not fresh:
                    age_failure = True
                    break
                eligible.append(fresh)
            if age_failure:
                return self._miss("age", "one or more camera frames are stale")

            combinations = list(product(*eligible))

            def bundle_skew(frames) -> float:
                return max(frame.host_timestamp for frame in frames) - min(
                    frame.host_timestamp for frame in frames
                )

            valid = [
                frames
                for frames in combinations
                if bundle_skew(frames) <= self.config.sync.max_skew_s + 1e-12
            ]
            if not valid:
                skew = min(bundle_skew(frames) for frames in combinations)
                return self._miss(
                    "skew",
                    f"cross-camera skew {skew * 1000:.3f} ms exceeds "
                    f"{self.config.sync.max_skew_s * 1000:.3f} ms",
                )
            chosen = min(
                valid,
                key=lambda frames: (
                    max(abs(reference_time - frame.host_timestamp) for frame in frames),
                    bundle_skew(frames),
                ),
            )
            skew = bundle_skew(chosen)

            frames = {name: frame for name, frame in zip(names, chosen)}
            for name, frame in frames.items():
                self._last_accepted[name] = frame.frame_number
                while (
                    self._buffers[name]
                    and self._buffers[name][0].frame_number <= frame.frame_number
                ):
                    self._buffers[name].popleft()
            self._consecutive_misses = 0
            return CameraBundle(
                reference_time=float(reference_time),
                frames=frames,
                skew_s=float(skew),
            )

    def begin_episode(self) -> None:
        """Drop pre-episode frames so the first saved row is captured afterward."""
        with self._condition:
            if not self._started or self._closed:
                raise RuntimeError("CameraRig must be streaming to begin an episode")
            for buffer in self._buffers.values():
                buffer.clear()
            self._consecutive_misses = 0

    def _miss(self, reason: str, detail: str) -> CameraMiss:
        self._consecutive_misses += 1
        return CameraMiss(reason, detail, self._consecutive_misses)

    def status(self) -> dict[str, CameraHealth]:
        with self._condition:
            result: dict[str, CameraHealth] = {}
            for name, health in self._health.items():
                elapsed = (
                    0.0
                    if health.first_host_timestamp is None
                    or health.last_host_timestamp is None
                    else health.last_host_timestamp - health.first_host_timestamp
                )
                measured_fps = (
                    (health.frames_received - 1) / elapsed
                    if health.frames_received > 1 and elapsed > 0
                    else 0.0
                )
                result[name] = CameraHealth(
                    state=health.state,
                    frames_received=health.frames_received,
                    measured_fps=float(measured_fps),
                    last_host_timestamp=health.last_host_timestamp,
                    error_type=health.error_type,
                    error_message=health.error_message,
                )
            return result

    def close(self) -> None:
        """Stop acquisition and close every source exactly once."""
        if self._closed:
            return
        self._closed = True
        self._started = False
        self._stop_event.set()
        self._close_sources(set(self._sources))
        for thread in self._threads:
            thread.join(timeout=1.0)
        with self._condition:
            for health in self._health.values():
                if health.state not in {"disconnected", "acquisition_error"}:
                    health.state = "closed"
            self._condition.notify_all()

    def _close_sources(self, names: set[str]) -> None:
        for name in self.config.cameras:
            if name not in names:
                continue
            try:
                self._sources[name].close()
            except Exception:
                pass


class _StrictLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_mapping(loader: _StrictLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def load_camera_rig_config(path: Path) -> CameraRigConfig:
    """Load and strictly validate a camera rig YAML configuration."""
    try:
        document = yaml.load(path.read_text(), Loader=_StrictLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid camera rig YAML: {path}") from exc

    root = _mapping(document, "root")
    _require_keys(root, {"cameras", "depth", "sync"}, "root")
    cameras_data = _mapping(root["cameras"], "cameras")
    if not cameras_data:
        raise ValueError("cameras must not be empty")

    cameras: dict[str, CameraSpec] = {}
    serials: set[str] = set()
    for name, camera_data in cameras_data.items():
        if not _is_nonempty_string(name):
            raise ValueError("camera names must be non-empty strings")
        camera = _mapping(camera_data, f"camera {name!r}")
        _require_keys(
            camera,
            {"serial", "model", "width", "height", "fps", "rotation"},
            f"camera {name!r}",
            optional={"auto_exposure", "exposure_us"},
        )
        serial = _string(camera["serial"], f"camera {name!r} serial")
        if serial in serials:
            raise ValueError(f"duplicate camera serial: {serial}")
        serials.add(serial)
        width = _positive_int(camera["width"], f"camera {name!r} width")
        height = _positive_int(camera["height"], f"camera {name!r} height")
        fps = _positive_int(camera["fps"], f"camera {name!r} fps")
        rotation = _int(camera["rotation"], f"camera {name!r} rotation")
        if rotation not in _VALID_ROTATIONS:
            raise ValueError(f"camera {name!r} rotation must be one of {_VALID_ROTATIONS}")
        auto_exposure = camera.get("auto_exposure")
        if auto_exposure is not None and type(auto_exposure) is not bool:
            raise ValueError(f"camera {name!r} auto_exposure must be a YAML boolean")
        exposure_us = camera.get("exposure_us")
        if exposure_us is not None:
            exposure_us = _positive_number(
                exposure_us, f"camera {name!r} exposure_us"
            )
        if auto_exposure is False and exposure_us is None:
            raise ValueError(
                f"camera {name!r} requires exposure_us when auto_exposure is false"
            )
        if exposure_us is not None and auto_exposure is not False:
            raise ValueError(
                f"camera {name!r} exposure_us requires auto_exposure: false"
            )
        cameras[name] = CameraSpec(
            name=name,
            serial=serial,
            model=_string(camera["model"], f"camera {name!r} model"),
            width=width,
            height=height,
            fps=fps,
            rotation=rotation,
            auto_exposure=auto_exposure,
            exposure_us=exposure_us,
        )

    depth = _mapping(root["depth"], "depth")
    _require_keys(depth, {"default_enabled"}, "depth")
    enabled = depth["default_enabled"]
    if type(enabled) is not bool:
        raise ValueError("depth default_enabled must be a YAML boolean")

    sync = _mapping(root["sync"], "sync")
    _require_keys(sync, {"max_skew_ms", "max_frame_age_ms", "max_consecutive_failures"}, "sync")
    max_skew_ms = _positive_number(sync["max_skew_ms"], "sync max_skew_ms")
    max_frame_age_ms = _positive_number(sync["max_frame_age_ms"], "sync max_frame_age_ms")
    failures = _positive_int(sync["max_consecutive_failures"], "sync max_consecutive_failures")

    return CameraRigConfig(
        cameras=cameras,
        depth_default_enabled=enabled,
        sync=SyncPolicy(
            max_skew_s=max_skew_ms / 1000,
            max_frame_age_s=max_frame_age_ms / 1000,
            max_consecutive_failures=failures,
        ),
    )


def enumerate_realsense_devices(rs_module: Any | None = None) -> tuple[CameraDeviceInfo, ...]:
    """Return SDK device metadata without altering hardware or starting streams."""
    if rs_module is None:
        import pyrealsense2 as rs_module

    devices = rs_module.context().query_devices()
    return tuple(
        CameraDeviceInfo(
            serial=device.get_info(rs_module.camera_info.serial_number),
            name=device.get_info(rs_module.camera_info.name),
            model=normalize_realsense_model(device.get_info(rs_module.camera_info.name)),
            firmware_version=device.get_info(rs_module.camera_info.firmware_version),
            usb_type_descriptor=device.get_info(rs_module.camera_info.usb_type_descriptor),
        )
        for device in devices
    )


def normalize_realsense_model(name: str) -> str:
    """Normalize an SDK display name to the model identifier used in config."""
    return re.sub(r"^(?:intel\s+)?realsense\s+", "", name, flags=re.IGNORECASE).strip()


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return value


def _require_keys(
    mapping: Mapping[str, Any],
    expected: set[str],
    context: str,
    *,
    optional: set[str] = frozenset(),
) -> None:
    actual = set(mapping)
    missing = expected - actual
    unknown = actual - expected - optional
    if missing or unknown:
        raise ValueError(f"{context} has missing keys {missing} or unknown keys {unknown}")


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string(value: Any, context: str) -> str:
    if not _is_nonempty_string(value):
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _int(value: Any, context: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{context} must be an integer")
    return value


def _positive_int(value: Any, context: str) -> int:
    value = _int(value, context)
    if value <= 0:
        raise ValueError(f"{context} must be positive")
    return value


def _positive_number(value: Any, context: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{context} must be a positive number")
    return float(value)
