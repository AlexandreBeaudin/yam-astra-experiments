"""Regression checks for real codecs, buffered feedback and bounded control work."""

import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import h5py
import numpy as np
from i2rt.robots.motor_chain_robot import MotorChainRobot

from gello.data_utils.mcap_recording import McapEpisodeWriter
from gello.data_utils.mcap_tools import export_hdf5, inspect, messages
from gello.data_utils.video_codec import H264Encoder
from gello.env import Rate, RobotEnv


class MultiratePerformanceTest(TestCase):
    def test_real_update_publishes_feedback_without_reacquiring_state_lock(self):
        """Exercise update -> _update_joint_state, not just a pre-filled history."""
        robot = MotorChainRobot.__new__(MotorChainRobot)
        robot._command_lock = threading.Lock()
        robot._state_lock = threading.Lock()
        robot._recording_history = deque(maxlen=2)
        robot._recording_evicted_ns = None
        robot._commands = SimpleNamespace(
            **{field: np.zeros(7) for field in ("torques", "pos", "vel", "kp", "kd")}
        )
        robot._joint_state = SimpleNamespace(
            timestamp=1.0, pos=np.zeros(7), vel=np.zeros(7), eff=np.zeros(7)
        )
        robot._compute_gravity_compensation = lambda state: np.zeros(7)
        robot.gravity_comp_factor = 1
        robot.use_coulomb_friction = False
        robot._clip_motor_torque = np.ones(7)
        robot._gripper_index = None
        robot._joint_state_saver = None
        robot._check_current_qpos_in_joint_limits = lambda: None
        calls = []

        def set_commands(*args, **kwargs):
            calls.append(1)
            return SimpleNamespace(
                timestamp=1 + len(calls),
                pos=np.ones(7) * len(calls),
                vel=np.zeros(7),
                eff=np.zeros(7),
            )

        robot.motor_chain = SimpleNamespace(set_commands=set_commands)
        robot._motor_state_to_joint_state = lambda state: state
        errors = []

        def update():
            try:
                for _ in range(3):
                    robot.update()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=update, daemon=True)
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive(), "control update deadlocked on its own state lock")
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 3)
        history = robot.get_recording_history(2_000_000_000)
        self.assertFalse(history["overflow"])
        self.assertEqual(
            [s["source_time_ns"] for s in history["samples"]], [3_000_000_000, 4_000_000_000]
        )
        self.assertTrue(robot.get_recording_history(1_000_000_000)["overflow"])
        self.assertEqual(robot.get_recording_state()["position"][0], 3)

    def test_overflow_aborts_and_retains_partial_file(self):
        class Client:
            def get_recording_history(self, since):
                return {s: dict(samples=[], overflow=True) for s in since}

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            writer = McapEpisodeWriter(tmp, "overflow", 200)
            writer.bind(Client, None)
            try:
                writer.start()
                deadline = time.monotonic() + 1
                while writer.error is None and time.monotonic() < deadline:
                    time.sleep(0.005)
                with self.assertRaisesRegex(RuntimeError, "history overflow"):
                    writer.raise_if_failed()
                writer.close_incomplete()
                self.assertTrue(writer.path.exists())
                self.assertFalse(list((writer.root / "success").glob("*.mcap")))
            finally:
                writer.close()

    def test_encoder_failure_never_publishes_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = McapEpisodeWriter(
                tmp,
                "bad_video",
                200,
                camera_metadata={"main": dict(serial="fake", width=3, height=3, fps=30)},
            )
            try:
                writer.start()
                writer.append({}, np.zeros(14), 0, leader_action=np.zeros(14))
                writer.record(
                    "/cameras/main/rgb",
                    dict(frame_number=1),
                    time.time_ns(),
                    image=np.zeros((3, 3, 3), np.uint8),
                )
                with self.assertRaisesRegex(RuntimeError, "even"):
                    writer.commit("success")
                self.assertTrue(writer.path.exists())
                self.assertFalse(list((writer.root / "success").glob("*.mcap")))
                self.assertIsNone(writer._encoder)
            finally:
                writer.close()

    def test_prepare_encoder_runs_before_control_activation(self):
        import queue

        from gello.data_utils.collection_session import run_collection_session
        from tests.test_collection_session import FakeAgent, FakeEnv, FakeRobot, FakeWriter

        writer, robot = FakeWriter(), FakeRobot()
        order = []
        writer.prepare = lambda: order.append("prepare")
        commands = queue.Queue()

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
            activate_control=lambda: order.append("activate"),
        )
        self.assertEqual(order, ["prepare", "activate"])

    def test_h264_full_episode_decode_export_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = McapEpisodeWriter(
                tmp,
                "codec",
                200,
                camera_metadata={
                    "main": dict(
                        serial="fake", model="D435", width=64, height=48, fps=30, rotation=0
                    )
                },
            )
            try:
                w.start()
                begin = w._epoch
                expected = []
                for i in range(12):
                    stamp = begin + i * 33_333_333
                    pixels = np.full((48, 64, 3), 60 + i * 5, np.uint8)
                    pixels[10:25, 10 + i : 25 + i] = 160
                    expected.append((stamp, pixels))
                    for side in ("left", "right"):
                        w.record(
                            f"/{side}/state",
                            dict(position=[i / 100] * 7, velocity=[0] * 7, effort=[0] * 7),
                            stamp,
                        )
                    for kind in ("requested", "accepted"):
                        w.record(f"/action/{kind}", dict(position=[i / 100] * 14), stamp)
                    w.record(
                        "/cameras/main/rgb",
                        dict(frame_number=i, serial="fake"),
                        stamp,
                        image=pixels,
                    )
                path = w.commit("success")
                frames = list(messages(path, "/cameras/main/rgb", decode_images=True))
                self.assertEqual(len(frames), len(expected))
                self.assertTrue(frames[0][2]["keyframe"])
                for (_, stamp, payload), (wanted_stamp, pixels) in zip(frames, expected):
                    self.assertEqual(stamp, wanted_stamp)
                    self.assertEqual(payload["encoding"], "h264")
                    self.assertLess(np.abs(payload["_pixels"].astype(float) - pixels).mean(), 4)
                self.assertLess(path.stat().st_size, 48 * 64 * 3 * 12)
                self.assertTrue(
                    inspect(path)["warnings"]
                )  # 30 Hz actions do not masquerade as 200 Hz.
                result = export_hdf5(path, Path(tmp) / "export", "codec")
                with h5py.File(result["path"]) as f:
                    self.assertGreater(f["observations/images/main"].shape[0], 5)
            finally:
                w.close()

    def test_encoder_failure_is_bounded(self):
        enc = H264Encoder()
        try:
            with self.assertRaisesRegex(RuntimeError, "even"):
                enc.encode("main", np.zeros((3, 3, 3), np.uint8), 1)
        finally:
            enc.close()
        self.assertFalse(enc.process.is_alive())

    def test_history_copies_every_native_sample_and_reports_overflow(self):
        r = MotorChainRobot.__new__(MotorChainRobot)
        r._state_lock = threading.Lock()
        r._recording_history = deque(maxlen=4)
        for i in range(1, 7):
            r._recording_history.append(dict(source_time_ns=i, position=np.ones(7) * i))
        r._recording_evicted_ns = 2
        self.assertTrue(r.get_recording_history(1)["overflow"])
        self.assertFalse(r.get_recording_history(2)["overflow"])
        batch = r.get_recording_history(3)
        self.assertFalse(batch["overflow"])
        self.assertEqual([s["source_time_ns"] for s in batch["samples"]], [4, 5, 6])
        batch["samples"][0]["position"][0] = -1
        self.assertEqual(r.get_recording_history(3)["samples"][0]["position"][0], 4)

    def test_slow_poll_preserves_all_buffered_native_states(self):
        class Client:
            sent = False

            def get_recording_history(self, since):
                if self.sent:
                    return {s: dict(samples=[], overflow=False) for s in since}
                self.sent = True
                # Feedback produced during one 20 ms polling interval.
                time.sleep(0.022)
                return {
                    s: dict(
                        samples=[
                            dict(
                                source_time_ns=since[s] + i * 4_000_000,
                                position=[0] * 7,
                                velocity=[0] * 7,
                                effort=[0] * 7,
                            )
                            for i in range(1, 6)
                        ],
                        overflow=False,
                    )
                    for s in since
                }

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            w = McapEpisodeWriter(tmp, "buffer", 200)
            w.bind(Client, None)
            try:
                w.start()
                time.sleep(0.04)
                w.append({}, np.zeros(14), 0, leader_action=np.zeros(14))
                p = w.commit("success")
                self.assertEqual(inspect(p)["streams"]["/left/state"]["count"], 5)
            finally:
                w.close()

    def test_fused_step_uses_one_command_and_no_extra_observation_rpc(self):
        class Robot:
            dof_queries = 0
            obs_queries = 0
            commands = 0

            def num_dofs(self):
                self.dof_queries += 1
                return 14

            def command_joint_state(self, joints):
                self.commands += 1
                return dict(
                    observation=dict(
                        joint_positions=joints,
                        joint_velocities=joints,
                        ee_pos_quat=np.zeros(14),
                        gripper_position=np.zeros(2),
                    )
                )

            def get_observations(self):
                self.obs_queries += 1
                raise AssertionError("extra RPC")

        r = Robot()
        e = RobotEnv(r, 200)
        with patch.object(e._rate, "sleep"):
            for _ in range(10):
                e.step(np.zeros(14))
        self.assertEqual((r.dof_queries, r.obs_queries, r.commands), (1, 0, 10))

    def test_deadline_skips_overrun_without_catchup_burst(self):
        with patch("gello.env.time.monotonic", return_value=0):
            r = Rate(200)
        with patch("gello.env.time.monotonic", return_value=0.017):
            r.sleep()
        self.assertEqual(r.missed_deadlines, 2)
        self.assertAlmostEqual(r.deadline, 0.02)
