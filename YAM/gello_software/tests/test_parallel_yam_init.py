import threading
import unittest
from unittest.mock import patch

from gello.robots.yam import YAMRobot
from gello.utils import launch_utils


class FakePreparedRobot:
    def __init__(self, channel, prepared_channels):
        self.channel = channel
        self.prepared_channels = prepared_channels
        self.control_started = False
        self.built = False
        self.rolled_back = False

    def start_control(self):
        if len(self.prepared_channels) != 2:
            raise AssertionError("control started before both sides prepared")
        self.control_started = True

    def build_robot(self):
        if not self.control_started:
            raise AssertionError("build before control start")
        self.built = True
        return f"hardware:{self.channel}"

    def rollback(self):
        self.rolled_back = True


class ParallelYAMInitializationTest(unittest.TestCase):
    def test_both_prepare_before_either_control_thread_starts(self):
        prepared_channels = set()
        preparations = {}
        lock = threading.Lock()

        def prepare(channel, **_kwargs):
            with lock:
                prepared_channels.add(channel)
                prepared = FakePreparedRobot(channel, prepared_channels)
                preparations[channel] = prepared
                return prepared

        with patch(
            "i2rt.robots.get_robot.prepare_yam_robot",
            side_effect=prepare,
        ):
            left, right = YAMRobot.create_pair(
                "can_follower_l", "can_follower_r"
            )

        self.assertEqual(left.robot, "hardware:can_follower_l")
        self.assertEqual(right.robot, "hardware:can_follower_r")
        self.assertTrue(preparations["can_follower_l"].control_started)
        self.assertTrue(preparations["can_follower_r"].control_started)

    def test_one_prepare_failure_rolls_back_the_other_side(self):
        left_prepared = FakePreparedRobot("can_follower_l", {"can_follower_l"})

        def prepare(channel, **_kwargs):
            if channel == "can_follower_r":
                raise RuntimeError("right motor 6 did not reply")
            return left_prepared

        with patch(
            "i2rt.robots.get_robot.prepare_yam_robot",
            side_effect=prepare,
        ):
            with self.assertRaisesRegex(RuntimeError, "right motor 6"):
                YAMRobot.create_pair("can_follower_l", "can_follower_r")

        self.assertTrue(left_prepared.rolled_back)
        self.assertFalse(left_prepared.control_started)

    def test_one_control_start_failure_rolls_back_both_sides(self):
        prepared_channels = {"can_follower_l", "can_follower_r"}
        preparations = {
            channel: FakePreparedRobot(channel, prepared_channels)
            for channel in prepared_channels
        }

        def prepare(channel, **_kwargs):
            prepared = preparations[channel]
            if channel == "can_follower_r":
                def fail_start():
                    raise RuntimeError("right control thread failed")

                prepared.start_control = fail_start
            return prepared

        with patch(
            "i2rt.robots.get_robot.prepare_yam_robot",
            side_effect=prepare,
        ):
            with self.assertRaisesRegex(RuntimeError, "right control thread"):
                YAMRobot.create_pair("can_follower_l", "can_follower_r")

        self.assertTrue(preparations["can_follower_l"].rolled_back)
        self.assertTrue(preparations["can_follower_r"].rolled_back)

    def test_launch_utils_selects_parallel_pair_for_two_yam_configs(self):
        left_cfg = {
            "_target_": "gello.robots.yam.YAMRobot",
            "channel": "can_follower_l",
        }
        right_cfg = {
            "_target_": "gello.robots.yam.YAMRobot",
            "channel": "can_follower_r",
        }

        with patch.object(
            YAMRobot,
            "create_pair",
            return_value=("left_robot", "right_robot"),
        ) as create_pair:
            robots = launch_utils.instantiate_robot_pair(left_cfg, right_cfg)

        self.assertEqual(robots, ("left_robot", "right_robot"))
        create_pair.assert_called_once_with("can_follower_l", "can_follower_r")


if __name__ == "__main__":
    unittest.main()
