import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gello.robots.yam import YAMRobot
from i2rt.robots.utils import ArmType


class YAMProSelectionTest(unittest.TestCase):
    """Verify the hardware boundary without opening CAN or enabling motors."""

    def test_single_arm_passes_pro_to_factory(self):
        with patch("i2rt.robots.get_robot.get_yam_robot") as factory:
            YAMRobot(channel="test_left")
        factory.assert_called_once_with(channel="test_left", arm_type=ArmType.YAM_PRO, zero_gravity_mode=False)

    def _check_prepared_entry(self, entry):
        prepared = []

        def fake_prepare(channel, **kwargs):
            self.assertIs(kwargs["arm_type"], ArmType.YAM_PRO)
            self.assertIs(kwargs["zero_gravity_mode"], False)
            robot = SimpleNamespace(
                start_control=Mock(), build_robot=Mock(return_value=object()), rollback=Mock()
            )
            prepared.append(robot)
            return robot

        with patch("i2rt.robots.get_robot.prepare_yam_robot", side_effect=fake_prepare) as factory:
            entry()
        self.assertEqual(factory.call_count, 2)
        for robot in prepared:
            robot.start_control.assert_called_once_with()
            robot.build_robot.assert_called_once_with()
            robot.rollback.assert_not_called()

    def test_pair_passes_pro_for_both_followers(self):
        self._check_prepared_entry(lambda: YAMRobot.create_pair("test_left", "test_right"))

    def test_many_passes_pro_for_every_follower(self):
        self._check_prepared_entry(
            lambda: YAMRobot.create_many({"left": "test_left", "right": "test_right"})
        )


if __name__ == "__main__":
    unittest.main()
