from collections import deque
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from gello.cameras.realsense_rig import (
    CameraBundle,
    CameraMiss,
    CameraRig,
    CameraRigConfig,
    CameraSpec,
    SyncPolicy,
    enumerate_realsense_devices,
    load_camera_rig_config,
)
from gello.cameras.realsense_camera import (
    RealSenseFrame,
    RealSenseFrameTimeout,
    RealSenseFrameSource,
)


PRODUCTION_CONFIG = Path(__file__).parents[1] / "configs/realsense_bimanual.yaml"
FOUR_WRIST_CONFIG = (
    Path(__file__).parents[1] / "configs/cameras/realsense_four_wrist.yaml"
)


class FakeDevice:
    def get_info(self, field):
        return {
            FakeRS.camera_info.serial_number: "111111111111",
            FakeRS.camera_info.name: "Intel RealSense D435",
            FakeRS.camera_info.firmware_version: "5.16.0.1",
            FakeRS.camera_info.usb_type_descriptor: "3.2",
        }[field]

    def hardware_reset(self):
        FakeRS.hardware_reset_calls += 1
        raise AssertionError("inventory must not reset hardware")


class FakeContext:
    def query_devices(self):
        return [FakeDevice()]


class FakeRS:
    hardware_reset_calls = 0

    class camera_info:
        serial_number = "serial_number"
        name = "name"
        firmware_version = "firmware_version"
        usb_type_descriptor = "usb_type_descriptor"

    @classmethod
    def for_inventory(cls):
        cls.hardware_reset_calls = 0
        return cls

    @staticmethod
    def context():
        return FakeContext()


class FakeFrame:
    def __init__(self, data, *, frame_number=42, timestamp_ms=1234.5):
        self.data = data
        self.frame_number = frame_number
        self.timestamp_ms = timestamp_ms

    def get_data(self):
        return self.data

    def get_frame_number(self):
        return self.frame_number

    def get_timestamp(self):
        return self.timestamp_ms

    def get_frame_timestamp_domain(self):
        return "hardware_clock"


class FakeClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, amount):
        self.value += amount


class FakeFrames:
    def __init__(self, color, depth):
        self.color = color
        self.depth = depth

    def get_color_frame(self):
        return self.color

    def get_depth_frame(self):
        return self.depth


class FakeConfig:
    def __init__(self):
        self.device_calls = []
        self.stream_calls = []

    def enable_device(self, serial):
        self.device_calls.append(serial)

    def enable_stream(self, *args):
        self.stream_calls.append(args)


class FakePipeline:
    def __init__(
        self, frames, start_error=None, stop_error=None, active_profile=None
    ):
        self.frames = frames
        self.start_error = start_error
        self.stop_error = stop_error
        self.active_profile = active_profile
        self.started_with = []
        self.wait_timeouts = []
        self.stop_calls = 0

    def start(self, config):
        self.started_with.append(config)
        if self.start_error is not None:
            raise self.start_error
        return self.active_profile

    def wait_for_frames(self, timeout_ms):
        self.wait_timeouts.append(timeout_ms)
        if isinstance(self.frames, BaseException):
            raise self.frames
        return self.frames

    def stop(self):
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error


class FakeAlign:
    def __init__(self, rs):
        self.rs = rs

    def process(self, frames):
        self.rs.align_process_calls += 1
        if self.rs.processing_hook is not None:
            self.rs.processing_hook()
        return frames


class FakeColorSensor:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    def supports(self, option):
        return option in {"enable_auto_exposure", "exposure"}

    def get_stream_profiles(self):
        return [FakeStreamProfile()]

    def set_option(self, option, value):
        self.set_calls.append((option, value))
        self.values[option] = value


class FakeStreamProfile:
    def stream_type(self):
        return "color"


class FakeActiveProfile:
    def __init__(self, sensor):
        self.sensor = sensor

    def get_device(self):
        return self

    def query_sensors(self):
        return [self.sensor]


class FakeFrameRS:
    class stream:
        color = "color"
        depth = "depth"

    class format:
        bgr8 = "bgr8"
        z16 = "z16"

    class option:
        enable_auto_exposure = "enable_auto_exposure"
        exposure = "exposure"

    def __init__(self, *, start_error=None, align_error=None, stop_error=None):
        self.format = type("Format", (), {"bgr8": "bgr8", "z16": "z16"})
        self.color_data = np.zeros((480, 640, 3), dtype=np.uint8)
        self.color_data[0, 0] = [1, 2, 3]
        self.depth_data = np.zeros((480, 640), dtype=np.uint16)
        self.depth_data[0, 0] = 99
        self.frames = FakeFrames(
            FakeFrame(self.color_data), FakeFrame(self.depth_data)
        )
        self.color_sensor = FakeColorSensor()
        self.pipeline_instance = FakePipeline(
            self.frames,
            start_error,
            stop_error,
            FakeActiveProfile(self.color_sensor),
        )
        self.config_instance = FakeConfig()
        self.align_calls = 0
        self.align_process_calls = 0
        self.align_error = align_error
        self.processing_hook = None

    @property
    def depth_enable_calls(self):
        return sum(call[0] == self.stream.depth for call in self.config_instance.stream_calls)

    def pipeline(self):
        return self.pipeline_instance

    def config(self):
        return self.config_instance

    def align(self, stream):
        self.align_calls += 1
        if stream != self.stream.color:
            raise AssertionError("depth must be aligned to color")
        if self.align_error is not None:
            raise self.align_error
        return FakeAlign(self)


SPEC = CameraSpec(
    name="main",
    serial="111111111111",
    model="D435",
    width=640,
    height=480,
    fps=30,
    rotation=0,
)


class CameraRigConfigTest(unittest.TestCase):
    def test_production_config_preserves_main_left_right_order(self):
        cfg = load_camera_rig_config(PRODUCTION_CONFIG)

        self.assertEqual(tuple(cfg.cameras), ("main", "left", "right"))
        self.assertEqual(
            [item.serial for item in cfg.cameras.values()],
            [
                "REPLACE_WITH_MAIN_SERIAL",
                "REPLACE_WITH_LEFT_SERIAL",
                "REPLACE_WITH_RIGHT_SERIAL",
            ],
        )
        self.assertIs(cfg.depth_default_enabled, False)
        self.assertEqual(cfg.sync.max_skew_s, 0.025)
        self.assertEqual(cfg.sync.max_frame_age_s, 0.100)
        self.assertEqual(cfg.sync.max_consecutive_failures, 10)
        self.assertEqual(cfg.cameras["main"].exposure_us, 8500)
        self.assertEqual(cfg.cameras["left"].exposure_us, 10000)
        self.assertEqual(cfg.cameras["right"].exposure_us, 10000)

    def test_four_wrist_config_has_four_d405_streams_without_main(self):
        cfg = load_camera_rig_config(FOUR_WRIST_CONFIG)

        self.assertEqual(tuple(cfg.cameras), ("left", "right", "third", "fourth"))
        self.assertEqual(
            [item.serial for item in cfg.cameras.values()],
            ["260322274929", "260422272011", "262422270050", "262422272588"],
        )
        self.assertTrue(all(item.model == "D405" for item in cfg.cameras.values()))
        self.assertTrue(all(item.exposure_us == 10000 for item in cfg.cameras.values()))
        self.assertIs(cfg.depth_default_enabled, False)

    def test_rejects_duplicate_serials(self):
        self.assert_invalid_config("""
cameras:
  first: {serial: "same", model: D435, width: 640, height: 480, fps: 30, rotation: 0}
  second: {serial: "same", model: D405, width: 640, height: 480, fps: 30, rotation: 0}
depth: {default_enabled: false}
sync: {max_skew_ms: 25, max_frame_age_ms: 100, max_consecutive_failures: 10}
""")

    def test_rejects_invalid_rotation(self):
        self.assert_invalid_config(self.valid_config().replace("rotation: 0", "rotation: 45"))

    def test_rejects_non_positive_dimensions(self):
        self.assert_invalid_config(self.valid_config().replace("width: 640", "width: 0"))

    def test_rejects_invalid_sync_thresholds(self):
        self.assert_invalid_config(self.valid_config().replace("max_skew_ms: 25", "max_skew_ms: 0"))

    def test_rejects_non_finite_sync_thresholds(self):
        for value in (".nan", ".inf", "-.inf"):
            with self.subTest(value=value):
                self.assert_invalid_config(
                    self.valid_config().replace("max_skew_ms: 25", f"max_skew_ms: {value}")
                )

    def test_rejects_non_boolean_depth_flag(self):
        self.assert_invalid_config(self.valid_config().replace("default_enabled: false", "default_enabled: 'false'"))

    def test_requires_manual_exposure_to_be_complete(self):
        self.assert_invalid_config(
            self.valid_config().replace(
                "rotation: 0", "rotation: 0\n    auto_exposure: false", 1
            )
        )
        self.assert_invalid_config(
            self.valid_config().replace(
                "rotation: 0", "rotation: 0\n    exposure_us: 10000", 1
            )
        )

    def assert_invalid_config(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rig.yaml"
            path.write_text(text)
            with self.assertRaises(ValueError):
                load_camera_rig_config(path)

    @staticmethod
    def valid_config():
        return """
cameras:
  main:
    serial: "111111111111"
    model: D435
    width: 640
    height: 480
    fps: 30
    rotation: 0
depth:
  default_enabled: false
sync:
  max_skew_ms: 25
  max_frame_age_ms: 100
  max_consecutive_failures: 10
"""


class RealSenseInventoryTest(unittest.TestCase):
    def test_inventory_never_resets_hardware(self):
        devices = enumerate_realsense_devices(FakeRS.for_inventory())

        self.assertEqual([device.serial for device in devices], ["111111111111"])
        self.assertEqual(devices[0].name, "Intel RealSense D435")
        self.assertEqual(devices[0].model, "D435")
        self.assertEqual(FakeRS.hardware_reset_calls, 0)


class RealSenseFrameSourceTest(unittest.TestCase):
    def test_sdk_frame_wait_timeout_is_exposed_as_recoverable_timeout(self):
        fake_rs = FakeFrameRS()
        fake_rs.pipeline_instance.frames = RuntimeError(
            "Frame didn't arrive within 250"
        )
        source = RealSenseFrameSource(SPEC, depth_enabled=False, rs_module=fake_rs)
        source.start()

        with self.assertRaises(RealSenseFrameTimeout):
            source.read(timeout_ms=250)

    def test_rgb_only_source_binds_exact_device_and_converts_bgr_to_rgb(self):
        fake_rs = FakeFrameRS()
        source = RealSenseFrameSource(
            SPEC, depth_enabled=False, rs_module=fake_rs, clock=lambda: 8.5
        )

        source.start()
        frame = source.read(timeout_ms=250)

        self.assertEqual(fake_rs.config_instance.device_calls, [SPEC.serial])
        self.assertEqual(
            fake_rs.config_instance.stream_calls,
            [(fake_rs.stream.color, 640, 480, fake_rs.format.bgr8, 30)],
        )
        self.assertEqual(fake_rs.pipeline_instance.wait_timeouts, [250])
        self.assertEqual(frame.rgb.shape, (480, 640, 3))
        self.assertEqual(frame.rgb.dtype, np.uint8)
        np.testing.assert_array_equal(frame.rgb[0, 0], [3, 2, 1])
        self.assertIsNone(frame.depth)
        self.assertEqual(fake_rs.depth_enable_calls, 0)
        self.assertEqual(fake_rs.align_calls, 0)
        self.assertEqual(frame.camera_name, SPEC.name)
        self.assertEqual(frame.serial, SPEC.serial)
        self.assertEqual(frame.frame_number, 42)
        self.assertEqual(frame.device_timestamp_ms, 1234.5)
        self.assertEqual(frame.timestamp_domain, "hardware_clock")
        self.assertEqual(frame.host_timestamp, 8.5)

    def test_source_prefers_rgb8_when_sdk_exposes_it(self):
        fake_rs = FakeFrameRS()
        fake_rs.format.rgb8 = "rgb8"
        source = RealSenseFrameSource(SPEC, depth_enabled=False, rs_module=fake_rs)

        source.start()
        frame = source.read()

        self.assertEqual(
            fake_rs.config_instance.stream_calls,
            [(fake_rs.stream.color, 640, 480, fake_rs.format.rgb8, 30)],
        )
        self.assertEqual(frame.rgb.shape, (480, 640, 3))
        self.assertEqual(frame.rgb.dtype, np.uint8)
        np.testing.assert_array_equal(frame.rgb[0, 0], [1, 2, 3])

    def test_source_applies_configured_manual_exposure(self):
        fake_rs = FakeFrameRS()
        spec = CameraSpec(
            **{
                **SPEC.__dict__,
                "auto_exposure": False,
                "exposure_us": 10000,
            }
        )
        source = RealSenseFrameSource(spec, depth_enabled=False, rs_module=fake_rs)

        source.start()

        self.assertEqual(fake_rs.color_sensor.set_calls, [("enable_auto_exposure", 1.0)])
        source.read()

        self.assertEqual(
            fake_rs.color_sensor.set_calls,
            [
                ("enable_auto_exposure", 1.0),
                ("enable_auto_exposure", 0.0),
                ("exposure", 10000.0),
            ],
        )

    def test_depth_source_returns_aligned_uint16_depth(self):
        fake_rs = FakeFrameRS()
        source = RealSenseFrameSource(SPEC, depth_enabled=True, rs_module=fake_rs)

        source.start()
        frame = source.read(timeout_ms=250)

        self.assertEqual(
            fake_rs.config_instance.stream_calls,
            [
                (fake_rs.stream.color, 640, 480, fake_rs.format.bgr8, 30),
                (fake_rs.stream.depth, 640, 480, fake_rs.format.z16, 30),
            ],
        )
        self.assertEqual(frame.depth.shape, (480, 640))
        self.assertEqual(frame.depth.dtype, np.uint16)
        self.assertEqual(frame.depth[0, 0], 99)
        self.assertEqual(fake_rs.align_calls, 1)
        self.assertEqual(fake_rs.align_process_calls, 1)

    def test_source_rotates_and_copies_published_arrays(self):
        fake_rs = FakeFrameRS()
        fake_rs.color_data = np.array(
            [[[1, 2, 3], [4, 5, 6]], [[7, 8, 9], [10, 11, 12]]], dtype=np.uint8
        )
        fake_rs.depth_data = np.array([[1, 2], [3, 4]], dtype=np.uint16)
        fake_rs.frames = FakeFrames(FakeFrame(fake_rs.color_data), FakeFrame(fake_rs.depth_data))
        fake_rs.pipeline_instance.frames = fake_rs.frames
        rotated_spec = CameraSpec(**{**SPEC.__dict__, "rotation": 90})
        source = RealSenseFrameSource(rotated_spec, depth_enabled=True, rs_module=fake_rs)

        source.start()
        frame = source.read()
        fake_rs.color_data[:, :, :] = 0
        fake_rs.depth_data[:, :] = 0

        np.testing.assert_array_equal(
            frame.rgb,
            [[[6, 5, 4], [12, 11, 10]], [[3, 2, 1], [9, 8, 7]]],
        )
        np.testing.assert_array_equal(frame.depth, [[2, 4], [1, 3]])

    def test_start_failure_does_not_stop_a_pipeline_that_never_started(self):
        fake_rs = FakeFrameRS(
            start_error=RuntimeError("startup failure"),
            stop_error=AssertionError("stop must not run"),
        )
        source = RealSenseFrameSource(SPEC, depth_enabled=False, rs_module=fake_rs)

        with self.assertRaisesRegex(RuntimeError, "startup failure"):
            source.start()

        self.assertEqual(fake_rs.pipeline_instance.stop_calls, 0)
        self.assertIsNone(source._pipeline)
        self.assertIsNone(source._align)
        self.assertIsNone(source._color_format)
        self.assertFalse(source._started)

    def test_start_rolls_back_pipeline_when_alignment_setup_fails(self):
        fake_rs = FakeFrameRS(
            align_error=RuntimeError("alignment failure"),
            stop_error=RuntimeError("cleanup failure"),
        )
        source = RealSenseFrameSource(SPEC, depth_enabled=True, rs_module=fake_rs)

        with self.assertRaisesRegex(RuntimeError, "alignment failure"):
            source.start()

        self.assertEqual(fake_rs.pipeline_instance.stop_calls, 1)
        self.assertIsNone(source._pipeline)
        self.assertIsNone(source._align)
        self.assertIsNone(source._color_format)
        self.assertFalse(source._started)

    def test_read_timestamps_frame_arrival_before_alignment_processing(self):
        fake_rs = FakeFrameRS()
        clock = FakeClock(10.0)
        fake_rs.processing_hook = lambda: clock.advance(5.0)
        source = RealSenseFrameSource(
            SPEC, depth_enabled=True, rs_module=fake_rs, clock=clock
        )

        source.start()
        frame = source.read()

        self.assertEqual(frame.host_timestamp, 10.0)
        self.assertEqual(clock.value, 15.0)

    def test_close_is_idempotent(self):
        fake_rs = FakeFrameRS()
        source = RealSenseFrameSource(SPEC, depth_enabled=False, rs_module=fake_rs)
        source.start()

        source.close()
        source.close()

        self.assertEqual(fake_rs.pipeline_instance.stop_calls, 1)


RIG_SPECS = {
    name: CameraSpec(
        name=name,
        serial=f"serial-{name}",
        model="D435" if name == "main" else "D405",
        width=2,
        height=2,
        fps=30,
        rotation=0,
    )
    for name in ("main", "left", "right")
}
RIG_CONFIG = CameraRigConfig(
    cameras=RIG_SPECS,
    depth_default_enabled=False,
    sync=SyncPolicy(
        max_skew_s=0.025,
        max_frame_age_s=0.100,
        max_consecutive_failures=10,
    ),
)


def rig_frame(name, frame_number, timestamp):
    return RealSenseFrame(
        camera_name=name,
        serial=RIG_SPECS[name].serial,
        rgb=np.zeros((2, 2, 3), dtype=np.uint8),
        depth=None,
        frame_number=frame_number,
        device_timestamp_ms=timestamp * 1000,
        timestamp_domain="hardware_clock",
        host_timestamp=timestamp,
    )


class FakeRigSource:
    def __init__(self, items=(), *, start_barrier=None, start_error=None):
        self._items = deque(items)
        self._condition = threading.Condition()
        self._start_barrier = start_barrier
        self._start_error = start_error
        self.opened = False
        self.start_calls = 0
        self.close_calls = 0
        self.read_timeouts = []

    def start(self):
        self.start_calls += 1
        if self._start_barrier is not None:
            self._start_barrier.wait(timeout=1.0)
        if self._start_error is not None:
            raise self._start_error
        self.opened = True

    def read(self, timeout_ms=250):
        self.read_timeouts.append(timeout_ms)
        with self._condition:
            while not self._items and self.opened:
                self._condition.wait(timeout=0.1)
            if not self.opened:
                raise RuntimeError("source closed")
            item = self._items.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def push(self, *items):
        with self._condition:
            self._items.extend(items)
            self._condition.notify_all()

    def close(self):
        with self._condition:
            if not self.opened:
                return
            self.opened = False
            self.close_calls += 1
            self._condition.notify_all()


def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("condition was not reached before timeout")


def started_fake_rig(times, *, frame_number=1):
    sources = {
        name: FakeRigSource([rig_frame(name, frame_number, times[name])])
        for name in RIG_SPECS
    }
    rig = CameraRig(RIG_CONFIG, sources=sources)
    rig.start(warmup_frames=1, timeout_s=1.0)
    return rig, sources


class CameraRigTest(unittest.TestCase):
    def test_frame_wait_timeout_retries_within_overall_warmup_window(self):
        sources = {
            name: FakeRigSource(
                [TimeoutError("transient"), rig_frame(name, 1, 1.0)]
            )
            for name in RIG_SPECS
        }
        rig = CameraRig(RIG_CONFIG, sources=sources)
        self.addCleanup(rig.close)

        rig.start(warmup_frames=1, timeout_s=2.0)

        self.assertTrue(all(rig.status()[name].state == "streaming" for name in RIG_SPECS))

    def test_warmup_allows_one_second_for_each_first_frame(self):
        sources = {
            name: FakeRigSource([rig_frame(name, 1, 1.0)]) for name in RIG_SPECS
        }
        rig = CameraRig(RIG_CONFIG, sources=sources)
        self.addCleanup(rig.close)

        rig.start(warmup_frames=1, timeout_s=2.0)

        self.assertEqual(
            {name: source.read_timeouts[0] for name, source in sources.items()},
            {"main": 1000, "left": 1000, "right": 1000},
        )

    def test_start_opens_sources_concurrently_and_rolls_back_opened_sources(self):
        barrier = threading.Barrier(3)
        sources = {
            name: FakeRigSource(
                [rig_frame(name, 1, 1.0)],
                start_barrier=barrier,
                start_error=(RuntimeError("left failed") if name == "left" else None),
            )
            for name in RIG_SPECS
        }
        rig = CameraRig(RIG_CONFIG, sources=sources)

        with self.assertRaisesRegex(RuntimeError, "left failed"):
            rig.start(warmup_frames=1, timeout_s=1.0)

        self.assertEqual([source.start_calls for source in sources.values()], [1, 1, 1])
        self.assertEqual(sources["main"].close_calls, 1)
        self.assertEqual(sources["left"].close_calls, 0)
        self.assertEqual(sources["right"].close_calls, 1)

    def test_complete_bundle_uses_each_new_frame_once(self):
        rig, _ = started_fake_rig(
            times={"main": 1.000, "left": 1.010, "right": 1.024}
        )
        self.addCleanup(rig.close)

        result = rig.sample(reference_time=1.025)

        self.assertIsInstance(result, CameraBundle)
        self.assertAlmostEqual(result.skew_s, 0.024)
        self.assertEqual(tuple(result.frames), ("main", "left", "right"))
        self.assertIsInstance(rig.sample(reference_time=1.026), CameraMiss)

    def test_sample_prefers_closest_valid_bundle_over_older_lower_skew_bundle(self):
        times = {"main": 0.990, "left": 0.980, "right": 0.970}
        sources = {
            name: FakeRigSource(
                [rig_frame(name, 1, 0.900), rig_frame(name, 2, times[name])]
            )
            for name in RIG_SPECS
        }
        rig = CameraRig(RIG_CONFIG, sources=sources)
        self.addCleanup(rig.close)
        rig.start(warmup_frames=2, timeout_s=1.0)

        result = rig.sample(reference_time=1.000)

        self.assertIsInstance(result, CameraBundle)
        self.assertEqual(
            [frame.frame_number for frame in result.frames.values()], [2, 2, 2]
        )

    def test_25_ms_skew_is_accepted_and_26_ms_is_rejected(self):
        accepted, _ = started_fake_rig(
            times={"main": 1.000, "left": 1.010, "right": 1.025}
        )
        rejected, _ = started_fake_rig(
            times={"main": 1.000, "left": 1.010, "right": 1.026}
        )
        self.addCleanup(accepted.close)
        self.addCleanup(rejected.close)

        self.assertIsInstance(accepted.sample(reference_time=1.027), CameraBundle)
        miss = rejected.sample(reference_time=1.027)
        self.assertIsInstance(miss, CameraMiss)
        self.assertEqual(miss.reason, "skew")

    def test_100_ms_age_is_accepted_and_older_frame_is_rejected(self):
        accepted, _ = started_fake_rig(
            times={"main": 1.000, "left": 1.000, "right": 1.000}
        )
        rejected, _ = started_fake_rig(
            times={"main": 1.000, "left": 1.000, "right": 1.000}
        )
        self.addCleanup(accepted.close)
        self.addCleanup(rejected.close)

        self.assertIsInstance(accepted.sample(reference_time=1.100), CameraBundle)
        miss = rejected.sample(reference_time=1.101)
        self.assertIsInstance(miss, CameraMiss)
        self.assertEqual(miss.reason, "age")

    def test_success_resets_consecutive_miss_count(self):
        rig, sources = started_fake_rig(
            times={"main": 1.000, "left": 1.000, "right": 1.000}
        )
        self.addCleanup(rig.close)

        first_miss = rig.sample(reference_time=1.101)
        self.assertEqual(first_miss.consecutive, 1)
        for name, source in sources.items():
            source.push(rig_frame(name, 2, 1.200))
        wait_until(lambda: all(item.frames_received == 2 for item in rig.status().values()))

        self.assertIsInstance(rig.sample(reference_time=1.201), CameraBundle)
        next_miss = rig.sample(reference_time=1.202)
        self.assertEqual(next_miss.consecutive, 1)

    def test_begin_episode_discards_pre_episode_frames_and_resets_misses(self):
        rig, sources = started_fake_rig(
            times={"main": 1.000, "left": 1.000, "right": 1.000}
        )
        self.addCleanup(rig.close)
        self.assertEqual(rig.sample(reference_time=1.101).consecutive, 1)

        rig.begin_episode()

        miss = rig.sample(reference_time=1.102)
        self.assertEqual(miss.reason, "no_new_frame")
        self.assertEqual(miss.consecutive, 1)
        for name, source in sources.items():
            source.push(rig_frame(name, 2, 1.200))
        wait_until(lambda: all(item.frames_received == 2 for item in rig.status().values()))
        self.assertIsInstance(rig.sample(reference_time=1.201), CameraBundle)

    def test_disconnect_and_other_acquisition_errors_are_structurally_distinct(self):
        rig, sources = started_fake_rig(
            times={"main": 1.000, "left": 1.000, "right": 1.000}
        )
        self.addCleanup(rig.close)

        sources["left"].push(ConnectionError("USB device disconnected"))
        sources["right"].push(ValueError("invalid frameset"))
        wait_until(
            lambda: rig.status()["left"].state == "disconnected"
            and rig.status()["right"].state == "acquisition_error"
        )

        health = rig.status()
        self.assertEqual(health["left"].error_type, "ConnectionError")
        self.assertEqual(health["right"].error_type, "ValueError")
        miss = rig.sample(reference_time=1.001)
        self.assertEqual(miss.reason, "disconnected")
        self.assertIn("left", miss.detail)

    def test_status_reports_measured_fps_and_buffers_remain_bounded(self):
        sources = {
            name: FakeRigSource(
                [rig_frame(name, number, 1.0 + (number - 1) * 0.1) for number in range(1, 7)]
            )
            for name in RIG_SPECS
        }
        rig = CameraRig(RIG_CONFIG, sources=sources)
        self.addCleanup(rig.close)

        rig.start(warmup_frames=6, timeout_s=1.0)

        self.assertAlmostEqual(rig.status()["main"].measured_fps, 10.0)
        self.assertEqual([frame.frame_number for frame in rig._buffers["main"]], [3, 4, 5, 6])

    def test_sample_is_non_blocking_and_close_is_idempotent(self):
        rig, sources = started_fake_rig(
            times={"main": 1.000, "left": 1.000, "right": 1.000}
        )

        rig.sample(reference_time=1.001)
        started_at = time.monotonic()
        miss = rig.sample(reference_time=1.002)
        elapsed = time.monotonic() - started_at
        rig.close()
        rig.close()

        self.assertIsInstance(miss, CameraMiss)
        self.assertLess(elapsed, 0.05)
        self.assertEqual([source.close_calls for source in sources.values()], [1, 1, 1])
