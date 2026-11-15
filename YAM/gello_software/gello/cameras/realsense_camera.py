import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from gello.cameras.camera import CameraDriver
from gello.cameras.realsense_rig import CameraSpec


def get_device_ids() -> List[str]:
    import pyrealsense2 as rs

    ctx = rs.context()
    devices = ctx.query_devices()
    return [dev.get_info(rs.camera_info.serial_number) for dev in devices]


@dataclass(frozen=True)
class RealSenseFrame:
    """One immutable camera sample with SDK and host timing metadata."""

    camera_name: str
    serial: str
    rgb: np.ndarray
    depth: np.ndarray | None
    frame_number: int
    device_timestamp_ms: float
    timestamp_domain: str
    host_timestamp: float


class RealSenseFrameTimeout(TimeoutError):
    """The SDK produced no frame before the requested wait deadline."""


class RealSenseFrameSource:
    """Exact-serial RealSense source with explicit streaming lifecycle."""

    def __init__(
        self,
        spec: CameraSpec,
        *,
        depth_enabled: bool,
        rs_module=None,
        clock=time.monotonic,
    ):
        self._spec = spec
        self._depth_enabled = depth_enabled
        self._rs = rs_module
        self._clock = clock
        self._pipeline = None
        self._align = None
        self._color_format = None
        self._color_controls_profile = None
        self._started = False
        self._closed = False

    def start(self) -> None:
        """Configure and start the exact configured device."""
        if self._started:
            return
        if self._closed:
            raise RuntimeError("cannot start a closed RealSenseFrameSource")
        if self._rs is None:
            import pyrealsense2 as rs

            self._rs = rs

        rs = self._rs
        pipeline = rs.pipeline()
        config = rs.config()
        color_format = getattr(rs.format, "rgb8", rs.format.bgr8)
        config.enable_device(self._spec.serial)
        config.enable_stream(
            rs.stream.color,
            self._spec.width,
            self._spec.height,
            color_format,
            self._spec.fps,
        )
        if self._depth_enabled:
            config.enable_stream(
                rs.stream.depth,
                self._spec.width,
                self._spec.height,
                rs.format.z16,
                self._spec.fps,
            )

        self._pipeline = pipeline
        self._color_format = color_format
        pipeline_started = False
        try:
            active_profile = pipeline.start(config)
            pipeline_started = True
            if self._spec.auto_exposure is not None:
                sensor = self._color_sensor(active_profile)
                sensor.set_option(rs.option.enable_auto_exposure, 1.0)
                if not self._spec.auto_exposure:
                    self._color_controls_profile = active_profile
            self._align = rs.align(rs.stream.color) if self._depth_enabled else None
        except Exception:
            self._pipeline = None
            self._color_format = None
            self._align = None
            self._color_controls_profile = None
            self._started = False
            if pipeline_started:
                try:
                    pipeline.stop()
                except Exception:
                    pass
            raise

        self._started = True

    def _color_sensor(self, active_profile):
        """Return the color sensor that exposes the auto-exposure control."""
        sensor = next(
            (
                item
                for item in active_profile.get_device().query_sensors()
                if item.supports(self._rs.option.enable_auto_exposure)
                and any(
                    profile.stream_type() == self._rs.stream.color
                    for profile in item.get_stream_profiles()
                )
            ),
            None,
        )
        if sensor is None:
            raise RuntimeError(
                f"camera {self._spec.name} does not support auto exposure control"
            )
        return sensor

    def _configure_color_controls(self, active_profile) -> None:
        """Apply explicitly configured color controls after a first frame arrives."""
        sensor = self._color_sensor(active_profile)
        sensor.set_option(
            self._rs.option.enable_auto_exposure,
            float(self._spec.auto_exposure),
        )
        if self._spec.exposure_us is not None:
            if not sensor.supports(self._rs.option.exposure):
                raise RuntimeError(
                    f"camera {self._spec.name} does not support manual exposure"
                )
            sensor.set_option(self._rs.option.exposure, self._spec.exposure_us)

    def read(self, timeout_ms: int = 250) -> RealSenseFrame:
        """Return a copied RGB sample and optional depth aligned to color."""
        if not self._started or self._pipeline is None:
            raise RuntimeError("RealSenseFrameSource is not started")

        try:
            frames = self._pipeline.wait_for_frames(timeout_ms)
        except RuntimeError as exc:
            if "frame didn't arrive within" in str(exc).lower():
                raise RealSenseFrameTimeout(str(exc)) from exc
            raise
        if self._color_controls_profile is not None:
            self._configure_color_controls(self._color_controls_profile)
            self._color_controls_profile = None
        host_timestamp = float(self._clock())
        if self._align is not None:
            frames = self._align.process(frames)

        color_frame = frames.get_color_frame()
        rgb = np.array(color_frame.get_data(), dtype=np.uint8, copy=True)
        if self._color_format == self._rs.format.bgr8:
            rgb = rgb[:, :, ::-1].copy()
        rgb = self._rotate(rgb)

        depth = None
        if self._depth_enabled:
            depth_frame = frames.get_depth_frame()
            depth = np.array(depth_frame.get_data(), dtype=np.uint16, copy=True)
            depth = self._rotate(depth)

        return RealSenseFrame(
            camera_name=self._spec.name,
            serial=self._spec.serial,
            rgb=rgb,
            depth=depth,
            frame_number=int(color_frame.get_frame_number()),
            device_timestamp_ms=float(color_frame.get_timestamp()),
            timestamp_domain=str(color_frame.get_frame_timestamp_domain()),
            host_timestamp=host_timestamp,
        )

    def close(self) -> None:
        """Stop streaming once; subsequent closes are safe no-ops."""
        if self._closed:
            return
        self._closed = True
        self._started = False
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None
        self._align = None
        self._color_controls_profile = None

    def _rotate(self, image: np.ndarray) -> np.ndarray:
        if self._spec.rotation == 0:
            return image
        return np.rot90(image, self._spec.rotation // 90).copy()


class RealSenseCamera(CameraDriver):
    def __repr__(self) -> str:
        return f"RealSenseCamera(device_id={self._device_id})"

    def __init__(self, device_id: Optional[str] = None, flip: bool = False):
        import pyrealsense2 as rs

        self._device_id = device_id

        if device_id is None:
            self._pipeline = rs.pipeline()
            config = rs.config()
        else:
            self._pipeline = rs.pipeline()
            config = rs.config()
            config.enable_device(device_id)

        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        self._pipeline.start(config)
        self._flip = flip

    def read(
        self,
        img_size: Optional[Tuple[int, int]] = None,  # farthest: float = 0.12
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Read a frame from the camera.

        Args:
            img_size: The size of the image to return. If None, the original size is returned.
            farthest: The farthest distance to map to 255.

        Returns:
            np.ndarray: The color image, shape=(H, W, 3)
            np.ndarray: The depth image, shape=(H, W, 1)
        """
        import cv2

        frames = self._pipeline.wait_for_frames()
        color_frame = frames.get_color_frame()
        color_image = np.asanyarray(color_frame.get_data())
        depth_frame = frames.get_depth_frame()
        depth_image = np.asanyarray(depth_frame.get_data())
        # depth_image = cv2.convertScaleAbs(depth_image, alpha=0.03)
        if img_size is None:
            image = color_image[:, :, ::-1]
            depth = depth_image
        else:
            image = cv2.resize(color_image, img_size)[:, :, ::-1]
            depth = cv2.resize(depth_image, img_size)

        # rotate 180 degree's because everything is upside down in order to center the camera
        if self._flip:
            image = cv2.rotate(image, cv2.ROTATE_180)
            depth = cv2.rotate(depth, cv2.ROTATE_180)[:, :, None]
        else:
            depth = depth[:, :, None]

        return image, depth


def _debug_read(camera, save_datastream=False):
    import cv2

    cv2.namedWindow("image")
    cv2.namedWindow("depth")
    counter = 0
    if not os.path.exists("images"):
        os.makedirs("images")
    if save_datastream and not os.path.exists("stream"):
        os.makedirs("stream")
    while True:
        time.sleep(0.1)
        image, depth = camera.read()
        depth = np.concatenate([depth, depth, depth], axis=-1)
        key = cv2.waitKey(1)
        cv2.imshow("image", image[:, :, ::-1])
        cv2.imshow("depth", depth)
        if key == ord("s"):
            cv2.imwrite(f"images/image_{counter}.png", image[:, :, ::-1])
            cv2.imwrite(f"images/depth_{counter}.png", depth)
        if save_datastream:
            cv2.imwrite(f"stream/image_{counter}.png", image[:, :, ::-1])
            cv2.imwrite(f"stream/depth_{counter}.png", depth)
        counter += 1
        if key == 27:
            break


if __name__ == "__main__":
    device_ids = get_device_ids()
    print(f"Found {len(device_ids)} devices")
    print(device_ids)
    rs = RealSenseCamera(flip=True, device_id=device_ids[0])
    im, depth = rs.read()
    _debug_read(rs, save_datastream=True)
