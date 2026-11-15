import json
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from gello.data_utils.episode_writer import JOINT_NAMES, EpisodeWriter
from gello.data_utils.replay import (
    EpisodeSummary,
    ReplayEpisode,
    discover_episodes,
    discover_tasks,
    load_episode,
    page_items,
    playback_deadlines,
    replay_start_trajectory,
    resolve_episode_choice,
    resolve_task_choice,
    run_replay,
    validate_episode_joint_limits,
)
from gello.safety.return_zero import (
    ARM_INDICES,
    GRIPPER_INDICES,
    ZERO_TARGET,
)


def write_episode(
    path: Path,
    *,
    steps: int = 4,
    complete: bool = True,
    qpos: np.ndarray | None = None,
    schema_version: str = "1.1",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    actions = np.arange(steps * 14).reshape(steps, 14) / 100
    recorded_qpos = actions.copy() if qpos is None else np.asarray(qpos, dtype=float)
    with h5py.File(path, "w") as episode:
        episode.attrs["schema_version"] = schema_version
        episode.attrs["task_name"] = path.parent.parent.name
        episode.attrs["episode_index"] = int(path.name[:4])
        episode.attrs["complete"] = complete
        episode.attrs["outcome"] = path.parent.name
        episode.attrs["control_hz"] = 30.0
        episode.attrs["num_steps"] = steps
        episode.attrs["joint_names"] = json.dumps(JOINT_NAMES)
        episode.attrs["sim"] = False
        if schema_version in {"1.2", "1.3"}:
            episode.attrs["camera_names"] = json.dumps([])
            episode.attrs["camera_metadata"] = json.dumps({})
            episode.attrs["depth_enabled"] = False
            episode.create_dataset("camera_sync_skew", data=np.zeros(steps))
        episode.create_dataset("action", data=actions)
        episode.create_dataset("timestamp", data=np.arange(steps) / 30)
        episode.create_dataset("observations/qpos", data=recorded_qpos)
        if schema_version == "1.3":
            episode.create_dataset("leader_action", data=actions)
            episode.create_dataset("observations/qvel", data=np.zeros_like(actions))
            episode.create_dataset("observations/effort", data=np.zeros_like(actions))


class ReplayDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_discovers_tasks_and_counts_both_outcomes(self):
        write_episode(self.root / "pick_cube" / "success" / "0001.hdf5")
        write_episode(self.root / "pick_cube" / "fail" / "0002.hdf5")
        write_episode(self.root / "fold" / "success" / "0003.hdf5")
        write_episode(self.root / "pick_cube" / ".in_progress" / "0004-x.hdf5")

        tasks = discover_tasks(self.root)

        self.assertEqual([task.name for task in tasks], ["fold", "pick_cube"])
        pick = tasks[1]
        self.assertEqual((pick.success_count, pick.fail_count, pick.latest_episode), (1, 1, 2))

    def test_task_choice_accepts_list_index_or_exact_name(self):
        write_episode(self.root / "a" / "success" / "0001.hdf5")
        write_episode(self.root / "b" / "success" / "0001.hdf5")
        tasks = discover_tasks(self.root)

        self.assertEqual(resolve_task_choice(tasks, "2").name, "b")
        self.assertEqual(resolve_task_choice(tasks, "a").name, "a")

    def test_episode_list_is_newest_first_and_combines_outcomes(self):
        write_episode(self.root / "task" / "success" / "0001.hdf5", steps=2)
        write_episode(self.root / "task" / "fail" / "0004.hdf5", steps=3)

        episodes = discover_episodes(self.root, "task")

        self.assertEqual([item.episode_index for item in episodes], [4, 1])
        self.assertEqual([item.outcome for item in episodes], ["fail", "success"])
        self.assertAlmostEqual(episodes[0].duration, 2 / 30)

    def test_episode_choice_uses_row_index_or_zero_padded_episode(self):
        for index in (1, 7, 12):
            write_episode(self.root / "task" / "success" / f"{index:04d}.hdf5")
        episodes = discover_episodes(self.root, "task")

        self.assertEqual(resolve_episode_choice(episodes, "1").episode_index, 12)
        self.assertEqual(resolve_episode_choice(episodes, "0007").episode_index, 7)

    def test_page_items_has_twenty_rows_and_rejects_bad_page(self):
        values = list(range(45))

        rows, page, pages = page_items(values, 2)

        self.assertEqual(rows, list(range(20, 40)))
        self.assertEqual((page, pages), (2, 3))
        with self.assertRaises(ValueError):
            page_items(values, 4)


class ReplayDataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "task" / "success" / "0001.hdf5"

    def tearDown(self):
        self.temp.cleanup()

    def test_loads_complete_actions_and_normalizes_timestamps(self):
        qpos = np.arange(4 * 14).reshape(4, 14) / 100
        qpos[0, 3] = 0.123
        write_episode(self.path, steps=4, qpos=qpos)
        with h5py.File(self.path, "r+") as episode:
            episode["timestamp"][:] += 3.0

        replay = load_episode(self.path)

        self.assertEqual(replay.actions.shape, (4, 14))
        np.testing.assert_array_equal(replay.qpos, qpos)
        np.testing.assert_array_equal(replay.start_qpos, qpos[0])
        np.testing.assert_allclose(replay.timestamps, [0, 1 / 30, 2 / 30, 3 / 30])

    def test_replay_accepts_schema_1_1_1_2_and_1_3(self):
        for schema_version in ("1.1", "1.2", "1.3"):
            with self.subTest(schema_version=schema_version):
                write_episode(self.path, schema_version=schema_version)
                replay = load_episode(self.path)
                self.assertEqual(replay.actions.shape, (4, 14))

    def test_loads_schema_1_4_four_arm_episode(self):
        writer = EpisodeWriter(
            self.root,
            "task",
            30,
            arm_names=("left", "right", "third", "fourth"),
        )
        writer.start()
        values = np.zeros(28)
        values[[6, 13, 20, 27]] = 0.5
        observation = {
            "joint_positions": values,
            "joint_velocities": np.zeros(28),
            "joint_efforts": np.zeros(28),
        }
        writer.append(observation, values, timestamp=0.0)
        path = writer.commit("success")

        replay = load_episode(path)

        self.assertEqual(replay.actions.shape, (1, 28))
        limits = np.column_stack((np.full(28, -1.0), np.full(28, 1.0)))
        self.assertEqual(
            validate_episode_joint_limits(replay, {"joint_limits": limits}).shape,
            (28, 2),
        )

    def test_rejects_incomplete_shape_nonfinite_time_and_unsafe_actions(self):
        cases = (
            "incomplete",
            "shape",
            "nan",
            "time",
            "gripper",
            "jump",
            "missing_qpos",
            "qpos_shape",
            "qpos_nan",
        )
        for case in cases:
            with self.subTest(case=case):
                path = self.root / case / "success" / "0001.hdf5"
                write_episode(path, complete=case != "incomplete")
                with h5py.File(path, "r+") as episode:
                    if case == "shape":
                        del episode["action"]
                        episode.create_dataset("action", data=np.zeros((4, 13)))
                    elif case == "nan":
                        episode["action"][1, 2] = np.nan
                    elif case == "time":
                        episode["timestamp"][2] = episode["timestamp"][1]
                    elif case == "gripper":
                        episode["action"][1, 6] = 1.1
                    elif case == "jump":
                        episode["action"][1, 0] = episode["action"][0, 0] + 0.21
                    elif case == "missing_qpos":
                        del episode["observations/qpos"]
                    elif case == "qpos_shape":
                        del episode["observations/qpos"]
                        episode.create_dataset(
                            "observations/qpos", data=np.zeros((4, 13))
                        )
                    elif case == "qpos_nan":
                        episode["observations/qpos"][0, 2] = np.nan
                with self.assertRaises(ValueError):
                    load_episode(path)

    def test_playback_speed_is_limited_and_scales_time(self):
        timestamps = np.array([0.0, 0.1, 0.2])

        np.testing.assert_allclose(playback_deadlines(timestamps, 0.5), [0.0, 0.2, 0.4])
        for speed in (0.09, 1.01):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                playback_deadlines(timestamps, speed)

    def test_start_trajectory_uses_slow_arm_and_gripper_limits(self):
        current = np.zeros(14)
        target = np.ones(14)

        frames = replay_start_trajectory(current, target)
        values = np.vstack([current, *frames])
        delta = np.abs(np.diff(values, axis=0))

        self.assertTrue(np.all(delta[:, ARM_INDICES] <= 0.10 / 30 + 1e-12))
        self.assertTrue(np.all(delta[:, GRIPPER_INDICES] <= 0.5 / 30 + 1e-12))
        np.testing.assert_array_equal(frames[-1], target)

    def test_rejects_initial_action_jump_and_too_fast_recorded_timestamps(self):
        write_episode(self.path)
        with h5py.File(self.path, "r+") as episode:
            episode["observations/qpos"][0, 0] = -0.20
            episode["action"][0, 0] = 0.01
        with self.assertRaisesRegex(ValueError, "initial arm action"):
            load_episode(self.path)

        write_episode(self.path)
        with h5py.File(self.path, "r+") as episode:
            episode["timestamp"][:] = np.arange(4) * 0.01
        with self.assertRaisesRegex(ValueError, "control_hz"):
            load_episode(self.path)

    def test_rejects_mismatched_episode_metadata(self):
        attributes = (
            ("schema_version", "1.0"),
            ("task_name", "wrong"),
            ("episode_index", 9),
            ("outcome", "fail"),
            ("num_steps", 99),
            ("joint_names", json.dumps(list(reversed(JOINT_NAMES)))),
            ("sim", True),
        )
        for name, value in attributes:
            with self.subTest(name=name):
                write_episode(self.path)
                with h5py.File(self.path, "r+") as episode:
                    episode.attrs[name] = value
                with self.assertRaisesRegex(ValueError, name):
                    load_episode(self.path)

    def test_strict_metadata_rejects_lossy_integer_and_non_boolean_sim_values(self):
        attributes = (
            ("episode_index", 1.9),
            ("num_steps", 4.9),
            ("sim", 0),
        )
        for name, value in attributes:
            with self.subTest(name=name):
                write_episode(self.path)
                with h5py.File(self.path, "r+") as episode:
                    episode.attrs[name] = value
                with self.assertRaisesRegex(ValueError, name):
                    load_episode(self.path)

    def test_joint_limit_validation_requires_new_daemon_and_checks_all_samples(self):
        actions = np.zeros((2, 14))
        qpos = np.zeros((2, 14))
        episode = fake_replay(actions, np.array([0.0, 0.1]), qpos=qpos)
        limits = np.column_stack((np.full(14, -1.0), np.full(14, 1.0)))

        validate_episode_joint_limits(episode, {"joint_limits": limits.tolist()})
        with self.assertRaisesRegex(RuntimeError, "restart.*safety daemon"):
            validate_episode_joint_limits(episode, {})

        episode.actions[1, 9] = 1.01
        with self.assertRaisesRegex(ValueError, "RIGHT joint 3"):
            validate_episode_joint_limits(episode, {"joint_limits": limits.tolist()})


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeRobot:
    def __init__(self, initial=None, fail_after=None):
        self.joints = np.zeros(14) if initial is None else np.asarray(initial, dtype=float)
        self.commands = []
        self.fail_after = fail_after

    def get_joint_state(self):
        return self.joints.copy()

    def command_joint_state(self, target):
        if self.fail_after is not None and len(self.commands) >= self.fail_after:
            raise RuntimeError("robot command failed")
        self.joints = np.asarray(target, dtype=float).copy()
        self.commands.append(self.joints.copy())


class FakeSafety:
    def __init__(self):
        self.activations = []
        self.holds = []

    def begin_session(self, session_id):
        self.session_id = session_id
        return self.status()

    def heartbeat(self, _client_id, _session_id):
        return self.status()

    def status(self):
        return {
            "state": "safe_hold",
            "stale_clients": [],
            "hold_confirmed": True,
            "joint_limits": np.column_stack(
                (np.full(14, -10.0), np.full(14, 10.0))
            ).tolist(),
        }

    def activate(self, session_id):
        self.activations.append(session_id)
        return {"state": "active"}

    def safe_hold(self, reason):
        self.holds.append(reason)
        return {"state": "safe_hold", "hold_confirmed": True, "reason": reason}


def fake_replay(actions, timestamps, *, start_qpos=None, qpos=None):
    action_values = np.asarray(actions, dtype=float)
    summary = EpisodeSummary(
        task_name="task",
        episode_index=1,
        outcome="success",
        path=Path("success/0001.hdf5"),
        num_steps=len(actions),
        duration=float(timestamps[-1]),
    )
    recorded_qpos = (
        np.asarray(qpos, dtype=float)
        if qpos is not None
        else action_values.copy()
    )
    if start_qpos is not None:
        recorded_qpos[0] = np.asarray(start_qpos, dtype=float)
    return ReplayEpisode(
        summary,
        action_values,
        np.asarray(timestamps),
        recorded_qpos,
    )


class ReplayControlTest(unittest.TestCase):
    def test_tracking_guard_ignores_gripper_contact_error(self):
        actions = np.zeros((8, 14))
        qpos = np.zeros((8, 14))
        qpos[:, GRIPPER_INDICES] = 0.9
        episode = fake_replay(actions, np.arange(8) * 0.1, qpos=qpos)
        clock = FakeClock()

        outcome = run_replay(
            FakeRobot(),
            FakeSafety(),
            "session",
            episode,
            speed=1.0,
            choose_after=lambda: "hold",
            clock=clock,
            sleep=clock.sleep,
        )

        self.assertEqual(outcome, "hold")

    def test_transient_tracking_error_recovers_but_five_frames_enter_safe_hold(self):
        class TrackingRobot(FakeRobot):
            def __init__(self, persistent):
                super().__init__()
                self.persistent = persistent
                self.reads = 0

            def get_joint_state(self):
                self.reads += 1
                value = super().get_joint_state()
                # Start verification consumes several reads. Apply the error only
                # while the formal replay loop is checking recorded feedback.
                if self.reads >= 5 and (self.persistent or self.reads < 9):
                    value[0] += 0.30
                return value

        actions = np.zeros((8, 14))
        episode = fake_replay(actions, np.arange(8) * 0.1)
        clock = FakeClock()
        self.assertEqual(
            run_replay(
                TrackingRobot(False), FakeSafety(), "session", episode,
                speed=1.0, choose_after=lambda: "hold", clock=clock,
                sleep=clock.sleep,
            ),
            "hold",
        )

        safety = FakeSafety()
        persistent_clock = FakeClock()
        with self.assertRaisesRegex(
            RuntimeError, "tracking error.*frame 5.*LEFT joint 1"
        ):
            run_replay(
                TrackingRobot(True), safety, "session", episode,
                speed=1.0, choose_after=lambda: "hold", clock=persistent_clock,
                sleep=persistent_clock.sleep,
            )
        self.assertIn("replay failed", safety.holds[-1])

    def test_excessive_schedule_lateness_enters_safe_hold_without_catch_up(self):
        class SlowCommandRobot(FakeRobot):
            def __init__(self, clock):
                super().__init__()
                self.clock = clock

            def command_joint_state(self, target):
                super().command_joint_state(target)
                self.clock.value += 0.25

        episode = fake_replay(np.zeros((4, 14)), np.arange(4) * 0.1)
        safety = FakeSafety()
        clock = FakeClock()
        with self.assertRaisesRegex(RuntimeError, "schedule late"):
            run_replay(
                SlowCommandRobot(clock), safety, "session", episode,
                speed=1.0, choose_after=lambda: "hold", clock=clock,
                sleep=clock.sleep,
            )
        self.assertIn("replay failed", safety.holds[-1])
    def test_start_pose_accepts_replay_specific_tracking_tolerance(self):
        class OffsetRobot(FakeRobot):
            def command_joint_state(self, target):
                self.commands.append(np.asarray(target, dtype=float).copy())

        actions = np.zeros((2, 14))
        actions[:, 0] = 0.10
        start_qpos = np.zeros(14)
        start_qpos[0] = 0.044
        episode = fake_replay(
            actions,
            np.array([0.0, 0.1]),
            start_qpos=start_qpos,
        )
        clock = FakeClock()

        outcome = run_replay(
            OffsetRobot(),
            FakeSafety(),
            "session",
            episode,
            speed=1.0,
            choose_after=lambda: "hold",
            clock=clock,
            sleep=clock.sleep,
        )

        self.assertEqual(outcome, "hold")

    def test_moves_to_recorded_qpos_before_first_action(self):
        actions = np.zeros((2, 14))
        actions[:, 0] = [0.20, 0.21]
        start_qpos = np.zeros(14)
        start_qpos[0] = 0.10
        episode = fake_replay(
            actions,
            np.array([0.0, 0.1]),
            start_qpos=start_qpos,
        )
        robot = FakeRobot()
        clock = FakeClock()

        run_replay(
            robot,
            FakeSafety(),
            "session",
            episode,
            speed=1.0,
            choose_after=lambda: "hold",
            clock=clock,
            sleep=clock.sleep,
        )

        first_action = next(
            index
            for index, command in enumerate(robot.commands)
            if np.array_equal(command, actions[0])
        )
        self.assertGreater(first_action, 0)
        np.testing.assert_array_equal(robot.commands[first_action - 1], start_qpos)

    def test_replays_every_action_at_scaled_timing_and_holds_before_choice(self):
        actions = np.zeros((3, 14))
        actions[:, 0] = [0.01, 0.02, 0.03]
        episode = fake_replay(actions, np.array([0.0, 0.1, 0.2]))
        robot = FakeRobot()
        safety = FakeSafety()
        clock = FakeClock()

        def choose_after():
            self.assertTrue(safety.holds)
            return "hold"

        outcome = run_replay(
            robot,
            safety,
            "session",
            episode,
            speed=0.5,
            choose_after=choose_after,
            clock=clock,
            sleep=clock.sleep,
        )

        self.assertEqual(outcome, "hold")
        for action in actions:
            self.assertTrue(any(np.array_equal(command, action) for command in robot.commands))
        np.testing.assert_array_equal(robot.commands[-1], actions[-1])
        self.assertGreaterEqual(clock.value, 0.6)

    def test_zero_choice_returns_exact_zero_and_finishes_in_hold(self):
        actions = np.full((2, 14), 0.02)
        actions[:, [6, 13]] = 0.9
        episode = fake_replay(actions, np.array([0.0, 0.01]))
        robot = FakeRobot()
        safety = FakeSafety()
        clock = FakeClock()

        outcome = run_replay(
            robot,
            safety,
            "session",
            episode,
            speed=1.0,
            choose_after=lambda: "zero",
            clock=clock,
            sleep=clock.sleep,
        )

        self.assertEqual(outcome, "zero")
        np.testing.assert_array_equal(
            robot.commands[-1],
            np.array([0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1]),
        )
        self.assertGreaterEqual(len(safety.activations), 2)
        self.assertIn("replay complete", safety.holds)
        self.assertEqual(safety.holds[-1], "replay zero complete")

    def test_zero_verification_keeps_strict_homing_tolerance(self):
        class ZeroOffsetRobot(FakeRobot):
            ignore_commands = False

            def command_joint_state(self, target):
                if self.ignore_commands:
                    self.commands.append(np.asarray(target, dtype=float).copy())
                    return
                super().command_joint_state(target)

        actions = np.zeros((2, 14))
        episode = fake_replay(actions, np.array([0.0, 0.1]))
        robot = ZeroOffsetRobot()
        clock = FakeClock()

        def choose_zero():
            robot.joints[0] = 0.051
            robot.ignore_commands = True
            return "zero"

        with self.assertRaisesRegex(
            RuntimeError,
            "zero verification timed out.*LEFT joint 1",
        ):
            run_replay(
                robot,
                FakeSafety(),
                "session",
                episode,
                speed=1.0,
                choose_after=choose_zero,
                clock=clock,
                sleep=clock.sleep,
            )

    def test_zero_verification_accepts_small_hardware_residual(self):
        class ZeroResidualRobot(FakeRobot):
            ignore_commands = False

            def command_joint_state(self, target):
                if self.ignore_commands:
                    self.commands.append(np.asarray(target, dtype=float).copy())
                    return
                super().command_joint_state(target)

        actions = np.zeros((2, 14))
        episode = fake_replay(actions, np.array([0.0, 0.1]))
        robot = ZeroResidualRobot()
        clock = FakeClock()

        def choose_zero():
            robot.joints[10] = 0.036
            robot.joints[GRIPPER_INDICES] = 1.0
            robot.ignore_commands = True
            return "zero"

        outcome = run_replay(
            robot,
            FakeSafety(),
            "session",
            episode,
            speed=1.0,
            choose_after=choose_zero,
            clock=clock,
            sleep=clock.sleep,
        )

        self.assertEqual(outcome, "zero")
        np.testing.assert_array_equal(robot.commands[-1], ZERO_TARGET)

    def test_command_failure_always_requests_safe_hold(self):
        actions = np.zeros((2, 14))
        episode = fake_replay(actions, np.array([0.0, 0.1]))
        safety = FakeSafety()
        clock = FakeClock()

        with self.assertRaisesRegex(RuntimeError, "robot command failed"):
            run_replay(
                FakeRobot(fail_after=0),
                safety,
                "session",
                episode,
                speed=1.0,
                choose_after=lambda: "hold",
                clock=clock,
                sleep=clock.sleep,
            )

        self.assertIn("replay failed", safety.holds[-1])

    def test_start_pose_must_be_verified_before_replay(self):
        class LaggingRobot(FakeRobot):
            def command_joint_state(self, target):
                self.commands.append(np.asarray(target, dtype=float).copy())

        actions = np.zeros((2, 14))
        actions[:, 0] = 0.1
        episode = fake_replay(actions, np.array([0.0, 0.1]))
        safety = FakeSafety()
        clock = FakeClock()

        with self.assertRaisesRegex(
            RuntimeError,
            "start pose verification timed out.*LEFT joint 1",
        ):
            run_replay(
                LaggingRobot(),
                safety,
                "session",
                episode,
                speed=1.0,
                choose_after=lambda: "hold",
                clock=clock,
                sleep=clock.sleep,
            )

        self.assertIn("replay failed", safety.holds[-1])


if __name__ == "__main__":
    unittest.main()
