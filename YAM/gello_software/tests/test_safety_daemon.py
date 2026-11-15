import time
import unittest
from unittest import mock

from experiments.yam_safety_daemon import (
    InitializingController,
    initialize_followers,
)


class SafetyDaemonInitializationTest(unittest.TestCase):
    def test_bootstrap_heartbeat_requires_token_and_expiry_cancels(self):
        controller = InitializingController({}, "secret")
        with self.assertRaises(PermissionError):
            controller.bootstrap_heartbeat("wrong")
        controller._last_bootstrap_heartbeat = time.monotonic() - 2.0
        controller.monitor_bootstrap_lease()
        self.assertTrue(controller.cancel_event.is_set())
        self.assertIn("heartbeat expired", controller.status()["reason"])

    def test_watchdogs_are_verified_before_hardware_construction(self):
        events = []
        left = object()
        right = object()

        def verifier(channels):
            events.append(("verify", dict(channels)))
            return {"left": {1: 8000}, "right": {1: 8000}}

        def pair_factory(left_channel, right_channel):
            events.append(("construct", left_channel, right_channel))
            return left, right

        robot, readings = initialize_followers(
            "can_l",
            "can_r",
            watchdog_verifier=verifier,
            pair_factory=pair_factory,
        )

        self.assertEqual(events[0][0], "verify")
        self.assertEqual(events[1][0], "construct")
        self.assertIs(robot._robot_l, left)
        self.assertIs(robot._robot_r, right)
        self.assertEqual(readings["left"][1], 8000)

    def test_four_followers_are_all_verified_before_construction(self):
        channels = {"third": "can_3", "fourth": "can_4"}
        readings = {name: {1: 8000} for name in ("left", "right", "third", "fourth")}
        with mock.patch(
            "experiments.yam_safety_daemon.YAMRobot.create_many",
            return_value={name: object() for name in readings},
        ) as create_many:
            robot, actual = initialize_followers(
                "can_l",
                "can_r",
                additional_channels=channels,
                watchdog_verifier=lambda value: readings if set(value) == set(readings) else {},
            )
        create_many.assert_called_once()
        self.assertEqual(set(robot._robots), set(readings))
        self.assertEqual(actual, readings)


if __name__ == "__main__":
    unittest.main()
