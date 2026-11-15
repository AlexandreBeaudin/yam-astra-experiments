import queue
import unittest

import numpy as np

from gello.data_utils.leader_sync import (
    sync_step,
    synchronize_followers,
)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeEnv:
    def __init__(self, qpos, clock):
        self.qpos = np.asarray(qpos, dtype=float)
        self.clock = clock
        self.steps = []

    def get_obs(self):
        return {"joint_positions": self.qpos.copy()}

    def step(self, action):
        self.qpos = np.asarray(action, dtype=float)
        self.steps.append(self.qpos.copy())
        self.clock.advance(1.0 / 30.0)
        return self.get_obs()


class StationaryFeedbackEnv(FakeEnv):
    """Record commands while simulating feedback that has not moved yet."""

    def step(self, action):
        self.steps.append(np.asarray(action, dtype=float).copy())
        self.clock.advance(1.0 / 30.0)
        return self.get_obs()


class SequenceAgent:
    def __init__(self, actions):
        self.actions = [np.asarray(action, dtype=float) for action in actions]
        self.index = 0

    def act(self, _observation):
        action = self.actions[min(self.index, len(self.actions) - 1)]
        self.index += 1
        return action.copy()


class LeaderSyncTest(unittest.TestCase):
    def test_step_obeys_arm_and_gripper_speed(self):
        current = np.zeros(14)
        target = np.ones(14)
        result = sync_step(current, target)
        arm = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12]
        np.testing.assert_allclose(result[arm], 0.10 / 30)
        np.testing.assert_allclose(result[[6, 13]], 0.5 / 30)

    def test_grippers_are_clamped(self):
        result = sync_step(
            np.zeros(14), np.array([0] * 6 + [5] + [0] * 6 + [-2])
        )
        self.assertTrue(np.all((result[[6, 13]] >= 0) & (result[[6, 13]] <= 1)))

    def test_rejects_invalid_or_nonfinite_vectors(self):
        with self.assertRaises(ValueError):
            sync_step(np.zeros(13), np.zeros(14))
        target = np.zeros(14)
        target[2] = np.nan
        with self.assertRaises(ValueError):
            sync_step(np.zeros(14), target)

    def test_requires_three_aligned_samples(self):
        clock = FakeClock()
        env = FakeEnv(np.zeros(14), clock)
        events = []

        result = synchronize_followers(
            env,
            SequenceAgent([np.zeros(14)]),
            queue.Queue(),
            events.append,
            clock=clock,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(env.steps), 2)
        self.assertEqual(events[-1]["type"], "sync_completed")

    def test_retargets_from_fresh_leader_feedback(self):
        clock = FakeClock()
        env = FakeEnv(np.zeros(14), clock)
        first = np.zeros(14)
        first[0] = 0.01
        moved = np.zeros(14)
        moved[0] = 0.20

        result = synchronize_followers(
            env,
            SequenceAgent([first, moved]),
            queue.Queue(),
            lambda _event: None,
            clock=clock,
        )

        self.assertTrue(result["ok"])
        self.assertLessEqual(abs(env.qpos[0] - moved[0]), 0.08)
        increments = np.diff([0.0] + [step[0] for step in env.steps])
        self.assertTrue(np.all(np.abs(increments) <= 0.20 / 30 + 1e-12))

    def test_commands_accumulate_while_feedback_is_stationary(self):
        clock = FakeClock()
        env = StationaryFeedbackEnv(np.zeros(14), clock)
        target = np.zeros(14)
        target[0] = 1.0

        synchronize_followers(
            env,
            SequenceAgent([target]),
            queue.Queue(),
            lambda _event: None,
            timeout_s=0.1,
            clock=clock,
        )

        expected_step = 0.20 / 30.0
        np.testing.assert_allclose(
            [command[0] for command in env.steps[:3]],
            [expected_step, 2 * expected_step, 3 * expected_step],
        )

    def test_timeout_reports_failure(self):
        clock = FakeClock()
        env = FakeEnv(np.zeros(14), clock)
        target = np.zeros(14)
        target[0] = 2.0
        events = []

        result = synchronize_followers(
            env,
            SequenceAgent([target]),
            queue.Queue(),
            events.append,
            timeout_s=0.1,
            clock=clock,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["exit"])
        self.assertEqual(events[-1]["type"], "sync_failed")

    def test_progress_identifies_worst_arm_joint(self):
        clock = FakeClock()
        env = StationaryFeedbackEnv(np.zeros(14), clock)
        target = np.zeros(14)
        target[8] = 0.4
        target[2] = 0.2
        events = []

        synchronize_followers(
            env,
            SequenceAgent([target]),
            queue.Queue(),
            events.append,
            timeout_s=0.1,
            clock=clock,
        )

        progress = next(event for event in events if event["type"] == "sync_progress")
        self.assertEqual(progress["worst_side"], "RIGHT")
        self.assertEqual(progress["worst_joint"], 2)
        self.assertEqual(progress["worst_target"], 0.4)
        self.assertEqual(progress["worst_measured"], 0.0)

    def test_stationary_feedback_uses_overall_timeout(self):
        clock = FakeClock()
        env = StationaryFeedbackEnv(np.zeros(14), clock)
        target = np.zeros(14)
        target[0] = 1.0
        events = []

        result = synchronize_followers(
            env,
            SequenceAgent([target]),
            queue.Queue(),
            events.append,
            timeout_s=3.0,
            clock=clock,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(events[-1]["type"], "sync_failed")
        self.assertEqual(events[-1]["message"], "synchronization timed out")

    def test_accepts_observed_hardware_tracking_error(self):
        clock = FakeClock()
        env = StationaryFeedbackEnv(np.zeros(14), clock)
        target = np.zeros(14)
        target[10] = -0.067

        result = synchronize_followers(
            env,
            SequenceAgent([target]),
            queue.Queue(),
            lambda _event: None,
            timeout_s=0.1,
            clock=clock,
        )

        self.assertTrue(result["ok"])

    def test_queued_exit_aborts_before_motion(self):
        clock = FakeClock()
        env = FakeEnv(np.zeros(14), clock)
        commands = queue.Queue()
        commands.put({"type": "exit_task"})

        result = synchronize_followers(
            env,
            SequenceAgent([np.ones(14)]),
            commands,
            lambda _event: None,
            clock=clock,
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["exit"])
        self.assertEqual(env.steps, [])


if __name__ == "__main__":
    unittest.main()
