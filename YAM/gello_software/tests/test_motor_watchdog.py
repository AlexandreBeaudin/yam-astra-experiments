import unittest

from gello.safety.watchdog import (
    WatchdogVerificationError,
    verify_motor_watchdogs,
)


class FakeInterface:
    def __init__(self, channel):
        self.channel = channel
        self.closed = False

    def close(self):
        self.closed = True


class MotorWatchdogTest(unittest.TestCase):
    def make_factory(self, created):
        def factory(*, channel, bustype, bitrate, name):
            self.assertEqual(bustype, "socketcan")
            self.assertEqual(bitrate, 1_000_000)
            interface = FakeInterface(channel)
            created.append(interface)
            return interface

        return factory

    def test_all_fourteen_motors_must_report_8000(self):
        created = []
        calls = []

        def reader(interface, motor_id, register):
            calls.append((interface.channel, motor_id, register))
            return 8000

        values = verify_motor_watchdogs(
            {"left": "can_l", "right": "can_r"},
            interface_factory=self.make_factory(created),
            register_reader=reader,
        )

        self.assertEqual(values["left"], {motor_id: 8000 for motor_id in range(1, 8)})
        self.assertEqual(len(calls), 14)
        self.assertTrue(all(interface.closed for interface in created))

    def test_reports_every_zero_wrong_and_unreadable_motor(self):
        created = []

        def reader(interface, motor_id, register):
            if interface.channel == "can_l" and motor_id == 2:
                return 0
            if interface.channel == "can_r" and motor_id == 4:
                return 7000
            if interface.channel == "can_r" and motor_id == 7:
                raise AssertionError("no response")
            return 8000

        with self.assertRaises(WatchdogVerificationError) as caught:
            verify_motor_watchdogs(
                {"left": "can_l", "right": "can_r"},
                interface_factory=self.make_factory(created),
                register_reader=reader,
            )

        message = str(caught.exception)
        self.assertIn("LEFT can_l motor 2: 0", message)
        self.assertIn("RIGHT can_r motor 4: 7000", message)
        self.assertIn("RIGHT can_r motor 7: unreadable", message)
        self.assertIn("Motor 7 is the YAM gripper drive", message)
        self.assertIn("joint-6-to-gripper CAN/power cable", message)
        self.assertTrue(all(interface.closed for interface in created))


if __name__ == "__main__":
    unittest.main()
