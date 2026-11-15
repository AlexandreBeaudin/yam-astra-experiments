import unittest

import numpy as np

from gello.safety.controller import (
    SafetyController,
    SafetyState,
)


class FakeClock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


class FakeRobot:
    def __init__(self):
        self.joints = np.arange(14, dtype=float)
        self.joints[[6, 13]] = 0.5
        self.commands = []
        self.gravity_calls = 0
        self.close_args = []
        self.command_error = None
        self.health = {"ok": True, "sides": {}}

    def num_dofs(self):
        return 14

    def get_joint_state(self):
        return self.joints.copy()

    def command_joint_state(self, joint_state):
        if self.command_error is not None:
            raise self.command_error
        self.commands.append(np.asarray(joint_state, dtype=float).copy())

    def get_observations(self):
        return {"joint_positions": self.joints.copy()}

    def enter_gravity_comp_idle(self):
        self.gravity_calls += 1

    def close(self, disable_motors=False):
        self.close_args.append(disable_motors)

    def get_health_status(self):
        return self.health

    def get_joint_limits(self):
        return np.column_stack((np.full(14, -2.0), np.full(14, 2.0)))


class SafetyControllerTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.robot = FakeRobot()
        self.controller = SafetyController(
            self.robot,
            lease_timeout_s=1.0,
            clock=self.clock,
        )

    def activate(self):
        self.controller.begin_session("session-a")
        self.controller.heartbeat("parent", "session-a")
        self.controller.heartbeat("collector", "session-a")
        self.controller.activate("session-a")

    def test_starts_in_safe_hold_at_actual_feedback(self):
        self.assertEqual(self.controller.state, SafetyState.SAFE_HOLD)
        np.testing.assert_array_equal(self.robot.commands[-1], self.robot.joints)

    def test_status_publishes_hardware_joint_limits(self):
        self.assertEqual(
            self.controller.status()["joint_limits"],
            self.robot.get_joint_limits().tolist(),
        )

    def test_metadata_cannot_override_hardware_joint_limits(self):
        controller = SafetyController(
            self.robot,
            lease_timeout_s=1.0,
            clock=self.clock,
            metadata={"joint_limits": [[-99.0, 99.0]] * 14},
        )

        self.assertEqual(
            controller.status()["joint_limits"],
            self.robot.get_joint_limits().tolist(),
        )

    def test_activation_requires_both_fresh_leases(self):
        self.controller.begin_session("session-a")
        self.controller.heartbeat("parent", "session-a")

        with self.assertRaisesRegex(RuntimeError, "collector"):
            self.controller.activate("session-a")

        self.assertEqual(self.controller.state, SafetyState.SAFE_HOLD)

    def test_expired_lease_latches_safe_hold(self):
        self.activate()
        self.robot.joints += 0.5
        self.clock.now += 1.01

        changed = self.controller.check_leases()

        self.assertTrue(changed)
        self.assertEqual(self.controller.state, SafetyState.SAFE_HOLD)
        np.testing.assert_array_equal(self.robot.commands[-1], self.robot.joints)
        self.controller.heartbeat("parent", "session-a")
        self.controller.heartbeat("collector", "session-a")
        self.assertEqual(self.controller.state, SafetyState.SAFE_HOLD)
        with self.assertRaisesRegex(
            RuntimeError, "safe_hold: heartbeat expired: collector, parent"
        ):
            self.controller.command_joint_state(np.zeros(14))

    def test_command_exception_enters_safe_hold(self):
        self.activate()
        self.robot.joints += 1.0
        calls = 0

        def fail_once(_joint_state):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("control failure")
            self.robot.commands.append(self.robot.joints.copy())

        self.robot.command_joint_state = fail_once

        with self.assertRaisesRegex(RuntimeError, "control failure"):
            self.controller.command_joint_state(np.zeros(14))

        self.assertEqual(self.controller.state, SafetyState.SAFE_HOLD)
        self.assertIn("control failure", self.controller.reason)

    def test_gravity_requires_safe_hold_and_does_not_disable(self):
        self.activate()
        with self.assertRaisesRegex(RuntimeError, "SAFE_HOLD"):
            self.controller.enter_gravity("session-a")

        self.controller.enter_safe_hold("operator request")
        self.controller.enter_gravity("session-a")

        self.assertEqual(self.controller.state, SafetyState.GRAVITY)
        self.assertEqual(self.robot.gravity_calls, 1)
        self.assertEqual(self.robot.close_args, [])

    def test_only_exact_explicit_off_disables(self):
        self.controller.begin_session("session-a")
        with self.assertRaisesRegex(ValueError, "off"):
            self.controller.disarm("session-a", "yes")
        self.assertEqual(self.robot.close_args, [])

        self.controller.disarm("session-a", "off")

        self.assertEqual(self.controller.state, SafetyState.DISARMED)
        self.assertEqual(self.robot.close_args, [True])

    def test_single_jump_is_limited_and_returns_applied_target(self):
        self.activate()
        target = self.robot.joints.copy()
        target[0] += 1.0
        target[6] = 0.7

        result = self.controller.command_joint_state(target)

        self.assertTrue(result["limited"])
        self.assertAlmostEqual(result["applied_joint_state"][0], 0.2)
        self.assertAlmostEqual(result["applied_joint_state"][6], 0.7)
        np.testing.assert_array_equal(self.robot.commands[-1], result["applied_joint_state"])

    def test_gripper_full_range_change_is_not_rate_limited(self):
        self.activate()
        target = self.robot.joints.copy()
        target[[6, 13]] = [0.0, 1.0]

        result = self.controller.command_joint_state(target)

        self.assertFalse(result["limited"])
        np.testing.assert_array_equal(
            result["applied_joint_state"][[6, 13]], [0.0, 1.0]
        )

    def test_repeated_gripper_jumps_never_latch_hold(self):
        self.activate()
        for value in (0.0, 1.0, 0.0, 1.0):
            target = self.robot.joints.copy()
            target[[6, 13]] = value
            result = self.controller.command_joint_state(target)
            self.assertFalse(result["limited"])

        self.assertEqual(self.controller.state, SafetyState.ACTIVE)

    def test_out_of_range_grippers_clamp_without_counting_violation(self):
        self.activate()
        target = self.robot.joints.copy()
        target[[6, 13]] = [-5.0, 8.0]

        result = self.controller.command_joint_state(target)

        np.testing.assert_array_equal(
            result["applied_joint_state"][[6, 13]], [0.0, 1.0]
        )
        self.assertEqual(result["consecutive_limited"], 0)

    def test_sixth_consecutive_limited_command_latches_hold_without_sending_it(self):
        self.activate()
        target = self.robot.joints + 10.0
        for expected_count in range(1, 6):
            result = self.controller.command_joint_state(target)
            self.assertTrue(result["limited"])
            self.assertEqual(result["consecutive_limited"], expected_count)
            self.assertEqual(self.controller.state, SafetyState.ACTIVE)
        calls_before = len(self.robot.commands)

        with self.assertRaisesRegex(RuntimeError, "consecutive"):
            self.controller.command_joint_state(target)

        self.assertEqual(self.controller.state, SafetyState.SAFE_HOLD)
        # Only the hold command is sent on the sixth violation.
        self.assertEqual(len(self.robot.commands), calls_before + 1)

    def test_non_finite_or_wrong_shape_immediately_latches_hold(self):
        self.activate()
        for target in (np.full(14, np.nan), np.zeros(13)):
            with self.subTest(shape=target.shape):
                with self.assertRaises(ValueError):
                    self.controller.command_joint_state(target)
                self.assertEqual(self.controller.state, SafetyState.SAFE_HOLD)
                self.activate()

    def test_live_active_session_cannot_be_taken_over(self):
        self.activate()
        with self.assertRaisesRegex(RuntimeError, "already active"):
            self.controller.begin_session("session-b")
        self.assertEqual(self.controller.status()["session_id"], "session-a")

    def test_unhealthy_hardware_latches_fault(self):
        self.activate()
        self.robot.health = {
            "ok": False,
            "reason": "LEFT control thread stopped",
            "sides": {"left": {"control_thread_alive": False}},
        }

        self.assertTrue(self.controller.check_hardware_health())
        status = self.controller.status()
        self.assertEqual(status["state"], "fault")
        self.assertFalse(status["hold_confirmed"])
        self.assertIn("control thread", status["reason"])

    def test_explicit_off_is_available_from_fault_without_prior_session(self):
        self.robot.health = {"ok": False, "reason": "CAN stopped", "sides": {}}
        self.controller.check_hardware_health()

        self.controller.disarm("recovery-cli", "off")

        self.assertEqual(self.controller.state, SafetyState.DISARMED)
        self.assertEqual(self.robot.close_args, [True])


if __name__ == "__main__":
    unittest.main()
