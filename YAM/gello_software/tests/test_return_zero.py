import threading
import unittest

import numpy as np

from gello.safety.return_zero import (
    ARM_INDICES,
    GRIPPER_INDICES,
    ZERO_TARGET,
    arm_indices_for_size,
    gripper_indices_for_size,
    joint_trajectory,
    validate_feedback,
    zero_target_for_size,
)
from scripts.return_zero import run_return_zero


class FakeRobot:
    def __init__(self, initial, fail=False):
        self.joints = np.asarray(initial, dtype=float).copy()
        self.commands = []
        self.fail = fail

    def get_joint_state(self):
        return self.joints.copy()

    def command_joint_state(self, target):
        if self.fail:
            raise RuntimeError("command failed")
        self.joints = np.asarray(target, dtype=float).copy()
        self.commands.append(self.joints.copy())


class FakeSafety:
    def __init__(self):
        self.clients = set()
        self.activations = []
        self.holds = []
        self.lock = threading.Lock()

    def begin_session(self, session_id):
        self.session_id = session_id
        return self.status()

    def heartbeat(self, client_id, session_id):
        with self.lock:
            self.clients.add(client_id)
        return self.status()

    def status(self):
        with self.lock:
            stale = sorted({"parent", "collector"} - self.clients)
        return {"state": "safe_hold", "stale_clients": stale, "hold_confirmed": True}

    def activate(self, session_id):
        self.activations.append(session_id)
        return {"state": "active"}

    def safe_hold(self, reason):
        self.holds.append(reason)
        return {"state": "safe_hold", "hold_confirmed": True, "reason": reason}


class ReturnZeroTrajectoryTest(unittest.TestCase):
    def test_target_is_zero_with_both_grippers_open(self):
        np.testing.assert_array_equal(
            ZERO_TARGET,
            np.array([0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1]),
        )

    def test_trajectory_limits_frames_and_includes_exact_endpoint(self):
        current = np.array(
            [1, -1, 0.5, 0, 0, 0, 0, -1, 1, -0.5, 0, 0, 0, 0],
            dtype=float,
        )
        frames = joint_trajectory(current)
        values = np.vstack([current, *frames])
        delta = np.abs(np.diff(values, axis=0))
        self.assertTrue(np.all(delta[:, ARM_INDICES] <= 0.01 + 1e-12))
        self.assertTrue(np.all(delta[:, GRIPPER_INDICES] <= 0.02 + 1e-12))
        np.testing.assert_array_equal(frames[-1], ZERO_TARGET)

    def test_invalid_joint_values_are_rejected(self):
        for value in (np.zeros(13), np.full(14, np.nan)):
            with self.subTest(shape=value.shape), self.assertRaises(ValueError):
                joint_trajectory(value)

    def test_validation_uses_separate_tolerances(self):
        feedback = ZERO_TARGET.copy()
        feedback[0] = 0.029
        feedback[6] = 0.951
        self.assertTrue(validate_feedback(feedback)[0])
        feedback[7] = 0.031
        self.assertFalse(validate_feedback(feedback)[0])

    def test_four_arm_trajectory_uses_dynamic_joint_groups(self):
        target = zero_target_for_size(28)
        current = np.full(28, 0.04)
        current[gripper_indices_for_size(28)] = 0.9
        frames = joint_trajectory(current, target)
        delta = np.abs(np.diff(np.vstack([current, *frames]), axis=0))
        self.assertTrue(
            np.all(delta[:, arm_indices_for_size(28)] <= 0.01 + 1e-12)
        )
        self.assertTrue(
            np.all(delta[:, gripper_indices_for_size(28)] <= 0.02 + 1e-12)
        )
        np.testing.assert_array_equal(frames[-1], target)


class ReturnZeroControlTest(unittest.TestCase):
    def test_success_commands_exact_target_and_finishes_in_hold(self):
        robot = FakeRobot(np.full(14, 0.04))
        robot.joints[[6, 13]] = 0.96
        safety = FakeSafety()

        run_return_zero(robot, safety, "session", sleep=lambda _: None)

        np.testing.assert_array_equal(robot.commands[-1], ZERO_TARGET)
        self.assertEqual(safety.activations, ["session"])
        self.assertEqual(safety.holds[-1], "return-to-zero complete")

    def test_command_failure_still_requests_hold(self):
        robot = FakeRobot(np.zeros(14), fail=True)
        safety = FakeSafety()

        with self.assertRaisesRegex(RuntimeError, "command failed"):
            run_return_zero(robot, safety, "session", sleep=lambda _: None)

        self.assertIn("return-to-zero failed", safety.holds[-1])


if __name__ == "__main__":
    unittest.main()
