"""Read-only verification of the Damiao motor command watchdog register."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any


EXPECTED_WATCHDOG_REGISTER = 8000
YAM_GRIPPER_MOTOR_ID = 7


class WatchdogVerificationError(RuntimeError):
    """Raised before motor enable when any watchdog is unsafe or unreadable."""


def verify_motor_watchdogs(
    channels: Mapping[str, str],
    *,
    motor_ids: Iterable[int] = range(1, 8),
    expected_value: int = EXPECTED_WATCHDOG_REGISTER,
    interface_factory: Callable[..., Any] | None = None,
    register_reader: Callable[[Any, int, str], Any] | None = None,
) -> dict[str, dict[int, int | None]]:
    """Require the persistent 400 ms watchdog on every follower motor.

    Register value ``8000`` is the i2rt/Damiao configuration for 400 ms. This
    function intentionally has no write path.
    """
    if interface_factory is None or register_reader is None:
        from i2rt.motor_config_tool.utils import (
            RawCanInterface,
            get_special_message_response,
        )

        interface_factory = interface_factory or RawCanInterface
        register_reader = register_reader or get_special_message_response

    readings: dict[str, dict[int, int | None]] = {}
    failures: list[str] = []
    ids = tuple(int(motor_id) for motor_id in motor_ids)
    for side, channel in channels.items():
        interface = interface_factory(
            channel=channel,
            bustype="socketcan",
            bitrate=1_000_000,
            name=f"watchdog_{side}",
        )
        side_readings: dict[int, int | None] = {}
        readings[side] = side_readings
        try:
            for motor_id in ids:
                try:
                    value = int(register_reader(interface, motor_id, "timeout"))
                except Exception as exc:
                    side_readings[motor_id] = None
                    failures.append(
                        f"{side.upper()} {channel} motor {motor_id}: unreadable ({exc})"
                    )
                    continue
                side_readings[motor_id] = value
                if value != expected_value:
                    failures.append(
                        f"{side.upper()} {channel} motor {motor_id}: {value} "
                        f"(expected {expected_value})"
                    )
        finally:
            interface.close()

    if failures:
        details = "\n".join(f"  - {failure}" for failure in failures)
        unreadable_grippers = []
        for side, channel in channels.items():
            side_readings = readings.get(side, {})
            if side_readings.get(YAM_GRIPPER_MOTOR_ID) is None:
                unreadable_grippers.append((side, channel))
        remediation = ""
        if unreadable_grippers:
            locations = ", ".join(
                f"{side.upper()} {channel}" for side, channel in unreadable_grippers
            )
            remediation = (
                "\nMotor 7 is the YAM gripper drive. It did not reply after the "
                f"built-in read retries on {locations}; check follower power, the "
                "joint-6-to-gripper CAN/power cable, and that the gripper is CAN ID 7. "
                "Do not bypass watchdog verification or enable the follower until it replies."
            )
        raise WatchdogVerificationError(
            "400 ms motor watchdog verification failed; followers were not enabled:\n"
            + details
            + remediation
        )
    return readings
