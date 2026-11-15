import unittest
from threading import Lock
from unittest import mock

from gello.dynamixel.driver import COMM_SUCCESS, DynamixelDriver


class PacketHandler:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def write1ByteTxRx(self, *_args):
        self.calls += 1
        return next(self.responses)


def make_driver(responses):
    driver = DynamixelDriver.__new__(DynamixelDriver)
    driver._ids = [1]
    driver._is_fake = False
    driver._torque_enabled = False
    driver._max_retries = 3
    driver._lock = Lock()
    driver._packetHandler = PacketHandler(responses)
    driver._portHandler = object()
    return driver


class TorqueModeRetryTest(unittest.TestCase):
    def test_retries_a_transient_communication_failure(self):
        driver = make_driver([(-3001, 0), (COMM_SUCCESS, 0)])

        with mock.patch("gello.dynamixel.driver.time.sleep") as sleep:
            driver.set_torque_mode(False)

        self.assertEqual(driver._packetHandler.calls, 2)
        sleep.assert_called_once()
        self.assertFalse(driver.torque_enabled())

    def test_raises_after_bounded_retries(self):
        driver = make_driver([(-3001, 0)] * 3)

        with (
            mock.patch("gello.dynamixel.driver.time.sleep"),
            self.assertRaisesRegex(RuntimeError, "after 3 attempts.*comm=-3001"),
        ):
            driver.set_torque_mode(False)

        self.assertEqual(driver._packetHandler.calls, 3)


if __name__ == "__main__":
    unittest.main()
