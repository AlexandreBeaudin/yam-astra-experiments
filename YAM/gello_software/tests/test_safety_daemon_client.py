import unittest

from gello.safety.daemon_client import (
    SAFETY_DAEMON_PROTOCOL_VERSION,
    validate_daemon_status,
)

CHANNELS = {"left": "can_follower_l", "right": "can_follower_r"}


def healthy_status(**updates):
    status = {
        "state": "safe_hold",
        "protocol_version": SAFETY_DAEMON_PROTOCOL_VERSION,
        "channels": CHANNELS.copy(),
        "hardware_port": 6001,
        "watchdogs": {
            "left": {i: 8000 for i in range(1, 8)},
            "right": {i: 8000 for i in range(1, 8)},
        },
        "stale_clients": ["collector", "parent"],
    }
    status.update(updates)
    return status


class DaemonStatusTest(unittest.TestCase):
    def test_healthy_hold_is_accepted(self):
        validate_daemon_status(healthy_status(), CHANNELS, 6001)

    def test_live_active_session_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "ACTIVE"):
            validate_daemon_status(
                healthy_status(state="active", stale_clients=[]), CHANNELS, 6001
            )

    def test_fault_disarmed_mismatch_and_watchdog_errors_are_rejected(self):
        cases = (
            (healthy_status(state="fault", reason="CAN stopped"), "FAULT"),
            (healthy_status(state="disarmed"), "DISARMED"),
            (healthy_status(protocol_version=None), "protocol"),
            (healthy_status(channels={"left": "wrong"}), "channels"),
            (healthy_status(hardware_port=9999), "port"),
            (healthy_status(watchdogs={}), "watchdog"),
        )
        for status, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                RuntimeError, message
            ):
                validate_daemon_status(status, CHANNELS, 6001)


if __name__ == "__main__":
    unittest.main()
