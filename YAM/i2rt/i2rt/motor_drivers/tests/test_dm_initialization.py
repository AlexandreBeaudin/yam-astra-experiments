import unittest
from typing import Any
from unittest.mock import patch

from i2rt.motor_drivers import dm_driver


class FakeMotorInterface:
    instances: list["FakeMotorInterface"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.motor_on_calls: list[tuple[int, float | None, int | None]] = []
        self.motor_off_calls: list[int] = []
        self.closed = False
        self.__class__.instances.append(self)

    def _drain_bus(self, timeout_s: float = 0.05) -> int:
        return 0

    def motor_on(
        self,
        motor_id: int,
        motor_type: str,
        response_timeout_s: float | None = None,
        max_retry: int | None = None,
    ) -> object:
        self.motor_on_calls.append(
            (motor_id, response_timeout_s, max_retry)
        )
        if motor_id == 2:
            raise AssertionError("motor 2 did not reply")
        return object()

    def motor_off(self, motor_id: int) -> None:
        self.motor_off_calls.append(motor_id)

    def close(self) -> None:
        self.closed = True


class DMInitializationRollbackTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeMotorInterface.instances.clear()

    @patch.object(dm_driver, "DMSingleMotorCanInterface", FakeMotorInterface)
    def test_failure_disables_enabled_motors_and_closes_can(self) -> None:
        progress: list[tuple[int, int]] = []
        with self.assertRaisesRegex(AssertionError, "motor 2"):
            dm_driver.DMChainCanInterface(
                motor_list=[(1, "DM4310"), (2, "DM4310")],
                motor_offset=[0.0, 0.0],
                motor_direction=[1, 1],
                channel="can_test",
                start_thread=False,
                init_response_timeout_s=0.03,
                init_max_retries=10,
                init_progress_callback=lambda motor_id, total: progress.append(
                    (motor_id, total)
                ),
            )

        interface = FakeMotorInterface.instances[0]
        self.assertEqual(interface.motor_on_calls[0], (1, 0.03, 10))
        self.assertEqual(interface.motor_off_calls, [1])
        self.assertTrue(interface.closed)
        self.assertEqual(progress, [(1, 2), (2, 2)])


if __name__ == "__main__":
    unittest.main()
