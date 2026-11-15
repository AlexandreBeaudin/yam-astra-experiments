import queue
from types import SimpleNamespace
import unittest

import numpy as np

from gello.cameras.realsense_camera import RealSenseFrame
from gello.cameras.realsense_rig import CameraBundle, CameraMiss
from gello.data_utils.collection_session import (
    CameraCaptureError,
    run_collection_session,
)


class FakeEnv:
    def __init__(self, qpos):
        self.qpos = np.asarray(qpos, dtype=float)
        self.steps = []

    def get_obs(self):
        return {
            "joint_positions": self.qpos.copy(),
            "joint_velocities": np.zeros(14),
        }

    def step(self, action):
        self.qpos = np.asarray(action, dtype=float)
        self.steps.append(self.qpos.copy())
        return self.get_obs()


class FakeAgent:
    def __init__(self, action):
        self.action = np.asarray(action, dtype=float)

    def act(self, _obs):
        return self.action.copy()


class SequenceAgent:
    def __init__(self, actions):
        self.actions = [np.asarray(action, dtype=float) for action in actions]

    def act(self, _obs):
        if len(self.actions) > 1:
            return self.actions.pop(0).copy()
        return self.actions[0].copy()


class FakeRobot:
    def __init__(self):
        self.gravity_comp_calls = 0
        self.safe_hold_reasons = []

    def enter_gravity_comp_idle(self):
        self.gravity_comp_calls += 1

    def enter_safe_hold(self, reason="collection exit"):
        self.safe_hold_reasons.append(reason)


class FakeWriter:
    def __init__(self):
        self.next_episode_index = 1
        self.recording = False
        self.samples = []
        self.commits = []
        self.discards = 0
        self.close_incomplete_calls = 0
        self.stats = []

    def start(self):
        self.recording = True

    def append(self, obs, action, timestamp, leader_action=None, camera_sample=None):
        self.samples.append(
            (obs["joint_positions"].copy(), action.copy(), timestamp, camera_sample)
        )

    def commit(self, outcome):
        self.recording = False
        self.commits.append(outcome)
        self.next_episode_index += 1
        return f"{self.next_episode_index - 1:04d}.hdf5"

    def discard(self):
        self.recording = False
        self.discards += 1

    def close_incomplete(self):
        self.recording = False
        self.close_incomplete_calls += 1

    def update_camera_stats(self, **stats):
        self.stats.append(stats)


def camera_bundle(reference_time=10.0, frame_number=1):
    frames = {}
    for index, name in enumerate(("main", "left", "right")):
        frames[name] = RealSenseFrame(
            camera_name=name,
            serial=f"serial-{name}",
            rgb=np.full((2, 3, 3), index, dtype=np.uint8),
            depth=None,
            frame_number=frame_number,
            device_timestamp_ms=(reference_time + index * 0.005) * 1000,
            timestamp_domain="hardware_clock",
            host_timestamp=reference_time + index * 0.005,
        )
    return CameraBundle(reference_time, frames, skew_s=0.010)


class FakeCameraRig:
    def __init__(self, results, commands=None, finish_after=None):
        self.results = list(results)
        self.commands = commands
        self.finish_after = finish_after
        self.calls = 0
        self.begin_episode_calls = 0
        self.config = SimpleNamespace(
            sync=SimpleNamespace(max_consecutive_failures=10)
        )

    def begin_episode(self):
        self.begin_episode_calls += 1

    def sample(self, _reference_time):
        self.calls += 1
        result = self.results.pop(0)
        if self.commands is not None and self.calls == self.finish_after:
            self.commands.put({"type": "finish_episode"})
            self.commands.put({"type": "label", "outcome": "discard"})
            self.commands.put({"type": "exit_task"})
        return result

    def status(self):
        return {
            name: SimpleNamespace(measured_fps=30.0)
            for name in ("main", "left", "right")
        }


class CollectionSessionTest(unittest.TestCase):
    def test_sync_miss_discards_complete_timestep_but_control_continues(self):
        home = np.zeros(14)
        commands = queue.Queue()
        rig = FakeCameraRig(
            [CameraMiss("skew", "26 ms", 1), camera_bundle()],
            commands=commands,
            finish_after=2,
        )
        writer = FakeWriter()
        events = []

        run_collection_session(
            wait_for_triggers=False,
            env=FakeEnv(home),
            agent=FakeAgent(home),
            robot=FakeRobot(),
            writer=writer,
            commands=commands,
            emit=events.append,
            camera_rig=rig,
        )

        self.assertEqual(rig.begin_episode_calls, 1)
        self.assertEqual(len(writer.samples), 1)
        self.assertIsNotNone(writer.samples[0][3])
        self.assertTrue(any(event["type"] == "camera_sync_miss" for event in events))
        self.assertEqual(writer.stats[-1]["total_misses"], 1)

    def test_tenth_consecutive_camera_miss_enters_hold_and_leaves_incomplete(self):
        home = np.zeros(14)
        writer = FakeWriter()
        robot = FakeRobot()
        rig = FakeCameraRig(
            [CameraMiss("no_new_frame", "waiting", index) for index in range(1, 11)]
        )
        events = []

        with self.assertRaisesRegex(CameraCaptureError, "10 consecutive"):
            run_collection_session(
                wait_for_triggers=False,
                env=FakeEnv(home),
                agent=FakeAgent(home),
                robot=robot,
                writer=writer,
                commands=queue.Queue(),
                emit=events.append,
                camera_rig=rig,
            )

        self.assertEqual(writer.samples, [])
        self.assertEqual(writer.close_incomplete_calls, 1)
        self.assertTrue(robot.safe_hold_reasons)
        self.assertEqual(events[-1]["type"], "camera_fault")

    def test_saved_action_jump_after_camera_miss_terminates_episode(self):
        home = np.zeros(14)
        large = home.copy()
        large[0] = 0.3
        agent = SequenceAgent([home, home, home, home, large, large])
        writer = FakeWriter()
        robot = FakeRobot()
        rig = FakeCameraRig(
            [
                camera_bundle(frame_number=1),
                CameraMiss("skew", "26 ms", 1),
                camera_bundle(reference_time=10.1, frame_number=2),
            ]
        )

        with self.assertRaisesRegex(CameraCaptureError, "saved action jump"):
            run_collection_session(
                wait_for_triggers=False,
                env=FakeEnv(home),
                agent=agent,
                robot=robot,
                writer=writer,
                commands=queue.Queue(),
                emit=lambda _event: None,
                camera_rig=rig,
            )

        self.assertEqual(len(writer.samples), 1)
        self.assertEqual(writer.close_incomplete_calls, 1)
        self.assertTrue(robot.safe_hold_reasons)
    def test_save_error_stays_in_review_and_allows_retry(self):
        class RetryWriter(FakeWriter):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            def commit(self, outcome):
                self.attempts += 1
                if self.attempts == 1:
                    raise OSError("temporary save failure")
                return super().commit(outcome)

        home = np.zeros(14)
        commands = queue.Queue()
        for command in (
            {"type": "finish_episode"},
            {"type": "label", "outcome": "success"},
            {"type": "label", "outcome": "success"},
            {"type": "exit_task"},
        ):
            commands.put(command)
        writer = RetryWriter()
        events = []

        run_collection_session(
            wait_for_triggers=False,
            env=FakeEnv(home),
            agent=FakeAgent(home),
            robot=FakeRobot(),
            writer=writer,
            commands=commands,
            emit=events.append,
        )

        self.assertEqual(writer.attempts, 2)
        self.assertTrue(any(event["type"] == "save_failed" for event in events))
        self.assertEqual(writer.commits, ["success"])

    def test_records_then_syncs_next_episode_without_saving_sync_motion(self):
        home = np.zeros(14)
        commands = queue.Queue()
        for command in (
            {"type": "finish_episode"},
            {"type": "label", "outcome": "success"},
            {"type": "start_episode"},
            {"type": "finish_episode"},
            {"type": "label", "outcome": "discard"},
            {"type": "exit_task"},
        ):
            commands.put(command)
        env = FakeEnv(home)
        agent = FakeAgent(home)
        robot = FakeRobot()
        writer = FakeWriter()
        events = []
        run_collection_session(
            wait_for_triggers=False,
            env=env,
            agent=agent,
            robot=robot,
            writer=writer,
            commands=commands,
            emit=events.append,
        )

        self.assertEqual(writer.commits, ["success"])
        self.assertEqual(writer.discards, 1)
        self.assertEqual(robot.gravity_comp_calls, 0)
        self.assertEqual(len(robot.safe_hold_reasons), 3)
        self.assertEqual(len(writer.samples), 2)
        self.assertGreaterEqual(len(env.steps), 6)
        self.assertEqual(events[-1]["type"], "safe_hold_active")

    def test_misalignment_is_synchronized_without_manual_alignment(self):
        home = np.zeros(14)
        leader = home.copy()
        leader[0] = 0.3
        commands = queue.Queue()
        commands.put({"type": "start_episode"})
        commands.put({"type": "finish_episode"})
        commands.put({"type": "label", "outcome": "discard"})
        commands.put({"type": "exit_task"})
        writer = FakeWriter()
        events = []

        run_collection_session(
            wait_for_triggers=False,
            env=FakeEnv(home),
            agent=FakeAgent(leader),
            robot=FakeRobot(),
            writer=writer,
            commands=commands,
            emit=events.append,
            auto_start=False,
        )

        self.assertFalse(writer.recording)
        # Synchronization motion is excluded; only the first recording tick is saved.
        self.assertEqual(len(writer.samples), 1)
        self.assertGreater(len(events), len(writer.samples))
        self.assertGreater(len(events), 0)
        self.assertTrue(any(event["type"] == "sync_started" for event in events))

    def test_exit_while_recording_discards_without_automatic_motion(self):
        home = np.zeros(14)
        commands = queue.Queue()
        commands.put({"type": "exit_task"})
        writer = FakeWriter()
        env = FakeEnv(home)

        run_collection_session(
            wait_for_triggers=False,
            env=env,
            agent=FakeAgent(home),
            robot=FakeRobot(),
            writer=writer,
            commands=commands,
            emit=lambda _event: None,
        )

        self.assertEqual(len(env.steps), 0)
        self.assertEqual(writer.discards, 0)


if __name__ == "__main__":
    unittest.main()
