import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import h5py
import numpy as np

from gello.data_utils.mcap_recording import McapCatalog, McapEpisodeWriter
from gello.data_utils.mcap_tools import export_hdf5, inspect, messages
from gello.safety.controller import SafetyController, SafetyState
from tests.test_safety_controller import FakeRobot


class BufferedClient:
    def get_recording_history(self, since):
        return {side: {"samples": [sample] if sample["source_time_ns"] > since[side] else [],
                       "overflow": False}
                for side, sample in self.get_recording_state().items()}


class McapRecordingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def writer(self, **kwargs):
        kwargs.setdefault("image_encoding", "raw+zstd")
        writer = McapEpisodeWriter(self.root, "task", 200, **kwargs)
        self.addCleanup(writer.close)
        return writer

    def test_independent_streams_and_lossless_export(self):
        camera = dict(serial="fake", model="D405", width=8, height=6, fps=30, rotation=0)
        writer = self.writer(camera_metadata={"main": camera}, queue_size=1024)
        writer.start()
        begin = writer._epoch
        image = np.arange(144, dtype=np.uint8).reshape(6, 8, 3)
        # No camera between 33 and 100 ms. Robot streams still preserve every sample.
        for tick in range(41):
            stamp = begin + tick * 5_000_000
            for side in ("left", "right"):
                writer.record(
                    f"/{side}/state",
                    dict(position=[tick / 1000] * 7, velocity=[0] * 7, effort=[0] * 7),
                    stamp,
                )
            for kind in ("requested", "accepted"):
                writer.record(f"/action/{kind}", dict(position=[tick / 1000] * 14), stamp)
            if tick in (0, 7, 20, 27, 34, 40):
                writer.record(
                    "/cameras/main/rgb", dict(frame_number=tick, serial="fake"), stamp, image=image
                )
        path = writer.commit("success")
        result = inspect(path)
        self.assertEqual(result["streams"]["/left/state"]["count"], 41)
        self.assertEqual(result["streams"]["/cameras/main/rgb"]["count"], 6)
        self.assertAlmostEqual(result["streams"]["/action/accepted"]["measured_hz"], 200)
        exported = export_hdf5(path, self.root / "export", "task", "success")
        self.assertGreater(exported["skipped_rows"], 0)
        with h5py.File(exported["path"]) as f:
            np.testing.assert_array_equal(f["observations/images/main"][0], image)
            self.assertTrue(np.all(np.diff(f["observations/camera_frame_number/main"][:]) > 0))

    def test_commit_publish_failure_can_retry(self):
        writer = self.writer()
        writer.start()
        writer.append({}, np.zeros(14), 0, leader_action=np.zeros(14))
        with patch("gello.data_utils.mcap_recording.os.link", side_effect=OSError("disk busy")):
            with self.assertRaises(OSError):
                writer.commit("success")
        self.assertIsNotNone(writer.path)
        self.assertTrue(writer.commit("success").exists())

    def test_incomplete_recovery_and_namespace(self):
        writer = self.writer()
        writer.start()
        writer.append({}, np.zeros(14), 0, leader_action=np.zeros(14))
        writer.close_incomplete()
        source = writer.path
        # Lose the footer / summary tail, retaining complete data chunks.
        with source.open("r+b") as f:
            f.truncate(source.stat().st_size - 30)
        catalog = McapCatalog(self.root, "task")
        recovered = catalog.recover_incomplete(source, "fail")
        self.assertTrue(recovered.exists())
        self.assertFalse(source.exists())
        self.assertTrue(list(messages(recovered)))
        self.assertEqual(catalog.next_episode_index, 2)

    def test_state_poll_deduplicates_feedback_and_closes_own_client(self):
        class Client(BufferedClient):
            closed = False
            stamp = None

            def get_recording_state(self):
                if self.stamp is None:
                    self.stamp = time.time_ns()
                return {
                    side: dict(
                        source_time_ns=self.stamp, position=[0] * 7, velocity=[0] * 7, effort=[0] * 7
                    )
                    for side in ("left", "right")
                }

            def close(self):
                self.closed = True

        client = Client()
        writer = self.writer()
        writer.bind(lambda: client, None)
        writer.start()
        time.sleep(0.03)
        writer.append({}, np.zeros(14), 0, leader_action=np.zeros(14))
        path = writer.commit("fail")
        self.assertEqual(inspect(path)["streams"]["/left/state"]["count"], 1)
        self.assertTrue(client.closed)

    def test_stale_state_fault_preserves_incomplete(self):
        class Client(BufferedClient):
            def get_recording_state(self):
                return {
                    side: dict(
                        source_time_ns=time.time_ns() - 1_000_000_000,
                        position=[0] * 7,
                        velocity=[0] * 7,
                        effort=[0] * 7,
                    )
                    for side in ("left", "right")
                }

            def close(self):
                pass

        writer = self.writer()
        writer.bind(Client, None)
        writer.start()
        deadline = time.monotonic() + 1
        while writer.error is None and time.monotonic() < deadline:
            time.sleep(0.005)
        with self.assertRaisesRegex(RuntimeError, "stale"):
            writer.raise_if_failed()
        writer.close_incomplete()
        self.assertTrue(writer.path.exists())


class TimedControlTest(unittest.TestCase):
    def controller(self):
        self.now = 1.0
        c = SafetyController(FakeRobot(), required_clients=(), clock=lambda: self.now)
        c.begin_session("test")
        c.activate("test")
        return c

    def test_rate_invariant_slew_limit_and_command_deadline(self):
        c = self.controller()
        self.now += 0.005
        result = c.command_joint_state(np.ones(14), command_period_s=0.005)
        self.assertAlmostEqual(result["applied_joint_state"][0], 0.03)
        self.now += 0.010
        later = c.command_joint_state(np.ones(14), command_period_s=0.005)
        self.assertAlmostEqual(later["applied_joint_state"][0], 0.09)
        self.now += 0.201
        self.assertTrue(c.check_leases())
        self.assertEqual(c.state, SafetyState.SAFE_HOLD)

    def test_no_extra_motion_budget_from_rapid_requests(self):
        c = self.controller()
        self.now += 0.005
        first = c.command_joint_state(np.ones(14), command_period_s=0.005)
        second = c.command_joint_state(np.ones(14), command_period_s=0.005)
        np.testing.assert_array_equal(first["applied_joint_state"], second["applied_joint_state"])

    def test_invalid_period_and_mode_switch_rejected(self):
        c = self.controller()
        with self.assertRaises(ValueError):
            c.command_joint_state(np.zeros(14), command_period_s=float("nan"))
        self.now += 0.005
        c.command_joint_state(np.zeros(14), command_period_s=0.005)
        with self.assertRaises(RuntimeError):
            c.command_joint_state(np.zeros(14))


class McapIntegrationTest(unittest.TestCase):
    def test_collection_freezes_sources_before_label_and_stops_before_discard(self):
        import queue

        from gello.data_utils.collection_session import run_collection_session
        from tests.test_collection_session import FakeAgent, FakeEnv
        from tests.test_collection_session import FakeRobot as CollectionRobot

        with tempfile.TemporaryDirectory() as temp:
            writer = McapEpisodeWriter(temp, "test", 200)
            commands = queue.Queue()
            robot = CollectionRobot()
            events = []

            def emit(event):
                events.append(event)
                if event["type"] == "episode_started":
                    commands.put({"type": "finish_episode"})
                elif event["type"] == "awaiting_label":
                    self.assertTrue(writer._source_stop.is_set())
                    commands.put({"type": "label", "outcome": "success"})
                elif event["type"] == "next_ready":
                    commands.put({"type": "exit_task"})

            try:
                run_collection_session(
                    wait_for_triggers=False,
                    env=FakeEnv(np.zeros(14)),
                    agent=FakeAgent(np.zeros(14)),
                    robot=robot,
                    writer=writer,
                    commands=commands,
                    emit=emit,
                )
                path = next(e["path"] for e in events if e["type"] == "episode_committed")
                self.assertEqual(inspect(path)["streams"]["/action/accepted"]["count"], 1)
                self.assertEqual(events[-1]["type"], "safe_hold_active")
            finally:
                writer.close()

    def test_atomic_hardware_snapshot_is_copied(self):
        import threading
        from types import SimpleNamespace

        from i2rt.robots.motor_chain_robot import MotorChainRobot

        robot = MotorChainRobot.__new__(MotorChainRobot)
        robot._state_lock = threading.Lock()
        robot._joint_state = SimpleNamespace(
            timestamp=1.25, pos=np.ones(7), vel=np.zeros(7), eff=np.zeros(7)
        )
        result = robot.get_recording_state()
        robot._joint_state.pos[0] = 9
        self.assertEqual(result["position"][0], 1)
        self.assertEqual(result["source_time_ns"], 1_250_000_000)

    def test_stopping_holds_before_disk_discard(self):
        import queue

        from gello.data_utils.collection_session import run_collection_session
        from tests.test_collection_session import FakeAgent, FakeEnv, FakeWriter
        from tests.test_collection_session import FakeRobot as CollectionRobot

        robot = CollectionRobot()
        writer = FakeWriter()
        commands = queue.Queue()
        original = writer.discard

        def discard():
            self.assertIn("collection task exiting", robot.safe_hold_reasons)
            original()

        writer.discard = discard

        def emit(event):
            if event["type"] == "episode_started":
                commands.put({"type": "exit_task"})

        run_collection_session(
            wait_for_triggers=False,
            env=FakeEnv(np.zeros(14)),
            agent=FakeAgent(np.zeros(14)),
            robot=robot,
            writer=writer,
            commands=commands,
            emit=emit,
        )

    def test_cli_accepts_mcap_but_rejects_high_rate_hdf5(self):
        from scripts.run_bimanual_teleop import parse_args
        from tests.test_profile_doctor import ProfileFixture

        fixture = ProfileFixture()
        fixture.setUp()
        try:
            args = parse_args(
                [
                    "--profile",
                    str(fixture.profile),
                    "--recording-format",
                    "mcap",
                    "--control-hz",
                    "200",
                    "--state-hz",
                    "300",
                ]
            )
            self.assertEqual(args.control_hz, 200)
            self.assertEqual(args.state_hz, 300)
            with self.assertRaises(SystemExit):
                parse_args(["--profile", str(fixture.profile), "--control-hz", "200"])
        finally:
            fixture.doCleanups()


class McapCameraAndBackpressureTest(unittest.TestCase):
    def test_camera_gaps_do_not_remove_state_and_depth_is_lossless(self):
        from types import SimpleNamespace

        from gello.cameras.realsense_camera import RealSenseFrame

        class Client(BufferedClient):
            def get_recording_state(self):
                return {
                    side: dict(
                        source_time_ns=time.time_ns(),
                        position=[0] * 7,
                        velocity=[0] * 7,
                        effort=[0] * 7,
                    )
                    for side in ("left", "right")
                }

            def close(self):
                pass

        class Rig:
            config = SimpleNamespace(cameras={"main": None})
            frames = ()

            def recording_frames(self):
                return {"main": self.frames}

        rig = Rig()
        with tempfile.TemporaryDirectory() as temp:
            writer = McapEpisodeWriter(
                temp,
                "camera",
                200,
                camera_metadata={
                    "main": dict(serial="fake", model="D405", width=3, height=2, fps=30, rotation=0)
                },
                depth_enabled=True,
                image_encoding="raw+zstd",
            )
            writer.bind(Client, rig)
            try:
                writer.start()
                depth = np.arange(6, dtype=np.uint16).reshape(2, 3) * 1000
                for frame_number in (1, 3):
                    rig.frames = (
                        RealSenseFrame(
                            "main",
                            "fake",
                            np.zeros((2, 3, 3), np.uint8),
                            depth,
                            frame_number,
                            frame_number * 33.3,
                            "hardware_clock",
                            time.monotonic(),
                        ),
                    )
                    time.sleep(0.03)
                writer.append({}, np.zeros(14), 0, leader_action=np.zeros(14))
                path = writer.commit("success")
                report = inspect(path)
                self.assertEqual(report["streams"]["/cameras/main/rgb"]["count"], 2)
                self.assertGreater(report["streams"]["/left/state"]["count"], 2)
                self.assertEqual(report["streams"]["/events"]["count"], 1)
                import base64

                frame = next(messages(path, "/cameras/main/depth"))[2]
                np.testing.assert_array_equal(
                    np.frombuffer(base64.b64decode(frame["data"]), "<u2").reshape(2, 3), depth
                )
            finally:
                writer.close()

    def test_queue_overflow_is_explicit_and_preserves_partial_file(self):
        import queue

        with tempfile.TemporaryDirectory() as temp:
            writer = McapEpisodeWriter(temp, "overflow", 200)
            try:
                writer.start()
                with patch.object(writer._queue, "put_nowait", side_effect=queue.Full):
                    with self.assertRaisesRegex(RuntimeError, "queue full"):
                        writer.append({}, np.zeros(14), 0, leader_action=np.zeros(14))
                writer.close_incomplete()
                self.assertTrue(writer.path.exists())
                self.assertFalse(list((writer.root / "success").glob("*.mcap")))
            finally:
                writer.close()
